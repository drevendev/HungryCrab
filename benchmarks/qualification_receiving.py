"""Acquire only the three owned receiving snapshots for the frozen qualification setup."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from qualification_acquire import VOLUME_BYTES, export_objects, write_json

from hungry_crab.cache import Slug
from hungry_crab.fetch.acquisition import AcquisitionBudget
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.fetch.github import GitHubClient


def target_plan(plan: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    branch, pull, pin = (
        plan.get("target_ref"),
        plan.get("target_pull_request"),
        plan.get("target_sha"),
    )
    if branch is None and pull is None and pin is None:
        return None, None, None
    valid_branch = (
        isinstance(branch, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch) is not None
        and pull is None
    )
    valid_pull = type(pull) is int and 1 <= pull <= 10_000_000 and branch is None
    if (
        not (valid_branch or valid_pull)
        or not isinstance(pin, str)
        or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", pin) is None
    ):
        raise ValueError(
            "receiving target requires an explicit owned branch or PR and full commit SHA"
        )
    return branch, pull, pin


def selected_pin(repo: Path, plan: dict[str, Any], default_sha: str) -> tuple[str, str | None]:
    """Qualify a declared owned branch target without checking it out or running it."""
    if default_sha != plan.get("sha"):
        raise ValueError("receiving default changed before sealing; refresh its context")
    branch, pull, pin = target_plan(plan)
    if pin is None:
        return default_sha, None
    git = GitRunner(repo, lazy_fetch=False)
    if branch is not None:
        git.run("check-ref-format", "--branch", branch)
    reference = f"refs/remotes/origin/{branch}" if branch else f"refs/crab/qualification/pr-{pull}"
    head = git.run("--no-replace-objects", "rev-parse", "--verify", reference + "^{commit}").strip()
    actual = git.run("--no-replace-objects", "rev-parse", "--verify", pin + "^{commit}").strip()
    if actual != pin:
        raise ValueError("receiving target is not a canonical available commit")
    if pull is not None and head != pin:
        raise ValueError("receiving PR head changed before sealing; refresh its context")
    git.run("--no-replace-objects", "merge-base", "--is-ancestor", pin, head)
    return pin, branch if branch else f"refs/pull/{pull}/head"


def pending_pins(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the complete bounded provider snapshot before any fetch."""
    pending = plan.get("pending", [])
    if not isinstance(pending, list) or len(pending) > 50:
        raise ValueError("receiving pending collection exceeds its bounded scope")
    seen: set[int] = set()
    for row in pending:
        if (
            not isinstance(row, dict)
            or set(row) != {"number", "sha"}
            or type(row["number"]) is not int
            or not 1 <= row["number"] <= 10_000_000
            or row["number"] in seen
            or not isinstance(row["sha"], str)
            or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", row["sha"]) is None
        ):
            raise ValueError(
                "pending work requires distinct canonical PR numbers and immutable heads"
            )
        seen.add(row["number"])
    return pending


def bind_integration_head(plan: dict[str, Any], name: str, head: str | None) -> dict[str, Any]:
    """Bind the self-referencing PR to an explicitly supplied immutable CI head.

    A committed plan cannot contain its own future commit hash. This opt-in is
    restricted to the owned #539 qualification; canonical fetch/selected_pin
    still verifies exact equality before exporting or sealing any evidence.
    """
    if not plan.get("integration_head"):
        return plan
    if (
        plan["integration_head"] is not True
        or name != "HungryCrab"
        or plan.get("target_pull_request") != 539
        or not isinstance(head, str)
        or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", head) is None
    ):
        raise ValueError("integration binding requires the explicit owned immutable workflow head")
    return {
        **plan,
        "target_sha": head,
        "pending": [
            {**r, "sha": head if r.get("number") == 539 else r.get("sha")}
            for r in plan.get("pending", [])
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--volume", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--integration-head", help="explicit owned #539 workflow commit SHA")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=False)
    cache = args.volume / "receiving-cache"
    receipts = []
    for name in ("HungryCrab", "Devostasis", "BeautifulRepo"):
        planned = bind_integration_head(plan["receiving"][name], name, args.integration_head)
        target_branch, target_pull, _ = target_plan(planned)
        pending = pending_pins(planned)
        slug = Slug("drevendev", name)
        metadata = GitHubClient(retries=0).repo(slug)
        if metadata.get("private") is not False:
            raise ValueError("receiving setup exports public owner repositories only")
        result = catch(
            slug,
            CatchOptions(
                shallow=target_branch is None,
                wiki=False,
                issues=0,
                acquisition_volume=args.volume,
                max_download_bytes=314_572_800,
                max_disk_bytes=VOLUME_BYTES,
            ),
            cache_root=cache,
        )
        numbers = sorted({r["number"] for r in pending} | ({target_pull} if target_pull else set()))
        if numbers:
            # Canonical receiving pull refs include confirmed fork contributions.
            # Fetch only data under the same hard volume and transport bounds.
            with AcquisitionBudget(args.volume, cache, 314_572_800, VOLUME_BYTES).scope():
                GitRunner(Path(result.repo_dir), lazy_fetch=False).run(
                    "fetch",
                    "--depth=1",
                    "--no-tags",
                    slug.url,
                    *[
                        f"refs/pull/{number}/head:refs/crab/qualification/pr-{number}"
                        for number in numbers
                    ],
                )
        git = GitRunner(Path(result.repo_dir), lazy_fetch=False)
        for row in pending:
            actual = git.run(
                "--no-replace-objects",
                "rev-parse",
                "--verify",
                f"refs/crab/qualification/pr-{row['number']}^{{commit}}",
            ).strip()
            if actual != row["sha"]:
                raise ValueError(
                    "confirmed pending head changed before sealing; refresh its context"
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
            "pending": pending,
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
