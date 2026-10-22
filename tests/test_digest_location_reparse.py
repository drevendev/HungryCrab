"""Regression coverage for Windows reparse/junction acceptance in #211."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hungry_crab.digest_location import (
    REF_SCHEMA,
    _require_real_directory,
    resolve_canonical_digest,
)
from hungry_crab.errors import CrabError


def _pretend_reparse(monkeypatch: pytest.MonkeyPatch, suspect: Path) -> None:
    """Exercise Windows lstat metadata on CI hosts without a real junction."""
    real_lstat = Path.lstat

    def wrapped_lstat(path: Path, *args: object, **kwargs: object) -> object:
        record = real_lstat(path, *args, **kwargs)
        if path == suspect:
            return SimpleNamespace(st_mode=record.st_mode, st_file_attributes=0x400)
        return record

    monkeypatch.setattr(Path, "lstat", wrapped_lstat)


def test_real_directory_accepted(tmp_path: Path) -> None:
    actual = tmp_path / "refs"
    actual.mkdir()
    _require_real_directory(actual, label="digest refs root")


def test_real_symlink_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(CrabError, match="expected a real directory"):
        _require_real_directory(link, label="digest refs root")


def test_windows_reparse_directory_refused_on_every_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs = tmp_path / ".refs"
    refs.mkdir()
    _pretend_reparse(monkeypatch, refs)
    with pytest.raises(CrabError, match="expected a real directory"):
        _require_real_directory(refs, label="digest refs root")


def test_windows_reparse_ref_file_refused_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "a" * 40
    refs = tmp_path / ".refs"
    refs.mkdir()
    ref_file = refs / f"{sha}.json"
    ref_file.write_text(json.dumps({"schema": REF_SCHEMA, "generation": "g1"}))
    (tmp_path / ".generations" / sha / "g1").mkdir(parents=True)
    _pretend_reparse(monkeypatch, ref_file)
    with pytest.raises(CrabError, match="expected a regular file"):
        resolve_canonical_digest(tmp_path, sha)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction check")
def test_native_windows_junction_refused(tmp_path: Path) -> None:
    import subprocess

    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"Windows junction creation unexpectedly failed: {completed.stderr[:300]}"
    )
    with pytest.raises(CrabError, match="expected a real directory"):
        _require_real_directory(link, label="digest refs root")
