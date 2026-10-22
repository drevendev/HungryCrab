"""Regression tests for conservative cache removal; no external prey is run."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from hungry_crab.errors import CrabError
from hungry_crab.fetch.catch import rmtree_force


def test_cache_removal_never_chmods_external_symlink_target(tmp_path: Path) -> None:
    external = tmp_path / "outside"
    external.mkdir()
    sentinel = external / "human-notes.txt"
    sentinel.write_bytes(b"never touch this file")
    original_mode = stat.S_IMODE(sentinel.stat().st_mode)
    sentinel.chmod(stat.S_IREAD)
    locked_mode = stat.S_IMODE(sentinel.stat().st_mode)

    cache = tmp_path / "cache-owned"
    cache.mkdir()
    (cache / "normal.txt").write_text("disposable")
    link = cache / "outside-link"
    try:
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symlinks are unavailable here")
        rmtree_force(cache)
        assert not cache.exists()
        assert sentinel.read_bytes() == b"never touch this file"
        assert stat.S_IMODE(sentinel.stat().st_mode) == locked_mode
    finally:
        sentinel.chmod(original_mode)


def test_refuse_removal_when_the_cache_path_is_a_link(tmp_path: Path) -> None:
    external = tmp_path / "real-human-directory"
    external.mkdir()
    (external / "important.txt").write_text("human", encoding="utf-8")
    link = tmp_path / "fake-cache"
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable here")
    with pytest.raises(CrabError, match="link/reparse"):
        rmtree_force(link)
    assert (external / "important.txt").read_text(encoding="utf-8") == "human"


def test_locked_cache_failure_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache-owned"
    cache.mkdir()
    sentinel = cache / "locked.txt"
    sentinel.write_text("keep until lock clears", encoding="utf-8")

    def deny_removal(_path: Path) -> None:
        raise PermissionError("simulated open file")

    monkeypatch.setattr("hungry_crab.fetch.catch.shutil.rmtree", deny_removal)
    with pytest.raises(CrabError, match="cannot safely remove cached files") as error:
        rmtree_force(cache)
    assert error.value.hint is not None and "Close programs" in error.value.hint
    assert sentinel.read_text(encoding="utf-8") == "keep until lock clears"
