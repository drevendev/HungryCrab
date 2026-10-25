"""Acquire one explicitly pinned public MIT reference on a Linux quota filesystem."""
from __future__ import annotations

import json
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import hungry_crab
from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.digest_integrity import digest_integrity_errors
from hungry_crab.fetch.acquisition import AcquisitionBudget
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.licensing.detect import detect_from_text


def main() -> None:
    if hungry_crab.__version__ != "0.6.4":
        raise ValueError("This one-off validation requires released hungry-crab 0.6.4")
    spec = json.loads(os.environ["PUBLIC_DONOR"])
    volume = Path(os.environ["CRAB_VOLUME"]).resolve(strict=True)
    cache = volume / "cache"
    export = Path(os.environ["RUNNER_TEMP"]) / "public-mit-snapshot"
    export.mkdir()
    record = {"source": spec, "started_at": datetime.now(UTC).isoformat(), "status": "RUNNING"}
    try:
        budget = AcquisitionBudget(volume, cache, 250 * 1024**2, 1024**3)
        with budget.scope():
            slug = Slug.parse(spec["repo"])
            catch(slug, CatchOptions(shallow=True, wiki=False), cache_root=cache, log=print)
            paths = prey_paths(slug, cache)
            git = GitRunner(paths.repo, timeout=180)
            observed = git.head_sha()
            if observed != spec["sha"]:
                git.run("fetch", "--depth=1", "origin", spec["sha"])
                git.run("reset", "--hard", "--quiet", spec["sha"])
                state = json.loads(paths.catch_file.read_text(encoding="utf-8"))
                state.update(sha=git.head_sha(), experimental_pin=spec["sha"], initially_caught_sha=observed)
                paths.catch_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            if git.head_sha() != spec["sha"]:
                raise ValueError("Pinned source commit was not established")
            blob = git.run("rev-parse", spec["sha"] + ":" + spec["license_path"]).strip()
            if blob != spec["license_blob"]:
                raise ValueError("Pinned licence blob differs")
            text = git.run("show", spec["sha"] + ":" + spec["license_path"])
            if detect_from_text(text)[0] != "MIT":
                raise ValueError("Standard MIT grant not established")
            result = run_digest(Target(slug=slug), DigestOptions(cache_root=cache), log=print)
            errors = digest_integrity_errors(result.out_dir, result.manifest)
            if errors or any(not entry["ok"] for entry in result.manifest["miners"]):
                raise ValueError("Incomplete digest: " + repr(errors))
            if result.manifest["prey"]["sha"] != spec["sha"]:
                raise ValueError("Digest commit differs from the qualified pin")
            record.update(status="ACQUIRED_AND_DIGESTED", cli=hungry_crab.__version__,
                          downloaded_bytes=budget.downloaded, disk_capacity=budget.capacity,
                          digest=str(result.out_dir.relative_to(cache)), license="MIT")
        total = 0
        count = 0
        for directory, _, names in os.walk(cache, followlinks=False):
            for name in names:
                source = Path(directory) / name
                count += 1
                if not source.is_symlink():
                    total += source.stat().st_size
        if total > 768 * 1024**2 or count > 100000:
            raise ValueError("Snapshot exceeds the explicit import envelope")
        record.update(export_regular_bytes=total, export_file_count=count)
        with tarfile.open(export / "cache.tar.gz", "w:gz", dereference=False) as archive:
            archive.add(cache, arcname="cache", recursive=True)
    except Exception as exc:
        record.update(status="BLOCKED", error_type=type(exc).__name__, error=str(exc)[:2000])
        raise
    finally:
        record["finished_at"] = datetime.now(UTC).isoformat()
        (export / "summary.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
