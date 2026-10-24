"""Regression guards for writer-side canonical digest publication."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hungry_crab import digest_location
from hungry_crab.digest_location import REF_SCHEMA, resolve_canonical_digest
from hungry_crab.digest_publication import (
    DigestGeneration,
    allocate_digest_generation,
    publish_digest_generation,
)
from hungry_crab.errors import CrabError
from hungry_crab.miners import MINER_NAMES

SHA = "a" * 40


def _write_artifact(path: Path, value: str) -> None:
    manifest = {
        "value": value,
        "miners": [{"name": name, "ok": True, "status": "ok"} for name in MINER_NAMES],
    }
    (path / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _symlink_directory(target: Path, link: Path, kind: str = "symlink") -> None:
    """Point ``link`` at ``target`` through a symlink or a Windows junction.

    A junction needs no privilege and reads as a plain directory to ``S_ISDIR``, so the
    junction twins run on every Windows machine, unlike the symlink tests.
    """
    if kind == "junction":
        if sys.platform != "win32":
            pytest.skip("junctions exist only on Windows")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"junction creation failed: {completed.stderr[:300]}"
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable on this platform: {exc}")


LINK_KINDS = pytest.mark.parametrize("kind", ["symlink", "junction"])


def test_generation_allocation_never_reuses_a_mutable_path(tmp_path: Path) -> None:
    digests = tmp_path / "digests"

    first = allocate_digest_generation(digests, SHA)
    second = allocate_digest_generation(digests, SHA)

    assert first.name != second.name
    assert first.path != second.path
    assert first.path.is_dir()
    assert second.path.is_dir()


@LINK_KINDS
def test_generation_allocation_rejects_symlinked_generations_root(
    kind: str, tmp_path: Path
) -> None:
    digests = tmp_path / "digests"
    digests.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_directory(outside, digests / ".generations", kind)

    with pytest.raises(CrabError, match="expected a real directory"):
        allocate_digest_generation(digests, SHA)

    assert not (outside / SHA).exists(), "allocation must not descend through a symlink"


@LINK_KINDS
def test_generation_allocation_rejects_symlinked_sha_root(kind: str, tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generations = digests / ".generations"
    generations.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_directory(outside, generations / SHA, kind)

    with pytest.raises(CrabError, match="expected a real directory"):
        allocate_digest_generation(digests, SHA)

    assert not list(outside.iterdir()), "allocation must not descend through a SHA symlink"


def test_ref_switch_retains_the_previous_reader_snapshot(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation_a = allocate_digest_generation(digests, SHA)
    _write_artifact(generation_a.path, "generation-a")
    publish_digest_generation(digests, generation_a)

    reader_a = resolve_canonical_digest(digests, SHA)
    assert reader_a.path == generation_a.path

    generation_b = allocate_digest_generation(digests, SHA)
    _write_artifact(generation_b.path, "generation-b")
    publish_digest_generation(digests, generation_b)

    reader_b = resolve_canonical_digest(digests, SHA)
    assert reader_b.path == generation_b.path
    assert reader_a.path == generation_a.path
    manifest_a = json.loads((reader_a.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_a["value"] == "generation-a"
    assert generation_a.path.is_dir(), "publication must not reclaim a live reader snapshot"


def test_invalid_new_generation_cannot_replace_the_active_ref(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation_a = allocate_digest_generation(digests, SHA)
    _write_artifact(generation_a.path, "generation-a")
    publish_digest_generation(digests, generation_a)

    missing = DigestGeneration(
        sha=SHA,
        name="missing-generation",
        path=digests / ".generations" / SHA / "missing-generation",
    )
    with pytest.raises(CrabError, match="cannot publish unsafe digest generation"):
        publish_digest_generation(digests, missing)

    assert resolve_canonical_digest(digests, SHA).path == generation_a.path


@LINK_KINDS
def test_publication_rejects_symlinked_generation(kind: str, tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_artifact(outside, "outside")

    generation.path.rmdir()
    _symlink_directory(outside, generation.path, kind)

    with pytest.raises(CrabError, match="cannot publish unsafe digest generation"):
        publish_digest_generation(digests, generation)

    assert not (digests / ".refs" / f"{SHA}.json").exists()


@LINK_KINDS
def test_refs_root_symlink_cannot_redirect_publication_or_resolution(
    kind: str, tmp_path: Path
) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    _write_artifact(generation.path, "complete")
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_directory(outside, digests / ".refs", kind)

    with pytest.raises(CrabError, match="digest refs root"):
        publish_digest_generation(digests, generation)

    assert not list(outside.iterdir()), "publication must not write through a refs symlink"

    (outside / f"{SHA}.json").write_text(
        f'{{"schema": "{REF_SCHEMA}", "generation": "{generation.name}"}}\n',
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(CrabError, match="digest refs root"):
        resolve_canonical_digest(digests, SHA)


@LINK_KINDS
def test_resolution_rejects_generation_replaced_by_symlink(kind: str, tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    _write_artifact(generation.path, "complete")
    publish_digest_generation(digests, generation)

    (generation.path / "manifest.json").unlink()
    generation.path.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_artifact(outside, "outside")
    _symlink_directory(outside, generation.path, kind)

    with pytest.raises(CrabError, match="does not resolve to a safe generation"):
        resolve_canonical_digest(digests, SHA)


def test_publication_ref_is_a_regular_small_pointer_file(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    _write_artifact(generation.path, "complete")

    published = publish_digest_generation(digests, generation)
    ref_path = digests / ".refs" / f"{SHA}.json"

    assert published.path == generation.path
    assert ref_path.is_file()
    assert resolve_canonical_digest(digests, SHA).path == generation.path
    assert not list(ref_path.parent.glob(f".{SHA}.*.tmp"))


@LINK_KINDS
def test_explicit_output_through_a_link_is_refused(
    kind: str, npm_app: Path, tmp_path: Path
) -> None:
    """`--out` that is a symlink or a junction must not carry the digest elsewhere."""
    from conftest import FIXED_NOW

    from hungry_crab.cache import Target
    from hungry_crab.digest import DigestOptions, run_digest

    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    _symlink_directory(outside, out, kind)

    with pytest.raises(CrabError, match="not a real directory"):
        run_digest(
            Target(path=npm_app),
            DigestOptions(out=out, now=FIXED_NOW, cache_root=tmp_path / "cache"),
        )
    assert list(outside.iterdir()) == []


def test_windows_ref_replace_retries_transient_sharing_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    _write_artifact(generation.path, "complete")

    monkeypatch.setattr(digest_location, "_IS_WINDOWS", True)
    monkeypatch.setattr(digest_location, "_REF_RETRY_DELAY_SECONDS", 0)
    original = Path.replace
    replaces = 0

    def intermittently_locked(self: Path, target: Path) -> Path:
        nonlocal replaces
        if self.name.endswith(".tmp"):
            replaces += 1
            if replaces <= 2:
                raise PermissionError("simulated Windows sharing violation")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", intermittently_locked)

    assert publish_digest_generation(digests, generation).path == generation.path
    assert replaces == 3
    assert resolve_canonical_digest(digests, SHA).path == generation.path


def test_windows_ref_replace_exhaustion_preserves_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    before = allocate_digest_generation(digests, SHA)
    _write_artifact(before.path, "before")
    publish_digest_generation(digests, before)

    after = allocate_digest_generation(digests, SHA)
    _write_artifact(after.path, "after")

    monkeypatch.setattr(digest_location, "_IS_WINDOWS", True)
    monkeypatch.setattr(digest_location, "_REF_RETRY_DELAY_SECONDS", 0)
    original = Path.replace
    replaces = 0

    def permanently_locked(self: Path, target: Path) -> Path:
        nonlocal replaces
        if self.name.endswith(".tmp"):
            replaces += 1
            raise PermissionError("simulated Windows sharing violation")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", permanently_locked)

    with pytest.raises(CrabError, match="could not publish digest generation"):
        publish_digest_generation(digests, after)

    assert replaces == 10
    assert resolve_canonical_digest(digests, SHA).path == before.path
    assert not list((digests / ".refs").glob(f".{SHA}.*.tmp"))
