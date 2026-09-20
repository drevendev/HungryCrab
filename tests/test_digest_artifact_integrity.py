"""A healthy manifest must not bless missing or corrupt producer evidence (#87)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import copy_repo

from hungry_crab.cache import Target
from hungry_crab.compare import CompareOptions, compare_digests
from hungry_crab.digest import DigestOptions, DigestResult, run_digest
from hungry_crab.digest_integrity import digest_integrity_errors
from hungry_crab.errors import CrabError


def _options(tmp_path: Path) -> DigestOptions:
    return DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache")


def _damage(path: Path, mode: str) -> None:
    if mode == "missing":
        path.unlink()
    elif mode == "corrupt":
        path.write_text("{", encoding="utf-8")
    elif mode == "wrong-size":
        path.write_text("{}\n", encoding="utf-8")
    else:  # pragma: no cover - only test parameters call this helper
        raise AssertionError(mode)


def test_complete_digest_remains_reusable(npm_app: Path, tmp_path: Path) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    options = _options(tmp_path)

    first = run_digest(Target(path=work), options)
    assert not first.cached
    assert digest_integrity_errors(first.out_dir, first.manifest) == []

    again = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert again.cached


@pytest.mark.parametrize("mode", ["missing", "corrupt", "wrong-size"])
def test_damaged_successful_producer_artifact_forces_cache_miss_and_repairs(
    npm_app: Path, tmp_path: Path, mode: str
) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    options = _options(tmp_path)
    first = run_digest(Target(path=work), options)
    deps = first.out_dir / "deps.json"
    _damage(deps, mode)

    errors = digest_integrity_errors(first.out_dir, first.manifest)
    assert any("deps.json" in error for error in errors)

    again = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not again.cached, "damaged evidence must never be reused as a healthy cache hit"
    assert isinstance(json.loads(deps.read_text(encoding="utf-8")), dict)
    assert digest_integrity_errors(again.out_dir, again.manifest) == []


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_compare_refuses_inconsistent_successful_producer_artifact_by_default(
    npm_digest: DigestResult, py_digest: DigestResult, tmp_path: Path, mode: str
) -> None:
    prey = copy_repo(npm_digest.out_dir, tmp_path / "prey")
    _damage(prey / "deps.json", mode)

    with pytest.raises(CrabError, match="inconsistent producer artifact"):
        compare_digests(prey, py_digest.out_dir)

    allowed = compare_digests(
        prey,
        py_digest.out_dir,
        options=CompareOptions(allow_partial=True),
    )
    assert allowed.menu["prey"]["label"]
