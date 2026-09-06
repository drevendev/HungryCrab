"""The ``crab`` command line.

Every subcommand is deterministic and idempotent. Progress goes to stderr; results go to stdout
(``--json`` prints machine-readable output only).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from . import __version__, updater
from .cache import Slug, cache_root, prey_paths, resolve_target
from .compare import compare_for_maw, load_menu, meal_for, menu_candidates
from .compare.scoring import Scoring
from .digest import DigestOptions, DigestResult, run_digest
from .errors import CrabError, UsageError
from .fetch.catch import CatchOptions, catch, rmtree_force
from .fetch.github import GitHubClient
from .ledger import Ledger
from .licensing.detect import detect_in_repo
from .maw import MawConfig, write_default_config
from .miners import MINER_NAMES
from .nutrients import STATUSES, Candidate
from .serve import GhIssueClient, ServeOptions, ServeReport, serve
from .sniff import format_report, sniff
from .tune import analyse
from .tune import apply as apply_tuning

_MAW_MANIFESTS = ("package.json", "pyproject.toml", "Cargo.toml", "setup.cfg", "composer.json")


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _quiet(_: str) -> None:
    return None


def detect_maw_license(path: Path) -> str | None:
    """Best-effort license of a local (maw) repository, without a full digest."""
    if not path.is_dir():
        raise UsageError(f"maw path {path} is not a directory")
    manifests = [name for name in _MAW_MANIFESTS if (path / name).is_file()]
    findings = detect_in_repo(path, [], manifests=manifests, max_header_files=0)
    return findings.spdx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crab",
        description="Hungry Crab: eat a foreign repository and digest it without running its code.",
        epilog="Prey content is never executed and is always treated as untrusted data.",
    )
    parser.add_argument("--version", action="version", version=f"crab {__version__}")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="cache directory (default: ~/.cache/hungry-crab or $CRAB_CACHE_DIR)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="no progress output on stderr")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_sniff = sub.add_parser("sniff", help="API reconnaissance: license, size, languages, verdict")
    p_sniff.add_argument("repo", help="owner/repo or a GitHub URL")
    p_sniff.add_argument(
        "--maw",
        type=Path,
        default=None,
        help="local maw repository; compute the mode against its license",
    )
    p_sniff.add_argument(
        "--maw-license", default=None, help="maw license SPDX id (overrides --maw detection)"
    )
    p_sniff.add_argument("--json", action="store_true", help="print the report as JSON")
    p_sniff.add_argument(
        "--no-gh", action="store_true", help="use plain HTTPS instead of the gh CLI"
    )

    p_catch = sub.add_parser("catch", help="clone (or refresh) the prey into the cache")
    p_catch.add_argument("repo", help="owner/repo or a GitHub URL")
    p_catch.add_argument(
        "--shallow", action="store_true", help="default branch only, depth 1 (tree-only snapshot)"
    )
    p_catch.add_argument(
        "--since", default=None, help="history newer than 2y / 6m / 90d / an ISO date, all branches"
    )
    p_catch.add_argument("--force", action="store_true", help="delete the existing clone first")
    p_catch.add_argument(
        "--issues",
        type=int,
        default=0,
        help="also fetch up to N issues (plus the top by reactions)",
    )
    p_catch.add_argument("--json", action="store_true")

    p_digest = sub.add_parser(
        "digest", help="run the miners; write digest/ for owner/repo or a local path"
    )
    p_digest.add_argument(
        "target", help="owner/repo, a GitHub URL, or a local directory (e.g. . for the maw)"
    )
    p_digest.add_argument("--depth", choices=("normal", "deep"), default="normal")
    p_digest.add_argument(
        "--out", type=Path, default=None, help="write the digest here instead of the cache"
    )
    p_digest.add_argument(
        "--force", action="store_true", help="re-run even if this SHA is already digested"
    )
    p_digest.add_argument(
        "--miners", default=None, help=f"comma-separated subset of: {', '.join(MINER_NAMES)}"
    )
    p_digest.add_argument("--maw-license", default=None, help="maw license SPDX id for the verdict")
    p_digest.add_argument(
        "--maw", type=Path, default=None, help="local maw repository; detect its license"
    )
    p_digest.add_argument("--md-budget", type=int, default=None, help="token cap per Markdown file")
    p_digest.add_argument("--shallow", action="store_true", help="when catching first: --shallow")
    p_digest.add_argument("--since", default=None, help="when catching first: --since")
    p_digest.add_argument("--issues", type=int, default=0, help="when catching first: --issues N")
    p_digest.add_argument("--json", action="store_true", help="print manifest.json")

    p_compare = sub.add_parser(
        "compare", help="digest prey and maw, diff them and write gap.md and menu.md"
    )
    p_compare.add_argument("prey", help="owner/repo, a GitHub URL, or a local directory")
    p_compare.add_argument("--maw", type=Path, default=Path(), help="maw repository (default: .)")
    p_compare.add_argument(
        "--maw-license", default=None, help="maw license SPDX id (else detected)"
    )
    p_compare.add_argument("--depth", choices=("normal", "deep"), default="normal")
    p_compare.add_argument("--force", action="store_true", help="re-run both digests")
    p_compare.add_argument("--top", type=int, default=30, help="candidates shown in menu.md")
    p_compare.add_argument("--shallow", action="store_true", help="when catching first: --shallow")
    p_compare.add_argument("--since", default=None, help="when catching first: --since")
    p_compare.add_argument("--issues", type=int, default=0, help="when catching first: --issues N")
    p_compare.add_argument(
        "--no-issues", action="store_true", help="do not ask GitHub which nutrients were served"
    )
    p_compare.add_argument("--json", action="store_true", help="print menu.json")

    p_init = sub.add_parser("init", help="write a default .crab.yml into the maw repository")
    p_init.add_argument("--maw", type=Path, default=Path(), help="maw repository (default: .)")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing file")

    p_ledger = sub.add_parser("ledger", help="show or update the maw ledger")
    p_ledger.add_argument("--maw", type=Path, default=Path(), help="maw repository (default: .)")
    ledger_sub = p_ledger.add_subparsers(dest="ledger_command", metavar="<action>")
    p_show = ledger_sub.add_parser("show", help="print the ledger summary (default)")
    p_show.add_argument("--json", action="store_true")
    p_mark = ledger_sub.add_parser("mark", help="record a decision for a nutrient")
    p_mark.add_argument("id", help="nutrient id, e.g. crab:ci:ci.cache")
    p_mark.add_argument("status", choices=STATUSES)
    p_mark.add_argument("--reason", default="", help="why (kept for crab tune)")
    p_mark.add_argument("--url", default=None, help="issue or pull request URL")

    p_serve = sub.add_parser(
        "serve", help="turn approved nutrients into issues (dry-run by default)"
    )
    p_serve.add_argument("prey", help="owner/repo, a GitHub URL, or a local directory")
    p_serve.add_argument("--maw", type=Path, default=Path(), help="maw repository (default: .)")
    p_serve.add_argument("--ids", default=None, help="comma-separated nutrient ids from menu.md")
    p_serve.add_argument("--top", type=int, default=None, help="serve the top N instead of --ids")
    p_serve.add_argument(
        "--as", dest="mode", choices=("dry-run", "issue", "pr-branch"), default="dry-run"
    )
    p_serve.add_argument(
        "--notes", type=Path, default=None, help="JSON with why/how per id (model-written)"
    )
    p_serve.add_argument("--json", action="store_true")

    p_tune = sub.add_parser("tune", help="suggest scoring weight changes from the ledger")
    p_tune.add_argument("--maw", type=Path, default=Path(), help="maw repository (default: .)")
    p_tune.add_argument("--write", action="store_true", help="apply the suggestions to .crab.yml")
    p_tune.add_argument("--min-decisions", type=int, default=3)
    p_tune.add_argument("--json", action="store_true")

    p_menu = sub.add_parser("menu", help="print the ranked menu from the last compare")
    p_menu.add_argument("prey", help="owner/repo, a GitHub URL, or a local directory")
    p_menu.add_argument(
        "--maw", type=Path, default=Path(), help="maw repository whose meal to read (default: .)"
    )
    p_menu.add_argument("--top", type=int, default=30)
    p_menu.add_argument("--category", default=None, help="comma-separated categories to show")
    p_menu.add_argument("--all", action="store_true", help="also list hidden candidates")
    p_menu.add_argument("--json", action="store_true")

    p_update = sub.add_parser(
        "update", help="check the CLI and the agent plugins, and bring them up to date"
    )
    p_update.add_argument(
        "--run", action="store_true", help="run the updates instead of only printing them"
    )
    p_update.add_argument("--json", action="store_true")

    p_cache = sub.add_parser("cache", help="inspect or clean the cache")
    cache_sub = p_cache.add_subparsers(dest="cache_command", metavar="<action>")
    cache_sub.add_parser("path", help="print the cache directory")
    cache_sub.add_parser("ls", help="list cached prey")
    p_rm = cache_sub.add_parser("rm", help="remove one cached prey (clone, API data and digests)")
    p_rm.add_argument("repo")

    sub.add_parser("version", help="print the version")
    return parser


def _resolve_maw_license(maw: Path | None, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if maw is not None:
        return detect_maw_license(maw)
    return None


def cmd_sniff(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    slug = Slug.parse(args.repo)
    maw_license = _resolve_maw_license(args.maw, args.maw_license)
    client = GitHubClient(prefer_gh=not args.no_gh)
    report = sniff(slug, client=client, cache_root=args.cache_dir, maw_license=maw_license, log=log)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report))
    return 0


def cmd_catch(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    slug = Slug.parse(args.repo)
    options = CatchOptions(
        shallow=args.shallow, since=args.since, force=args.force, issues=args.issues
    )
    result = catch(slug, options, cache_root=args.cache_dir, log=log)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        state = "refreshed" if result.updated else "cloned"
        print(f"{state} {slug} -> {result.repo_dir}")
        print(
            f"HEAD {result.sha[:12]} on {result.default_branch}"
            + (" (shallow)" if result.shallow else "")
        )
    return 0


def print_digest_summary(result: DigestResult) -> None:
    manifest = result.manifest
    prey = manifest["prey"]
    state = "cached" if result.cached else "written"
    print(
        f"Digest of {prey['label']}@{prey['sha'][:7]} ({manifest['depth']}) "
        f"{state} at {result.out_dir}"
    )
    print(f"{'file':<20}{'tokens':>8}{'bytes':>10}  miner")
    for entry in manifest["files"]:
        miner = entry["miner"] or ""
        print(f"{entry['name']:<20}{entry['tokens_est']:>8}{entry['bytes']:>10}  {miner}")
    budget = manifest["budget"]
    status = "OVER" if manifest["over_budget"] else "ok"
    print(
        f"markdown tokens: {manifest['markdown_tokens_est']} of {budget['markdown_total']} "
        f"({status}); all files: {manifest['total_tokens_est']}"
    )
    ok = sum(1 for m in manifest["miners"] if m["ok"])
    failed = [m for m in manifest["miners"] if not m["ok"]]
    print(f"miners: {ok} ok, {len(failed)} failed; {manifest['elapsed_seconds']} s")
    for miner in failed:
        print(f"  FAILED {miner['name']}: {miner['error']}")
    for warning in manifest["warnings"]:
        print(f"  warning: {warning}")


def cmd_digest(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    target = resolve_target(args.target)
    maw_license = _resolve_maw_license(args.maw, args.maw_license)
    miners = [m.strip() for m in args.miners.split(",") if m.strip()] if args.miners else None
    options = DigestOptions(
        depth=args.depth,
        out=args.out,
        force=args.force,
        miners=miners,
        maw_license=maw_license,
        md_budget=args.md_budget,
        cache_root=args.cache_dir,
        catch_options=CatchOptions(shallow=args.shallow, since=args.since, issues=args.issues),
    )
    result = run_digest(target, options, log=log)
    if args.json:
        print(json.dumps(result.manifest, indent=2, ensure_ascii=False))
    else:
        print_digest_summary(result)
    return 0


def print_menu(
    menu: dict[str, Any], cards: list[Candidate], *, top: int, show_hidden: bool
) -> None:
    prey = menu.get("prey", {})
    maw = menu.get("maw", {})
    counts = menu.get("counts", {})
    verdict = menu.get("verdict", {})
    print(
        f"Menu: {prey.get('label')}@{str(prey.get('sha', ''))[:7]} for {maw.get('label')} "
        f"({counts.get('total', len(cards))} candidates, {counts.get('hidden', 0)} hidden, "
        f"default mode {verdict.get('mode', '?')})"
    )
    print(
        f"{'#':>3} {'score':>5}  {'category':<15}{'nutrient':<52}{'mode':<12}{'eff':<4}{'art':<6}id"
    )
    for index, card in enumerate(cards[:top], start=1):
        title = card.title if len(card.title) <= 50 else card.title[:47] + "..."
        print(
            f"{index:>3} {card.score:>5.2f}  {card.category:<15}{title:<52}{card.license_mode:<12}"
            f"{card.effort:<4}{card.serve_as:<6}{card.id}"
        )
    if show_hidden:
        for item in menu.get("hidden", []):
            print(f"    hidden {item.get('id')}: {item.get('reason')}")


def _maw_dir(value: Path) -> Path:
    maw = Path(value).resolve()
    if not maw.is_dir():
        raise UsageError(f"maw path {maw} is not a directory")
    return maw


def cmd_compare(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    prey = resolve_target(args.prey)
    maw = _maw_dir(args.maw)
    digest_options = DigestOptions(
        depth=args.depth,
        force=args.force,
        maw_license=args.maw_license,
        cache_root=args.cache_dir,
        catch_options=CatchOptions(shallow=args.shallow, since=args.since, issues=args.issues),
    )
    lookup = None
    if not args.no_issues and shutil.which("gh"):
        lookup = GhIssueClient().list_marked
    result, _, _, _ = compare_for_maw(
        prey, maw, digest_options=digest_options, top=args.top, issue_lookup=lookup, log=log
    )
    if args.json:
        print(json.dumps(result.menu, indent=2, ensure_ascii=False))
        return 0
    print_menu(result.menu, result.candidates, top=min(args.top, 15), show_hidden=False)
    print(f"meal written to {result.meal_dir}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = write_default_config(_maw_dir(args.maw), force=args.force)
    print(f"wrote {path}")
    return 0


def print_ledger(ledger: Ledger, config: MawConfig) -> None:
    stats = ledger.stats()
    location = str(ledger.path) if ledger.path else "not persisted (ledger: none)"
    print(
        f"Ledger for {ledger.maw or config.root.name}: {stats['entries']} nutrients, "
        f"{stats['meals']} meals, {location}"
    )
    by_status = ", ".join(f"{k} {v}" for k, v in sorted(stats["by_status"].items()))
    print(f"By status: {by_status or 'nothing yet'}")
    entries = sorted(ledger.entries.values(), key=lambda e: (e.status, -e.score, e.id))
    if entries:
        print(f"{'status':<9}{'score':>5}  {'prey':<24}{'id':<48}reason")
        for entry in entries[:60]:
            print(
                f"{entry.status:<9}{entry.score:>5.2f}  {entry.prey[:23]:<24}{entry.id[:47]:<48}"
                f"{entry.reason[:40]}"
            )
        if len(entries) > 60:
            print(f"... {len(entries) - 60} more (use --json)")


def cmd_ledger(args: argparse.Namespace) -> int:
    maw = _maw_dir(args.maw)
    config = MawConfig.load(maw)
    ledger = Ledger.load(config.ledger_path(args.cache_dir), maw=maw.name)
    if args.ledger_command == "mark":
        entry = ledger.mark(args.id, args.status, reason=args.reason, url=args.url)
        saved = ledger.save()
        print(f"{entry.id}: {entry.status}" + (f" ({entry.reason})" if entry.reason else ""))
        print(f"ledger saved to {saved}" if saved else "ledger mode is none; nothing persisted")
        return 0
    if getattr(args, "json", False):
        print(json.dumps(ledger.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print_ledger(ledger, config)
    return 0


def print_serve_report(report: ServeReport) -> None:
    if report.mode == "dry-run":
        for preview in report.previews:
            print("=" * 72)
            print(f"[{preview['id']}] {preview['title']}")
            print("-" * 72)
            print(preview["body"])
        print("=" * 72)
        print(f"dry run: {len(report.previews)} issue(s) would be created")
    for item in report.served:
        print(f"created {item['url']}  {item['id']}")
    for item in report.skipped:
        print(f"skipped {item['id']}: {item['reason']}")
    if report.mode == "issue" and report.ledger_path:
        print(f"ledger updated: {report.ledger_path} (commit it when the ledger mode is repo)")


def cmd_serve(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    prey = resolve_target(args.prey)
    maw = _maw_dir(args.maw)
    config = MawConfig.load(maw)
    ledger = Ledger.load(config.ledger_path(args.cache_dir), maw=maw.name)
    meal_dir = meal_for(prey, maw, DigestOptions(cache_root=args.cache_dir))
    ids = [item.strip() for item in args.ids.split(",") if item.strip()] if args.ids else []
    options = ServeOptions(ids=ids, top=args.top, mode=args.mode, notes=args.notes)
    client = GhIssueClient() if (args.mode == "issue" or shutil.which("gh")) else None
    report = serve(meal_dir, maw, options, config=config, ledger=ledger, client=client, log=log)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print_serve_report(report)
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    maw = _maw_dir(args.maw)
    config = MawConfig.load(maw)
    ledger = Ledger.load(config.ledger_path(args.cache_dir), maw=maw.name)
    scoring = Scoring.default().merged(config.scoring)
    report = analyse(ledger, scoring, min_decisions=args.min_decisions)
    written = None
    if args.write and any(s.kind in ("category", "trait") for s in report.suggestions):
        apply_tuning(report, config)
        written = config.path
    if args.json:
        payload = report.to_dict()
        payload["written"] = str(written) if written else None
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(report.to_markdown(), end="")
    if written:
        print(f"scoring overrides written to {written}")
    return 0


def cmd_menu(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    prey = resolve_target(args.prey)
    meal_dir = meal_for(prey, _maw_dir(args.maw), DigestOptions(cache_root=args.cache_dir))
    menu = load_menu(meal_dir)
    if menu is None:
        raise CrabError(
            f"no menu for {prey.label} yet",
            hint=f"run: crab compare {args.prey} --maw <path to the maw repository>",
        )
    cards = menu_candidates(menu)
    if args.category:
        wanted = {c.strip() for c in args.category.split(",") if c.strip()}
        cards = [card for card in cards if card.category in wanted]
    if args.json:
        payload = dict(menu)
        payload["candidates"] = [card.to_dict() for card in cards[: args.top]]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    log(f"menu from {meal_dir / 'menu.json'}")
    print_menu(menu, cards, top=args.top, show_hidden=args.all)
    return 0


def cmd_update(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    log("checking the CLI and the agent plugins against master")
    report = updater.check()
    if args.run:
        updater.apply(report, log=log)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(updater.format_report(report))
    return 0


def cmd_cache(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    root = args.cache_dir or cache_root()
    if args.cache_command == "path" or args.cache_command is None:
        print(root)
        return 0
    if args.cache_command == "ls":
        github = root / "github"
        found = False
        if github.is_dir():
            for owner in sorted(p for p in github.iterdir() if p.is_dir()):
                for repo in sorted(p for p in owner.iterdir() if p.is_dir()):
                    found = True
                    digests = repo / "digests"
                    count = (
                        len([d for d in digests.iterdir() if d.is_dir()]) if digests.is_dir() else 0
                    )
                    clone = "clone" if (repo / "repo" / ".git").exists() else "no clone"
                    print(f"{owner.name}/{repo.name}: {clone}, {count} digest(s)")
        if not found:
            print("cache is empty")
        return 0
    if args.cache_command == "rm":
        slug = Slug.parse(args.repo)
        paths = prey_paths(slug, root)
        if not paths.root.exists():
            print(f"{slug} is not cached")
            return 0
        rmtree_force(paths.root)
        print(f"removed {paths.root}")
        return 0
    parser.error("unknown cache action")


def _configure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    log = _quiet if args.quiet else _stderr
    try:
        if args.command == "version":
            print(f"crab {__version__}")
            return 0
        if args.command == "sniff":
            return cmd_sniff(args, log)
        if args.command == "catch":
            return cmd_catch(args, log)
        if args.command == "digest":
            return cmd_digest(args, log)
        if args.command == "compare":
            return cmd_compare(args, log)
        if args.command == "menu":
            return cmd_menu(args, log)
        if args.command == "init":
            return cmd_init(args)
        if args.command == "ledger":
            return cmd_ledger(args)
        if args.command == "serve":
            return cmd_serve(args, log)
        if args.command == "tune":
            return cmd_tune(args)
        if args.command == "update":
            return cmd_update(args, log)
        if args.command == "cache":
            return cmd_cache(args, parser)
    except CrabError as exc:
        _stderr(f"crab: error: {exc.message}")
        if exc.hint:
            _stderr(f"crab: hint: {exc.hint}")
        return exc.exit_code
    except KeyboardInterrupt:
        _stderr("crab: interrupted")
        return 130
    parser.error(f"unknown command {args.command}")


def git_available() -> bool:
    return shutil.which("git") is not None
