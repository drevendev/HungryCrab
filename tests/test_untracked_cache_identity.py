"""Regression coverage for untracked worktree cache identity (#65)."""

from __future__ import annotations

import os
from pathlib import Path

from conftest import FIXED_NOW
from helpers import copy_repo

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest, worktree_fingerprint
from hungry_crab.fetch.git import GitRunner


def test_untracked_bytes_are_not_reused_when_metadata_is_restored(
    npm_app: Path, tmp_path: Path
) -> None:
    """Same name/size/mtime must not stand in for the bytes miners actually read."""
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    scratch = work / "scratch.py"
    scratch.write_bytes(b"alpha\n")
    original = scratch.stat()
    options = DigestOptions(
        out=tmp_path / "out",
        now=FIXED_NOW,
        cache_root=tmp_path / "cache",
    )

    first = run_digest(Target(path=work), options)
    assert not first.cached
    assert worktree_fingerprint(GitRunner(work), work) == "unknown"

    scratch.write_bytes(b"omega\n")
    os.utime(scratch, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert scratch.stat().st_size == original.st_size
    assert scratch.stat().st_mtime_ns == original.st_mtime_ns

    changed = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not changed.cached, "untracked bytes changed even though their metadata did not"
