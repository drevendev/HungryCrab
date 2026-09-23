"""Regression guards for canonical digest generation resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
