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

from . import __version__
from .cache import Slug, cache_root, prey_paths, resolve_target
from .compare import CompareOptions, load_menu, menu_candidates, run_compare
from .digest import DigestOptions, DigestResult, locate_digest, run_digest
from .errors import CrabError, UsageError
from .fetch.catch import CatchOptions, catch, rmtree_force
from .fetch.github import GitHubClient
from .licensing.detect import detect_in_repo
from .miners import MINER_NAMES
from .nutrients import Candidate
from .sniff import format_report, sniff

_HOST_MANIFESTS = ("package.json", "pyproject.toml", "Cargo.toml", "setup.cfg", "composer.json")


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _quiet(_: str) -> None:
    return None


def detect_host_license(path: Path) -> str | None:
    """Best-effort license of a local (host) repository, without a full digest."""
    if not path.is_dir():
        raise UsageError(f"host path {path} is not a directory")
    manifests = [name for name in _HOST_MANIFESTS if (path / name).is_file()]
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
        "--host",
        type=Path,
        default=None,
        help="local host repository; compute the mode against its license",
    )
    p_sniff.add_argument(
        "--host-license", default=None, help="host license SPDX id (overrides --host detection)"
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
    p_catch.add_argument("--json", action="store_true")

    p_digest = sub.add_parser(
        "digest", help="run the miners; write digest/ for owner/repo or a local path"
    )
    p_digest.add_argument(
        "target", help="owner/repo, a GitHub URL, or a local directory (e.g. . for the host)"
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
    p_digest.add_argument(
        "--host-license", default=None, help="host license SPDX id for the verdict"
    )
    p_digest.add_argument(
        "--host", type=Path, default=None, help="local host repository; detect its license"
    )
    p_digest.add_argument("--md-budget", type=int, default=None, help="token cap per Markdown file")
    p_digest.add_argument("--shallow", action="store_true", help="when catching first: --shallow")
    p_digest.add_argument("--since", default=None, help="when catching first: --since")
    p_digest.add_argument("--json", action="store_true", help="print manifest.json")

    p_compare = sub.add_parser(
        "compare", help="digest prey and host, diff them and write gap.md and menu.md"
    )
    p_compare.add_argument("prey", help="owner/repo, a GitHub URL, or a local directory")
    p_compare.add_argument("--host", type=Path, default=Path(), help="host repository (default: .)")
    p_compare.add_argument(
        "--host-license", default=None, help="host license SPDX id (else detected)"
    )
    p_compare.add_argument("--depth", choices=("normal", "deep"), default="normal")
    p_compare.add_argument("--force", action="store_true", help="re-run both digests")
    p_compare.add_argument("--top", type=int, default=30, help="candidates shown in menu.md")
    p_compare.add_argument("--shallow", action="store_true", help="when catching first: --shallow")
    p_compare.add_argument("--since", default=None, help="when catching first: --since")
    p_compare.add_argument("--json", action="store_true", help="print menu.json")

    p_menu = sub.add_parser("menu", help="print the ranked menu from the last compare")
    p_menu.add_argument("prey", help="owner/repo, a GitHub URL, or a local directory")
    p_menu.add_argument("--top", type=int, default=30)
    p_menu.add_argument("--category", default=None, help="comma-separated categories to show")
    p_menu.add_argument("--all", action="store_true", help="also list hidden candidates")
    p_menu.add_argument("--json", action="store_true")

    p_cache = sub.add_parser("cache", help="inspect or clean the cache")
    cache_sub = p_cache.add_subparsers(dest="cache_command", metavar="<action>")
    cache_sub.add_parser("path", help="print the cache directory")
    cache_sub.add_parser("ls", help="list cached prey")
    p_rm = cache_sub.add_parser("rm", help="remove one cached prey (clone, API data and digests)")
    p_rm.add_argument("repo")

    sub.add_parser("version", help="print the version")
    return parser


def _resolve_host_license(host: Path | None, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if host is not None:
        return detect_host_license(host)
    return None


def cmd_sniff(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    slug = Slug.parse(args.repo)
    host_license = _resolve_host_license(args.host, args.host_license)
    client = GitHubClient(prefer_gh=not args.no_gh)
    report = sniff(
        slug, client=client, cache_root=args.cache_dir, host_license=host_license, log=log
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report))
    return 0


def cmd_catch(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    slug = Slug.parse(args.repo)
    options = CatchOptions(shallow=args.shallow, since=args.since, force=args.force)
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
    host_license = _resolve_host_license(args.host, args.host_license)
    miners = [m.strip() for m in args.miners.split(",") if m.strip()] if args.miners else None
    options = DigestOptions(
        depth=args.depth,
        out=args.out,
        force=args.force,
        miners=miners,
        host_license=host_license,
        md_budget=args.md_budget,
        cache_root=args.cache_dir,
        catch_options=CatchOptions(shallow=args.shallow, since=args.since),
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
    host = menu.get("host", {})
    counts = menu.get("counts", {})
    verdict = menu.get("verdict", {})
    print(
        f"Menu: {prey.get('label')}@{str(prey.get('sha', ''))[:7]} for {host.get('label')} "
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
            f"{card.effort:<4}{card.artifact:<6}{card.id}"
        )
    if show_hidden:
        for item in menu.get("hidden", []):
            print(f"    hidden {item.get('id')}: {item.get('reason')}")


def cmd_compare(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    prey = resolve_target(args.prey)
    host = Path(args.host).resolve()
    if not host.is_dir():
        raise UsageError(f"host path {host} is not a directory")
    host_license = _resolve_host_license(host, args.host_license)
    digest_options = DigestOptions(
        depth=args.depth,
        force=args.force,
        host_license=host_license,
        cache_root=args.cache_dir,
        catch_options=CatchOptions(shallow=args.shallow, since=args.since),
    )
    options = CompareOptions(top=args.top, host_license=host_license)
    result, prey_digest, _ = run_compare(
        prey, host, digest_options=digest_options, options=options, log=log
    )
    if args.json:
        print(json.dumps(result.menu, indent=2, ensure_ascii=False))
        return 0
    print_menu(result.menu, result.candidates, top=min(args.top, 15), show_hidden=False)
    print(f"gap.md and menu.md written to {prey_digest.out_dir}")
    return 0


def cmd_menu(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    prey = resolve_target(args.prey)
    prey_dir = locate_digest(prey, DigestOptions(cache_root=args.cache_dir))
    menu = load_menu(prey_dir)
    if menu is None:
        raise CrabError(
            f"no menu for {prey.label} yet",
            hint=f"run: crab compare {args.prey} --host <path to the host repository>",
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
    log(f"menu from {prey_dir / 'menu.json'}")
    print_menu(menu, cards, top=args.top, show_hidden=args.all)
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
