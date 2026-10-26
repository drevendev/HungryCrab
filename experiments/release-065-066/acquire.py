"""Acquire only frozen public inputs on the verified noexec quota filesystem."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.fetch.acquisition import AcquisitionBudget
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.git import GitRunner


def main() -> None:
    spec = json.loads(Path(__file__).with_name("spec.json").read_text())
    volume = Path(os.environ["CRAB_VOLUME"])
    cache = volume / "inputs"
    output = volume / "experiment-output"
    output.mkdir()
    (output / "spec.json").write_text(json.dumps(spec, indent=2))
    records = []
    for source in [*spec["maws"], *spec["sources"]]:
        started = time.perf_counter()
        slug = Slug.parse(source["repo"])
        budget = AcquisitionBudget(
            volume,
            cache,
            spec["budget"]["download_bytes_per_repo"],
            spec["budget"]["acquisition_disk_bytes"],
        )
        with budget.scope():
            catch(slug, CatchOptions(shallow=True, wiki=False), cache_root=cache)
            paths = prey_paths(slug, cache)
            git = GitRunner(paths.repo, timeout=300)
            observed = git.head_sha()
            if observed != source["sha"]:
                git.run("fetch", "--depth=1", "origin", source["sha"])
                git.run("reset", "--hard", "--quiet", source["sha"])
                record = json.loads(paths.catch_file.read_text())
                record.update(sha=git.head_sha(), experimental_pin=source["sha"])
                paths.catch_file.write_text(json.dumps(record))
            if git.head_sha() != source["sha"]:
                raise ValueError("Source pin differs: " + source["repo"])
            blob = git.run("rev-parse", source["sha"] + ":" + source["license_path"]).strip()
            if blob != source["license_blob"]:
                raise ValueError("Licence blob differs: " + source["repo"])
        records.append(
            {
                "repo": source["repo"],
                "sha": source["sha"],
                "initial_head": observed,
                "wall_seconds": time.perf_counter() - started,
                "git_download_bytes": budget.downloaded,
                "capacity_bytes": budget.capacity,
            }
        )
        (output / "acquisition.json").write_text(json.dumps(records, indent=2))
        print(json.dumps(records[-1]), flush=True)
    ledger_hashes = {}
    for maw in spec["maws"]:
        repo = prey_paths(Slug.parse(maw["repo"]), cache).repo
        ledger = repo / ".crab/ledger.json"
        ledger_hashes[maw["id"]] = (
            hashlib.sha256(ledger.read_bytes()).hexdigest() if ledger.exists() else None
        )
    (output / "input-ledgers.json").write_text(json.dumps(ledger_hashes, indent=2))
    (output / "acquired-at.json").write_text(json.dumps({"utc": datetime.now(UTC).isoformat()}))


def export() -> None:
    """Export public results and inert Git objects, never runnable donor checkouts."""
    volume = Path(os.environ["CRAB_VOLUME"])
    spec = json.loads(Path(__file__).with_name("spec.json").read_text())
    output = volume / "experiment-output"
    objects = output / "objects"
    objects.mkdir(exist_ok=True)
    for source in [*spec["maws"], *spec["sources"]]:
        repo = prey_paths(Slug.parse(source["repo"]), volume / "inputs").repo
        if not (repo / ".git").is_dir():
            continue
        if GitRunner(repo).run("status", "--porcelain").strip():
            raise ValueError("Analysis changed a source checkout")
        dest = objects / source["repo"].replace("/", "--") / ".git"
        shutil.copytree(
            repo / ".git", dest, symlinks=True, ignore=shutil.ignore_patterns("hooks", "logs")
        )
    exported = Path(os.environ["RUNNER_TEMP"]) / "release-comparison-export"
    exported.mkdir(exist_ok=True)
    entries = [p for p in output.rglob("*") if p.is_file() and not p.is_symlink()]
    total = sum(p.stat().st_size for p in entries)
    if total > 1024**3 or len(entries) > 150000:
        raise ValueError("Export exceeds the fixed import envelope")
    with tarfile.open(exported / "results.tar.gz", "w:gz", dereference=False) as archive:
        archive.add(output, arcname="results")
    (exported / "summary.json").write_text(
        json.dumps({"regular_bytes": total, "files": len(entries)})
    )


if __name__ == "__main__":
    import sys

    export() if len(sys.argv) > 1 and sys.argv[1] == "export" else main()
