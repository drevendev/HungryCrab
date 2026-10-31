"""Prepare shared public facts on a verified CI volume; never launch a model or donor program."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hungry_crab.analysis_context import installed_source_identity
from hungry_crab.cache import Slug, prey_paths
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.fetch.github import GitHubClient
from hungry_crab.fs import is_link_stat
from hungry_crab.sniff import sniff

MAX_EXPORT = 512 * 1024 * 1024
MAX_REPO_EXPORT = 64 * 1024 * 1024
VOLUME_BYTES = 2 * 1024 * 1024 * 1024


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_regular_tree(source: Path, destination: Path, maximum: int) -> int:
    """Copy only regular entries with a finite total; never follow source links."""
    if maximum < 0 or is_link_stat(source.lstat()):
        raise ValueError("invalid bounded export source or allowance")
    destination.mkdir(parents=True, exist_ok=False)
    used, count = 0, 0
    for parent, directories, filenames in os.walk(source, followlinks=False):
        parent_path = Path(parent)
        for name in [*directories, *filenames]:
            mode = (parent_path / name).lstat().st_mode
            if is_link_stat((parent_path / name).lstat()) or not (
                stat.S_ISDIR(mode) or stat.S_ISREG(mode)
            ):
                raise ValueError("export refuses linked or special source entries")
        for name in filenames:
            count += 1
            if count > 50_000:
                raise ValueError("export entry budget exhausted")
            original = parent_path / name
            relative = original.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with original.open("rb") as incoming, target.open("xb") as outgoing:
                while block := incoming.read(min(65_536, maximum - used + 1)):
                    if len(block) > maximum - used:
                        raise ValueError("export byte budget exhausted")
                    outgoing.write(block)
                    used += len(block)
    return used


def export_objects(repo: Path, destination: Path, slug: Slug, maximum: int) -> int:
    git_dir = repo / ".git"
    if not git_dir.is_dir() or (git_dir / "objects/info/alternates").exists():
        raise ValueError("source objects must be self-contained")
    destination.mkdir()
    used = copy_regular_tree(git_dir / "objects", destination / "objects", maximum)
    used += copy_regular_tree(git_dir / "refs", destination / "refs", maximum - used)
    for name in ("HEAD", "packed-refs", "shallow"):
        source = git_dir / name
        if source.exists():
            if not stat.S_ISREG(source.lstat().st_mode):
                raise ValueError("Git administration entry is not regular")
            allowance = min(1_048_576, maximum - used)
            with source.open("rb") as handle:
                data = handle.read(allowance + 1)
            if len(data) > allowance:
                raise ValueError("Git administration byte budget exhausted")
            (destination / name).write_bytes(data)
            used += len(data)
    # No donor/global configuration, hooks or credentials enter the object reader.
    (destination / "config").write_text(
        f'[core]\nrepositoryformatversion = 0\nbare = true\n[remote "origin"]\nurl = {slug.url}\n',
        encoding="utf-8",
    )
    return used


def digest_with_arm(slug: Slug, output: Path, arm: Path, cache: Path, log: Path) -> None:
    environment = {**os.environ, "PYTHONPATH": str(arm / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    with log.open("w", encoding="utf-8") as handle:
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
            env=environment,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=True,
        )


def acquire(args: argparse.Namespace) -> None:
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    rows = [row for row in plan["pool"] if row["batch"] == args.batch]
    if len(rows) != 10 or not args.volume.is_mount() or args.volume == Path(args.volume.anchor):
        raise ValueError("qualification requires ten nominations and a dedicated mounted volume")
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "preflight.json", plan)
    cache = args.volume / "qualification-cache"
    receipts: list[dict[str, Any]] = []
    exported = 0
    try:
        for index, row in enumerate(rows, 1):
            slug = Slug.parse(row["repository"])
            if slug.host != "github.com":
                raise ValueError("this qualification preparation supports public GitHub only")
            target = args.output / f"{index:02}"
            target.mkdir()
            receipt: dict[str, Any] = {
                "repository": str(slug),
                "nominated_sha": row["sha"],
                "status": "not-acquired",
                "model_trials_started": 0,
            }
            try:
                metadata = GitHubClient(retries=0).repo(slug)
                if metadata.get("private") is not False:
                    raise ValueError("public visibility was not established before Git acquisition")
                sniff(slug, cache_root=cache)
                result = catch(
                    slug,
                    CatchOptions(
                        shallow=True,
                        wiki=False,
                        issues=0,
                        acquisition_volume=args.volume,
                        max_download_bytes=314_572_800,
                        max_disk_bytes=VOLUME_BYTES,
                    ),
                    cache_root=cache,
                )
                repo = Path(result.repo_dir)
                if GitRunner(repo).head_sha() != result.sha:
                    raise ValueError("acquired commit identity is inconsistent")
                receipt.update(
                    sha=result.sha,
                    pin_changed=result.sha != row["sha"],
                    catch=result.to_dict(),
                    provider_id=metadata.get("id"),
                )
                objects_bytes = export_objects(
                    repo, target / "objects", slug, min(MAX_REPO_EXPORT, MAX_EXPORT - exported)
                )
                exported += objects_bytes
                receipt["object_bytes"] = objects_bytes
                for name, arm in (("baseline", args.baseline), ("candidate", args.candidate)):
                    staging = args.volume / f"{args.batch}-{index}-{name}-digest"
                    digest_with_arm(slug, staging, arm, cache, target / f"{name}.log")
                    amount = copy_regular_tree(
                        staging, target / f"{name}-digest", MAX_EXPORT - exported
                    )
                    exported += amount
                    if staging.resolve().parent != args.volume.resolve() or staging.is_symlink():
                        raise ValueError("refusing redirected owned digest cleanup")
                    shutil.rmtree(staging)
                receipt.update(
                    status="shared-facts-acquired; not a measured arm",
                    objects="objects",
                    source_coverage="complete-acquired-shallow-objects; bounded miners",
                    issues="not-acquired",
                    history="shallow",
                    donor_executed=False,
                )
            except Exception as exc:
                receipt.update(status="held", reason=f"{type(exc).__name__}: {str(exc)[:600]}")
            finally:
                write_json(target / "receipt.json", receipt)
                receipts.append(receipt)
                print(
                    json.dumps(
                        {
                            "batch": args.batch,
                            "index": index,
                            "repository": str(slug),
                            "status": receipt["status"],
                        }
                    ),
                    flush=True,
                )
                write_json(args.output / "acquisitions.json", receipts)
                # Only CLI-owned cache entries are removed; artifacts retain actual holds.
                from hungry_crab.fetch.catch import rmtree_force

                paths = prey_paths(slug, cache)
                if paths.root.exists():
                    rmtree_force(paths.root, cache_root=cache)
        write_json(
            args.output / "setup.json",
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "tool": installed_source_identity(),
                "batch": args.batch,
                "model_trials_started": 0,
                "model_tokens": None,
                "currency": None,
                "shared_setup_only": True,
                "export_bytes": exported,
                "model_hypotheses_or_findings": None,
                "receipts": receipts,
            },
        )
    finally:
        # The workflow's existing owned-volume provisioner handles detach and cleanup.
        print("Acquisition preparation ended; no model, donor test, build or installer ran.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--batch", choices=("crab", "devostasis", "beautifulrepo"), required=True)
    parser.add_argument("--volume", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    acquire(parser.parse_args())


if __name__ == "__main__":
    main()
