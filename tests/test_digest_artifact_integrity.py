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


@pytest.mark.parametrize("damage", ["oversized", "wrong-size", "missing-size"])
def test_integrity_refuses_unbounded_json_before_opening_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    from hungry_crab import digest_integrity

    raw = b'{"value":"' + b"x" * 50 + b'"}'
    (tmp_path / "deps.json").write_bytes(raw)
    entry = {"name": "deps.json", "miner": "deps", "bytes": len(raw)}
    if damage == "wrong-size":
        entry["bytes"] = 1
    elif damage == "missing-size":
        entry.pop("bytes")
    manifest = {"files": [entry], "miners": [{"name": "deps", "ok": True, "files": ["deps.json"]}]}
    monkeypatch.setattr(digest_integrity, "MAX_JSON_BYTES", 32)
    monkeypatch.setattr(
        digest_integrity, "read_object", lambda _: pytest.fail("untrusted JSON was opened")
    )
    errors = digest_integrity_errors(tmp_path, manifest)
    assert errors and "deps.json" in errors[0]


def test_deep_producer_json_is_an_integrity_error_not_a_traceback(tmp_path: Path) -> None:
    raw = b'{"nested":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    (tmp_path / "deps.json").write_bytes(raw)
    manifest = {
        "files": [{"name": "deps.json", "miner": "deps", "bytes": len(raw)}],
        "miners": [{"name": "deps", "ok": True, "files": ["deps.json"]}],
    }
    assert digest_integrity_errors(tmp_path, manifest) == ["deps: invalid producer JSON: deps.json"]


@pytest.mark.parametrize("extra", [False, True])
def test_artifact_json_depth_has_a_platform_independent_boundary(
    tmp_path: Path, extra: bool
) -> None:
    from hungry_crab.artifact_read import MAX_JSON_DEPTH, read_object

    depth = MAX_JSON_DEPTH - 1 + int(extra)
    path = tmp_path / "deps.json"
    path.write_bytes(b'{"value":' + b"[" * depth + b"0" + b"]" * depth + b"}")
    if extra:
        with pytest.raises(CrabError, match="invalid JSON"):
            read_object(path)
    else:
        assert "value" in read_object(path)


def test_json_string_brackets_and_escaped_quotes_do_not_count_as_nesting(tmp_path: Path) -> None:
    from hungry_crab.artifact_read import read_object

    data = {"text": '"[]{}\\' * 2000}
    path = tmp_path / "deps.json"
    path.write_text(json.dumps(data), encoding="utf-8-sig")
    assert read_object(path) == data
