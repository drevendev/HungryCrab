"""Regression guards for canonical digest generation resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hungry_crab import digest_location
from hungry_crab.digest_location import REF_SCHEMA, resolve_canonical_digest
from hungry_crab.errors import CrabError

SHA = "a" * 40


def _write_ref(digests: Path, generation: str) -> None:
    refs = digests / ".refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / f"{SHA}.json").write_text(
        json.dumps({"schema": REF_SCHEMA, "generation": generation}) + "\n",
        encoding="utf-8",
    )


def test_resolver_keeps_legacy_digest_readable_without_ref(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    legacy = digests / SHA
    legacy.mkdir(parents=True)

    resolved = resolve_canonical_digest(digests, SHA)

    assert resolved.sha == SHA
    assert resolved.path == legacy


def test_ref_switch_does_not_change_an_already_resolved_snapshot(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    generation_a = digests / ".generations" / SHA / "generation-a"
    generation_b = digests / ".generations" / SHA / "generation-b"
    generation_a.mkdir(parents=True)
    generation_b.mkdir(parents=True)

    _write_ref(digests, "generation-a")
    first = resolve_canonical_digest(digests, SHA)

    # An abandoned complete generation is invisible until the active ref switches to it.
    assert first.path == generation_a
    assert resolve_canonical_digest(digests, SHA).path == generation_a

    _write_ref(digests, "generation-b")
    second = resolve_canonical_digest(digests, SHA)

    assert first.sha == second.sha == SHA
    assert first.path == generation_a
    assert second.path == generation_b


def test_present_but_broken_ref_fails_closed(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    legacy = digests / SHA
    legacy.mkdir(parents=True)
    _write_ref(digests, "missing-generation")

    with pytest.raises(CrabError, match="points to missing generation"):
        resolve_canonical_digest(digests, SHA)


def test_present_non_file_ref_fails_closed(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    legacy = digests / SHA
    legacy.mkdir(parents=True)
    ref_path = digests / ".refs" / f"{SHA}.json"
    ref_path.mkdir(parents=True)

    with pytest.raises(CrabError, match="expected a regular file"):
        resolve_canonical_digest(digests, SHA)


def test_windows_ref_read_retries_transient_sharing_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    generation = digests / ".generations" / SHA / "current"
    generation.mkdir(parents=True)
    _write_ref(digests, "current")
    ref_path = digests / ".refs" / f"{SHA}.json"

    monkeypatch.setattr(digest_location, "_IS_WINDOWS", True)
    monkeypatch.setattr(digest_location, "_REF_RETRY_DELAY_SECONDS", 0)
    original = Path.read_text
    reads = 0

    def intermittently_locked(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        nonlocal reads
        if self == ref_path:
            reads += 1
            if reads <= 2:
                raise PermissionError("simulated Windows sharing violation")
        return original(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", intermittently_locked)

    assert resolve_canonical_digest(digests, SHA).path == generation
    assert reads == 3


def test_windows_ref_stat_retries_transient_sharing_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    generation = digests / ".generations" / SHA / "current"
    generation.mkdir(parents=True)
    _write_ref(digests, "current")
    ref_path = digests / ".refs" / f"{SHA}.json"

    monkeypatch.setattr(digest_location, "_IS_WINDOWS", True)
    monkeypatch.setattr(digest_location, "_REF_RETRY_DELAY_SECONDS", 0)
    original = Path.lstat
    stats = 0

    def intermittently_locked(self: Path) -> os.stat_result:
        nonlocal stats
        if self == ref_path:
            stats += 1
            if stats <= 2:
                raise PermissionError("simulated Windows sharing violation")
        return original(self)

    monkeypatch.setattr(Path, "lstat", intermittently_locked)

    assert resolve_canonical_digest(digests, SHA).path == generation
    assert stats == 3


@pytest.mark.parametrize("is_windows, expected_attempts", [(True, 10), (False, 1)])
def test_ref_read_denial_never_falls_back_to_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_windows: bool,
    expected_attempts: int,
) -> None:
    digests = tmp_path / "digests"
    (digests / SHA).mkdir(parents=True)  # A stale legacy slot must never become authoritative.
    _write_ref(digests, "current")
    ref_path = digests / ".refs" / f"{SHA}.json"

    monkeypatch.setattr(digest_location, "_IS_WINDOWS", is_windows)
    monkeypatch.setattr(digest_location, "_REF_RETRY_DELAY_SECONDS", 0)
    original = Path.read_text
    reads = 0

    def permanently_locked(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        nonlocal reads
        if self == ref_path:
            reads += 1
            raise PermissionError("simulated Windows sharing violation")
        return original(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", permanently_locked)

    with pytest.raises(CrabError, match="cannot read digest ref"):
        resolve_canonical_digest(digests, SHA)
    assert reads == expected_attempts


def test_unreadable_ref_has_actionable_force_hint(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    _write_ref(digests, "gone")
    (digests / ".refs" / f"{SHA}.json").write_text("", encoding="utf-8")
    with pytest.raises(CrabError, match="cannot read digest ref") as exc:
        resolve_canonical_digest(digests, SHA)
    assert exc.value.hint is not None and "--force" in exc.value.hint


def test_missing_generation_hint_requires_force(tmp_path: Path) -> None:
    digests = tmp_path / "digests"
    _write_ref(digests, "gone")
    (digests / ".generations" / SHA).mkdir(parents=True)
    with pytest.raises(CrabError, match="points to missing generation") as exc:
        resolve_canonical_digest(digests, SHA)
    assert exc.value.hint is not None and "--force" in exc.value.hint
