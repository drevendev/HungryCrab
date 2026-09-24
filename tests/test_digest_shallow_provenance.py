"""Regression coverage for canonical digest history provenance (#145)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXED_NOW

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.digest_location import resolve_canonical_digest
from hungry_crab.fetch.git import GitRunner


def _options(tmp_path: Path) -> DigestOptions:
    return DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache")


def test_shallow_history_stays_scratch_until_full_history_is_available(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = Target(path=npm_app)
    monkeypatch.setattr(GitRunner, "is_shallow", lambda self: True)

    shallow = run_digest(target, _options(tmp_path))
    sha = shallow.manifest["prey"]["sha"]
    digests_dir = shallow.out_dir.parents[2]

    assert shallow.manifest["prey"]["shallow"] is True
    assert shallow.out_dir.is_relative_to(digests_dir / ".scratch" / sha)
    assert not (digests_dir / ".refs" / f"{sha}.json").exists()

    monkeypatch.setattr(GitRunner, "is_shallow", lambda self: False)
    full = run_digest(target, _options(tmp_path))

    assert not full.cached
    assert full.manifest["prey"]["shallow"] is False
    assert resolve_canonical_digest(digests_dir, sha).path == full.out_dir

    again = run_digest(target, _options(tmp_path))
    assert again.cached
    assert again.out_dir == full.out_dir


def test_unknown_shallow_probe_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = GitRunner(tmp_path)

    def unavailable(*args: str, timeout: float | None = None) -> str | None:
        return None

    monkeypatch.setattr(runner, "try_run", unavailable)
    assert runner.is_shallow() is True
