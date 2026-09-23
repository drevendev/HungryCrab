"""Regression guards for target-to-digest reader resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import hungry_crab.digest_reader as digest_reader
from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions
from hungry_crab.digest_location import REF_SCHEMA

SHA = "a" * 40


def _stub_context(
    monkeypatch: pytest.MonkeyPatch, out_dir: Path, *, worktree: str = "clean"
) -> None:
    def fake_prepare_context(
        target: Target, options: DigestOptions
    ) -> tuple[SimpleNamespace, Path]:
        del target, options
        return SimpleNamespace(sha=SHA, worktree=worktree), out_dir

    monkeypatch.setattr(digest_reader, "prepare_context", fake_prepare_context)


def _write_ref(digests: Path, generation: str) -> Path:
    generation_dir = digests / ".generations" / SHA / generation
    generation_dir.mkdir(parents=True)
    refs = digests / ".refs"
    refs.mkdir(parents=True)
    (refs / f"{SHA}.json").write_text(
        json.dumps({"schema": REF_SCHEMA, "generation": generation}) + "\n",
        encoding="utf-8",
    )
    return generation_dir


def test_clean_canonical_target_resolves_active_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = tmp_path / "digests"
    legacy = digests / SHA
    generation = _write_ref(digests, "generation-a")
    _stub_context(monkeypatch, legacy)

    location = digest_reader.locate_digest_location(Target(path=tmp_path / "prey"))

    assert location.sha == SHA
    assert location.path == generation


def test_clean_full_target_preselected_as_noncanonical_keeps_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "digests" / ".scratch" / SHA / "future-policy"
    _stub_context(monkeypatch, out_dir)

    def fail_if_refs_are_consulted(digests: Path, sha: str) -> None:
        del digests, sha
        raise AssertionError("non-canonical selected output must not consult generation refs")

    monkeypatch.setattr(digest_reader, "resolve_canonical_digest", fail_if_refs_are_consulted)

    location = digest_reader.locate_digest_location(Target(path=tmp_path / "prey"))

    assert location.sha == SHA
    assert location.path == out_dir


@pytest.mark.parametrize(
    ("options", "worktree", "relative_out"),
    [
        (DigestOptions(miners=["license"]), "clean", ".scratch/request"),
        (DigestOptions(), "dirty", ".scratch/dirty-request"),
        (DigestOptions(out=Path("explicit")), "clean", "explicit"),
    ],
)
def test_noncanonical_target_keeps_selected_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: DigestOptions,
    worktree: str,
    relative_out: str,
) -> None:
    out_dir = tmp_path / relative_out
    _stub_context(monkeypatch, out_dir, worktree=worktree)

    location = digest_reader.locate_digest_location(Target(path=tmp_path / "prey"), options)

    assert location.sha == SHA
    assert location.path == out_dir
