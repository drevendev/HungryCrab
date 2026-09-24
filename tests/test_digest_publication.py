"""Regression guards for writer-side canonical digest publication."""

from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.digest_location import resolve_canonical_digest
from hungry_crab.digest_publication import (
    DigestGeneration,
    allocate_digest_generation,
    publish_digest_generation,
)
from hungry_crab.errors import CrabError

SHA = "a" * 40


def _write_artifact(path: Path, value: str) -> None:
    (path / "manifest.json").write_text(value, encoding="utf-8", newline="\n")


def _symlink_directory(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable on this platform: {exc}")


def test_generation_allocation_never_reuses_a_mutable_path(tmp_path: Path) -> None:
    digests = tmp_path / "digests"

    first = allocate_digest_generation(digests, SHA)
    second = allocate_digest_generation(digests, SHA)

    assert first.name != second.name
    assert first.path != second.path
    assert first.path.is_dir()
    assert second.path.is_dir()


def test_generation_allocation_rejects_symlinked_generations_root(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    digests.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_directory(outside, digests / ".generations")

    with pytest.raises(CrabError, match="expected a real directory"):
        allocate_digest_generation(digests, SHA)

    assert not (outside / SHA).exists(), "allocation must not descend through a symlink"


def test_generation_allocation_rejects_symlinked_sha_root(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generations = digests / ".generations"
    generations.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_directory(outside, generations / SHA)

    with pytest.raises(CrabError, match="expected a real directory"):
        allocate_digest_generation(digests, SHA)

    assert not list(outside.iterdir()), "allocation must not descend through a SHA symlink"


def test_ref_switch_retains_the_previous_reader_snapshot(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation_a = allocate_digest_generation(digests, SHA)
    _write_artifact(generation_a.path, "generation-a\n")
    publish_digest_generation(digests, generation_a)

    reader_a = resolve_canonical_digest(digests, SHA)
    assert reader_a.path == generation_a.path

    generation_b = allocate_digest_generation(digests, SHA)
    _write_artifact(generation_b.path, "generation-b\n")
    publish_digest_generation(digests, generation_b)

    reader_b = resolve_canonical_digest(digests, SHA)
    assert reader_b.path == generation_b.path
    assert reader_a.path == generation_a.path
    assert (reader_a.path / "manifest.json").read_text(encoding="utf-8") == "generation-a\n"
    assert generation_a.path.is_dir(), "publication must not reclaim a live reader snapshot"


def test_invalid_new_generation_cannot_replace_the_active_ref(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation_a = allocate_digest_generation(digests, SHA)
    _write_artifact(generation_a.path, "generation-a\n")
    publish_digest_generation(digests, generation_a)

    missing = DigestGeneration(
        sha=SHA,
        name="missing-generation",
        path=digests / ".generations" / SHA / "missing-generation",
    )
    with pytest.raises(CrabError, match="cannot publish unsafe digest generation"):
        publish_digest_generation(digests, missing)

    assert resolve_canonical_digest(digests, SHA).path == generation_a.path


def test_publication_rejects_symlinked_generation(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_artifact(outside, "outside\n")

    generation.path.rmdir()
    _symlink_directory(outside, generation.path)

    with pytest.raises(CrabError, match="cannot publish unsafe digest generation"):
        publish_digest_generation(digests, generation)

    assert not (digests / ".refs" / f"{SHA}.json").exists()


def test_resolution_rejects_generation_replaced_by_symlink(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    _write_artifact(generation.path, "complete\n")
    publish_digest_generation(digests, generation)

    (generation.path / "manifest.json").unlink()
    generation.path.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_artifact(outside, "outside\n")
    _symlink_directory(outside, generation.path)

    with pytest.raises(CrabError, match="does not resolve to a safe generation"):
        resolve_canonical_digest(digests, SHA)


def test_publication_ref_is_a_regular_small_pointer_file(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    _write_artifact(generation.path, "complete\n")

    published = publish_digest_generation(digests, generation)
    ref_path = digests / ".refs" / f"{SHA}.json"

    assert published.path == generation.path
    assert ref_path.is_file()
    assert resolve_canonical_digest(digests, SHA).path == generation.path
    assert not list(ref_path.parent.glob(f".{SHA}.*.tmp"))
