"""Regression guards for writer-side canonical digest publication."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hungry_crab import digest_location, digest_publication
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


def test_generation_and_temp_ref_fsync_precede_ref_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    generation = allocate_digest_generation(digests, SHA)
    _write_artifact(generation.path, "complete")
    (generation.path / "payload.json").write_text("extra artifact\n", encoding="utf-8")
    events: list[str] = []
    original_fsync = digest_publication.os.fsync
    original_replace = Path.replace
    refs_dir = digests / ".refs"
    known_paths = {
        "manifest": generation.path / "manifest.json",
        "payload": generation.path / "payload.json",
        "generation_dir": generation.path,
        "sha_dir": generation.path.parent,
        "generations_dir": generation.path.parent.parent,
        "digests_dir": digests,
        "refs_dir": refs_dir,
    }

    def tracked_fsync(fd: int) -> None:
        opened = os.fstat(fd)
        targets = [
            name
            for name, path in known_paths.items()
            if path.exists() and os.path.samestat(opened, path.stat())
        ]
        targets.extend(
            "temp_ref"
            for path in refs_dir.glob(f".{SHA}.*.tmp")
            if os.path.samestat(opened, path.stat())
        )
        assert len(targets) == 1, "each durability barrier must have a known descriptor"
        events.append(targets[0])
        original_fsync(fd)

    def tracked_replace(self: Path, target: Path) -> Path:
        if self.name.endswith(".tmp"):
            events.append("replace")
        return original_replace(self, target)

    monkeypatch.setattr(digest_publication.os, "fsync", tracked_fsync)
    monkeypatch.setattr(Path, "replace", tracked_replace)

    publish_digest_generation(digests, generation)

    assert events.count("replace") == 1
    switch = events.index("replace")
    before, after = events[:switch], events[switch + 1 :]
    for name in ("manifest", "payload", "temp_ref"):
        assert before.count(name) == 1, f"{name} must be synced before ref switch"
    if os.name != "nt":
        for name in ("generation_dir", "sha_dir", "generations_dir"):
            assert before.count(name) == 1, f"{name} must be synced before ref switch"
        assert before.count("digests_dir") >= 2, "cache root must be synced after refs mkdir"
        assert after == ["refs_dir"], "canonical refs directory must sync after replacement"
    else:
        assert not after, "Windows directory fsync is explicitly unsupported"
    assert resolve_canonical_digest(digests, SHA).path == generation.path


def test_generation_fsync_failure_does_not_switch_active_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    before = allocate_digest_generation(digests, SHA)
    _write_artifact(before.path, "before")
    publish_digest_generation(digests, before)
    after = allocate_digest_generation(digests, SHA)
    _write_artifact(after.path, "after")

    def unavailable_fsync(_fd: int) -> None:
        raise OSError("simulated durability barrier failure")

    monkeypatch.setattr(digest_publication.os, "fsync", unavailable_fsync)
    with pytest.raises(CrabError, match="could not sync complete digest generation"):
        publish_digest_generation(digests, after)

    assert resolve_canonical_digest(digests, SHA).path == before.path


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory sync only")
def test_late_parent_directory_sync_failure_preserves_active_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    before = allocate_digest_generation(digests, SHA)
    _write_artifact(before.path, "before")
    publish_digest_generation(digests, before)
    after = allocate_digest_generation(digests, SHA)
    _write_artifact(after.path, "after")

    original_sync_directory = digest_publication._sync_directory
    root_sync_calls = 0

    def refuse_second_root_sync(path: Path) -> None:
        nonlocal root_sync_calls
        if path == digests:
            root_sync_calls += 1
            if root_sync_calls == 2:
                raise OSError("simulated late pre-switch directory sync failure")
        original_sync_directory(path)

    monkeypatch.setattr(digest_publication, "_sync_directory", refuse_second_root_sync)
    with pytest.raises(CrabError, match="could not prepare digest refs root"):
        publish_digest_generation(digests, after)

    assert root_sync_calls == 2
    assert resolve_canonical_digest(digests, SHA).path == before.path
    assert not list((digests / ".refs").glob(f".{SHA}.*.tmp"))


def test_post_switch_sync_failure_does_not_claim_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    before = allocate_digest_generation(digests, SHA)
    _write_artifact(before.path, "before")
    publish_digest_generation(digests, before)
    after = allocate_digest_generation(digests, SHA)
    _write_artifact(after.path, "after")
    original_sync_dir = digest_publication._sync_directory

    def refuse_ref_directory_sync(path: Path) -> None:
        if path.name == ".refs":
            raise OSError("simulated directory sync failure")
        original_sync_dir(path)

    monkeypatch.setattr(digest_publication, "_sync_directory", refuse_ref_directory_sync)
    with pytest.raises(CrabError, match="may already point"):
        publish_digest_generation(digests, after)

    assert resolve_canonical_digest(digests, SHA).path == after.path
    assert not list((digests / ".refs").glob(f".{SHA}.*.tmp"))


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
