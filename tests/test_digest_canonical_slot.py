"""Regression guards for canonical digest-slot isolation."""

from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW
from helpers import copy_repo

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.miners import MINER_NAMES


def test_selective_digest_does_not_replace_canonical_full_entry(
    npm_app: Path, tmp_path: Path
) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    target = Target(path=work)
    base = DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache")

    full = run_digest(target, base)
    assert not full.cached
    assert {record["name"] for record in full.manifest["miners"]} == set(MINER_NAMES)

    selective = run_digest(
        target,
        DigestOptions(
            now=FIXED_NOW,
            cache_root=tmp_path / "cache",
            miners=["license"],
        ),
    )
    assert not selective.cached
    assert selective.out_dir != full.out_dir
    assert ".scratch" in selective.out_dir.parts
    assert {record["name"] for record in selective.manifest["miners"]} < set(MINER_NAMES)

    again = run_digest(
        target,
        DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache"),
    )
    assert again.cached, "selective diagnostics must not invalidate the canonical full digest"
    assert again.out_dir == full.out_dir
    assert {record["name"] for record in again.manifest["miners"]} == set(MINER_NAMES)
