"""Acquire exact public pins on the existing quota backend, without donor execution."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from qualification_acquire import MAX_EXPORT, MAX_REPO_EXPORT, copy_regular_tree, export_objects

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.fetch.acquisition import AcquisitionBudget
from hungry_crab.fetch.git import GitRunner
from hungry_crab.fetch.github import GitHubClient

BASELINE = "915779769747908a14b1a0f7fdecdd79d16dce43"


def records(value: object, batch: str) -> list[dict[str, Any]]:
    """Accept only neutral locator metadata, never investigator forecasts or code."""
    if not isinstance(value, dict) or set(value) != {"schema", "pool"} or value["schema"] != 1:
        raise ValueError("expected the neutral pinned acquisition schema")
    pool = value["pool"]
    if not isinstance(pool, list) or not 1 <= len(pool) <= 30:
        raise ValueError("acquisition pool must contain 1..30 metadata records")
    seen: set[str] = set()
    seen_ids: set[str] = set()
    selected = []
    for row in pool:
        if not isinstance(row, dict) or set(row) != {
            "id",
            "batch",
            "repository",
            "provider_id",
            "sha",
            "tree",
        }:
            raise ValueError("acquisition accepts locator metadata only")
        for key in ("id", "batch"):
            if not isinstance(row[key], str) or not re.fullmatch(r"[a-z0-9-]{1,60}", row[key]):
                raise ValueError("invalid acquisition identity")
        if row["id"] in seen_ids:
            raise ValueError("acquisition locator identities must be unique")
        seen_ids.add(row["id"])
        slug = Slug.parse(row["repository"])
        # Confirmed receiving PRs are revisions of one receiver, not new donor
        # nominations. Every other batch retains canonical repository uniqueness.
        source_key = str(slug).casefold()
        if row["batch"] == "pending":
            source_key += "@" + str(row["sha"])
        if slug.host != "github.com" or source_key in seen:
            raise ValueError("acquisition requires distinct public GitHub repositories")
        seen.add(source_key)
        if type(row["provider_id"]) is not int or row["provider_id"] <= 0:
            raise ValueError("positive immutable provider identity required")
        if any(
            not isinstance(row[key], str) or not re.fullmatch(r"[a-f0-9]{40}", row[key])
            for key in ("sha", "tree")
        ):
            raise ValueError("complete commit and tree pins required")
        if row["batch"] == batch:
            selected.append(row)
    if not 1 <= len(selected) <= (6 if batch == "control" else 10):
        raise ValueError("invalid per-batch nomination count")
    return selected


def fetch_pin(repo: Path, row: dict[str, Any]) -> None:
    """Fetch a commit directly; never follow moving default heads or donor hooks."""
    git = GitRunner(repo, lazy_fetch=False)
    git.run("init", "--quiet", "--template=")
    git.run("config", "core.hooksPath", os.devnull)
    git.run("config", "remote.origin.url", Slug.parse(row["repository"]).url + ".git")
    git.run("fetch", "--no-tags", "--depth=1", "--no-recurse-submodules", "origin", row["sha"])
    if git.run("rev-parse", "FETCH_HEAD^{commit}").strip() != row["sha"]:
        raise ValueError("acquired commit differs from the nomination")
    if git.run("rev-parse", row["sha"] + "^{tree}").strip() != row["tree"]:
        raise ValueError("acquired tree differs from the nomination")
    # The repository/config/template are ours. No donor/global filter or hook is installed.
    git.run("reset", "--hard", row["sha"], stdout_limit=4096)
    if git.head_sha() != row["sha"]:
        raise ValueError("pinned worktree identity changed")


def digest(slug: Slug, output: Path, arm: Path, cache: Path, log: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(arm / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    with log.open("wb") as stream:
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "hungry_crab",
                "--cache-dir",
                str(cache),
                "digest",
                str(slug),
                "--out",
                str(output),
                "--depth",
                "normal",
                "--fail-on-miner-error",
            ],
            cwd=arm,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=240,
            check=True,
        )


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-env", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--volume", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    payload = os.environ[args.catalog_env]
    if len(payload.encode()) > 16000:
        raise ValueError("neutral catalog exceeds the transport allowance")
    rows = records(json.loads(payload), args.batch)
    if GitRunner(args.baseline).head_sha() != BASELINE:
        raise ValueError("published 0.6.8 baseline required")
    args.output.mkdir(parents=True, exist_ok=False)
    receipts: list[dict[str, Any]] = []
    exported = 0
    for row in rows:
        target = args.output / row["id"]
        target.mkdir()
        owned = args.volume / ("pinned-" + row["id"])
        owned.mkdir(exist_ok=False)
        receipt = {**row, "status": "not-acquired", "donor_executed": False}
        try:
            budget = AcquisitionBudget(args.volume, owned, 314572800, 2147483648)
            metadata = GitHubClient(retries=0).repo(Slug.parse(row["repository"]))
            if metadata.get("private") is not False or metadata.get("id") != row["provider_id"]:
                raise ValueError("public provider identity changed")
            cache = owned / "cache"
            paths = prey_paths(Slug.parse(row["repository"]), cache)
            repo = paths.repo
            repo.mkdir(parents=True)
            paths.api.mkdir()
            write(paths.api / "repo.json", metadata)
            with budget.scope():
                fetch_pin(repo, row)
            receipt["downloaded_tls_bytes"] = budget.downloaded
            exported += export_objects(
                repo,
                target / "objects",
                Slug.parse(row["repository"]),
                min(MAX_REPO_EXPORT, MAX_EXPORT - exported),
            )
            for name, arm in (("baseline", args.baseline), ("candidate", args.candidate)):
                staging = owned / (name + "-digest")
                digest(Slug.parse(row["repository"]), staging, arm, cache, target / (name + ".log"))
                exported += copy_regular_tree(
                    staging, target / (name + "-digest"), MAX_EXPORT - exported
                )
            receipt.update(
                status="shared-facts-acquired",
                exported_bytes_cumulative=exported,
                scope="exact shallow commit; no wiki or issue/history sample",
            )
        except Exception as error:
            receipt.update(status="held", error=type(error).__name__ + ": " + str(error)[:1000])
        finally:
            write(target / "receipt.json", receipt)
            receipts.append(receipt)
            write(args.output / "acquisitions.json", receipts)
            print(json.dumps({"id": row["id"], "status": receipt["status"]}), flush=True)
            resolved = owned.resolve()
            if resolved.parent != args.volume.resolve() or owned.is_symlink():
                raise ValueError("refusing redirected owned cleanup")
            shutil.rmtree(resolved)
    if any(row["status"] != "shared-facts-acquired" for row in receipts):
        raise SystemExit("Acquisition holds retained; do not treat partial artifacts as complete")


if __name__ == "__main__":
    main()
