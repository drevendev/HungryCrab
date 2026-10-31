"""Acquire only the three owned receiving snapshots for the frozen qualification setup."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from qualification_acquire import VOLUME_BYTES, export_objects, write_json

from hungry_crab.cache import Slug
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.fetch.github import GitHubClient


def selected_pin(repo: Path, plan: dict[str, Any], default_sha: str) -> tuple[str, str | None]:
    """Qualify a declared owned branch target without checking it out or running it."""
    if default_sha != plan.get("sha"):
        raise ValueError("receiving default changed before sealing; refresh its context")
    branch, pin = plan.get("target_ref"), plan.get("target_sha")
    if branch is None and pin is None:
        return default_sha, None
    if (
        not isinstance(branch, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch) is None
        or not isinstance(pin, str)
        or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", pin) is None
    ):
        raise ValueError("receiving target requires an explicit owned branch and full commit SHA")
    git = GitRunner(repo, lazy_fetch=False)
    git.run("check-ref-format", "--branch", branch)
    reference = f"refs/remotes/origin/{branch}"
    head = git.run("--no-replace-objects", "rev-parse", "--verify", reference + "^{commit}").strip()
    actual = git.run("--no-replace-objects", "rev-parse", "--verify", pin + "^{commit}").strip()
    if actual != pin:
        raise ValueError("receiving target is not a canonical available commit")
    git.run("--no-replace-objects", "merge-base", "--is-ancestor", pin, head)
    return pin, branch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--volume", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=False)
    cache = args.volume / "receiving-cache"
    receipts = []
    for name in ("HungryCrab", "Devostasis", "BeautifulRepo"):
        planned = plan["receiving"][name]
        slug = Slug("drevendev", name)
        metadata = GitHubClient(retries=0).repo(slug)
        if metadata.get("private") is not False:
            raise ValueError("receiving setup exports public owner repositories only")
        result = catch(
            slug,
            CatchOptions(
                shallow=planned.get("target_ref") is None,
                wiki=False,
                issues=0,
                acquisition_volume=args.volume,
                max_download_bytes=314_572_800,
                max_disk_bytes=VOLUME_BYTES,
            ),
            cache_root=cache,
        )
        pin, branch = selected_pin(Path(result.repo_dir), planned, result.sha)
        output = args.output / name
        output.mkdir()
        amount = export_objects(Path(result.repo_dir), output / "objects", slug, 128 * 1024 * 1024)
        receipt = {
            "repository": str(slug),
            "sha": pin,
            "default_sha": result.sha,
            "selected_target_ref": branch,
            "object_bytes": amount,
            "catch": result.to_dict(),
            "model_trials_started": 0,
            "receiving_code_executed": False,
        }
        receipts.append(receipt)
        write_json(output / "receipt.json", receipt)
        print(
            json.dumps({"repository": str(slug), "sha": pin, "object_bytes": amount}),
            flush=True,
        )
    write_json(args.output / "receiving.json", receipts)


if __name__ == "__main__":
    main()
