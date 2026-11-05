"""Exact pins and neutral inputs survive acquisition without following defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git
from qualification_pinned_acquire import fetch_pin, records
from test_discovery_inputs import repository

from hungry_crab.errors import CrabError


def row() -> dict[str, Any]:
    return {
        "id": "control-1",
        "batch": "control",
        "repository": "owned/source",
        "provider_id": 1,
        "sha": "a" * 40,
        "tree": "b" * 40,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"expected_answer": "a useful change"},
        {"sha": "master"},
        {"tree": "../escape"},
        {"provider_id": True},
        {"id": "../escape"},
        {"repository": "https://evil.invalid/a/b"},
    ],
)
def test_bad_or_non_neutral_catalog_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises((ValueError, CrabError)):
        records({"schema": 1, "pool": [{**row(), **change}]}, "control")


def test_exact_old_commit_is_fetched_instead_of_moving_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = repository(tmp_path / "source", {"data.txt": "original\n"})
    old = git(source, "rev-parse", "HEAD").strip()
    tree = git(source, "rev-parse", "HEAD^{tree}").strip()
    (source / "data.txt").write_text("newer\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "fixture: move default")
    target = tmp_path / "target"
    target.mkdir()
    from hungry_crab.cache import Slug

    monkeypatch.setattr(Slug, "url", property(lambda _: str(source)))
    # fetch_pin appends .git to a canonical HTTPS locator; the local test endpoint
    # retains that shape without a shell, server or donor program.
    endpoint = Path(str(source) + ".git")
    source.rename(endpoint)
    fetch_pin(target, {**row(), "sha": old, "tree": tree})
    assert git(target, "rev-parse", "HEAD").strip() == old
    assert (target / "data.txt").read_text() == "original\n"
