"""Integration guards for canonical digest generation publication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXED_NOW

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.digest_location import REF_SCHEMA, resolve_canonical_digest
from hungry_crab.errors import CrabError


def _canonical_options(tmp_path: Path, *, force: bool = False) -> DigestOptions:
    return DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache", force=force)


def _digests_dir(result_path: Path) -> Path:
    assert result_path.parent.parent.name == ".generations"
    return result_path.parents[2]


def _ref_path(digests_dir: Path, sha: str) -> Path:
    return digests_dir / ".refs" / f"{sha}.json"


def _symlink_directory(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable on this platform: {exc}")


def test_canonical_digest_publishes_generation_and_reuses_active_snapshot(
    npm_app: Path, tmp_path: Path
) -> None:
    target = Target(path=npm_app)
    first = run_digest(target, _canonical_options(tmp_path))
    sha = first.manifest["prey"]["sha"]
    digests_dir = _digests_dir(first.out_dir)

    assert not first.cached
    assert resolve_canonical_digest(digests_dir, sha).path == first.out_dir

    again = run_digest(target, _canonical_options(tmp_path))
    assert again.cached
    assert again.out_dir == first.out_dir


def test_force_publishes_fresh_generation_without_mutating_previous_snapshot(
    npm_app: Path, tmp_path: Path
) -> None:
    target = Target(path=npm_app)
    first = run_digest(target, _canonical_options(tmp_path))
    sha = first.manifest["prey"]["sha"]
    digests_dir = _digests_dir(first.out_dir)
    manifest_before = first.manifest_path.read_bytes()

    forced = run_digest(target, _canonical_options(tmp_path, force=True))

    assert not forced.cached
    assert forced.out_dir != first.out_dir
    assert first.manifest_path.read_bytes() == manifest_before
    assert resolve_canonical_digest(digests_dir, sha).path == forced.out_dir
    assert first.manifest_path.is_file(), "an already-resolved reader must retain its snapshot"


def test_normal_lookup_fails_closed_on_malformed_ref_but_force_repairs_it(
    npm_app: Path, tmp_path: Path
) -> None:
    target = Target(path=npm_app)
    first = run_digest(target, _canonical_options(tmp_path))
    sha = first.manifest["prey"]["sha"]
    digests_dir = _digests_dir(first.out_dir)
    _ref_path(digests_dir, sha).write_text("not json\n", encoding="utf-8", newline="\n")

    with pytest.raises(CrabError, match="cannot read digest ref"):
        run_digest(target, _canonical_options(tmp_path))

    repaired = run_digest(target, _canonical_options(tmp_path, force=True))
    assert repaired.out_dir != first.out_dir
    assert resolve_canonical_digest(digests_dir, sha).path == repaired.out_dir


def test_force_repairs_ref_to_missing_generation(npm_app: Path, tmp_path: Path) -> None:
    target = Target(path=npm_app)
    first = run_digest(target, _canonical_options(tmp_path))
    sha = first.manifest["prey"]["sha"]
    digests_dir = _digests_dir(first.out_dir)
    _ref_path(digests_dir, sha).write_text(
        json.dumps({"schema": REF_SCHEMA, "generation": "missing-generation"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    repaired = run_digest(target, _canonical_options(tmp_path, force=True))
    assert repaired.out_dir != first.out_dir
    assert resolve_canonical_digest(digests_dir, sha).path == repaired.out_dir


def test_failed_miner_rebuild_leaves_previous_generation_active(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = Target(path=npm_app)
    first = run_digest(target, _canonical_options(tmp_path))
    sha = first.manifest["prey"]["sha"]
    digests_dir = _digests_dir(first.out_dir)
    generations_dir = digests_dir / ".generations" / sha
    before_generations = set(generations_dir.iterdir())
    manifest_before = first.manifest_path.read_bytes()

    def fail_inventory(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected miner failure")

    monkeypatch.setattr("hungry_crab.miners.inventory.InventoryMiner.run", fail_inventory)
    with pytest.raises(CrabError, match="cannot publish incomplete digest generation"):
        run_digest(target, _canonical_options(tmp_path, force=True))

    after_generations = set(generations_dir.iterdir())
    failed_generations = after_generations - before_generations
    assert len(failed_generations) == 1
    failed_generation = failed_generations.pop()
    failed_manifest = json.loads((failed_generation / "manifest.json").read_text(encoding="utf-8"))
    inventory = next(record for record in failed_manifest["miners"] if record["name"] == "inventory")
    assert inventory["status"] == "failed"
    assert resolve_canonical_digest(digests_dir, sha).path == first.out_dir
    assert first.manifest_path.read_bytes() == manifest_before


def test_force_does_not_write_through_unsafe_refs_root(npm_app: Path, tmp_path: Path) -> None:
    target = Target(path=npm_app)
    first = run_digest(target, _canonical_options(tmp_path))
    sha = first.manifest["prey"]["sha"]
    digests_dir = _digests_dir(first.out_dir)
    refs_dir = digests_dir / ".refs"
    _ref_path(digests_dir, sha).unlink()
    refs_dir.rmdir()
    outside = tmp_path / "outside-refs"
    outside.mkdir()
    _symlink_directory(outside, refs_dir)

    with pytest.raises(CrabError, match="digest refs root"):
        run_digest(target, _canonical_options(tmp_path, force=True))

    assert not list(outside.iterdir()), "forced repair must not write through a refs symlink"
