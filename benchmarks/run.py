"""Digest benchmark: time and token budget for public repositories.

    uv run python benchmarks/run.py pallets/click colinhacks/zod [--depth deep]

Clones (or refreshes) each prey first so that network time stays out of the measurement, then
digests it with ``--force`` and records seconds, token estimates and whether the milestone limits
held (120 seconds, 30,000 Markdown tokens). Results go to ``benchmarks/results/<date>.json``;
every version of the crab adds entries here because the Evolving Crab stands on them.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from hungry_crab import __version__
from hungry_crab.cache import resolve_target
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.fetch.catch import CatchOptions, catch

SECONDS_LIMIT = 120.0
MARKDOWN_TOKENS_LIMIT = 30_000
SCHEMA = "hungry-crab.benchmark/1"


def _quiet(_: str) -> None:
    return None


def measure(repo: str, *, depth: str, no_catch: bool) -> dict[str, object]:
    target = resolve_target(repo)
    if target.slug is not None and not no_catch:
        catch(target.slug, CatchOptions(), log=_quiet)
    started = time.perf_counter()
    result = run_digest(target, DigestOptions(depth=depth, force=True), log=_quiet)
    elapsed = time.perf_counter() - started
    manifest = result.manifest
    summary = manifest.get("summary", {})
    md_tokens = int(manifest["markdown_tokens_est"])
    return {
        "repo": repo,
        "sha": manifest["prey"]["sha"],
        "seconds": round(elapsed, 2),
        "markdown_tokens": md_tokens,
        "total_tokens": manifest["total_tokens_est"],
        "files": summary.get("files"),
        "loc": summary.get("loc"),
        "commits": summary.get("commits"),
        "miners_ok": sum(1 for m in manifest["miners"] if m["ok"]),
        "miners_failed": sum(1 for m in manifest["miners"] if not m["ok"]),
        "within_limits": elapsed <= SECONDS_LIMIT and md_tokens <= MARKDOWN_TOKENS_LIMIT,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repos", nargs="+", help="owner/repo or local paths")
    parser.add_argument("--depth", choices=("normal", "deep"), default="normal")
    parser.add_argument("--no-catch", action="store_true", help="do not clone or refresh first")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "results",
        help="directory for the JSON result (default: benchmarks/results)",
    )
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    results = [measure(repo, depth=args.depth, no_catch=args.no_catch) for repo in args.repos]
    payload = {
        "schema": SCHEMA,
        "crab_version": __version__,
        "date": now.isoformat(timespec="seconds"),
        "depth": args.depth,
        "platform": {
            "os": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "limits": {"seconds": SECONDS_LIMIT, "markdown_tokens": MARKDOWN_TOKENS_LIMIT},
        "results": results,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / f"{now.date().isoformat()}.json"
    out_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{'repo':<24}{'seconds':>8}{'md tokens':>11}{'all tokens':>12}{'commits':>9}  ok")
    for row in results:
        ok = "yes" if row["within_limits"] else "NO"
        print(
            f"{row['repo']:<24}{row['seconds']:>8}{row['markdown_tokens']:>11}"
            f"{row['total_tokens']:>12}{row['commits'] or 0:>9}  {ok}"
        )
    print(f"written {out_file}")
    return 0 if all(row["within_limits"] for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
