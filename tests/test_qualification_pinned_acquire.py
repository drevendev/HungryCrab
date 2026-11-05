"""Exact pins and neutral inputs survive acquisition without following defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git
from qualification_pinned_acquire import digest, fetch_pin, records
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


def test_shared_digest_retains_canonical_public_source_identity(tmp_path: Path) -> None:
    import json

    from hungry_crab.cache import Slug, prey_paths

    slug = Slug("owned", "source")
    cache = tmp_path / "cache"
    paths = prey_paths(slug, cache)
    repo = repository(paths.repo, {"source.py": "raise RuntimeError('never execute donor')\n"})
    pin = git(repo, "rev-parse", "HEAD").strip()
    output = tmp_path / "digest"
    digest(slug, output, Path(__file__).resolve().parents[1], cache, tmp_path / "digest.log")
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["prey"]["url"] == slug.url
    assert manifest["prey"]["label"] == str(slug)
    assert manifest["prey"]["sha"] == pin
    from qualification_acquire import export_objects

    from hungry_crab.cache import Target
    from hungry_crab.compare import compare_for_maw
    from hungry_crab.digest import DigestOptions

    objects = tmp_path / "objects"
    export_objects(repo, objects, slug, 1024 * 1024)
    maw = repository(tmp_path / "maw", {"app.py": "def owned(): return 1\n"})
    result, _, _, _ = compare_for_maw(
        Target(slug=slug),
        maw,
        digest_options=DigestOptions(cache_root=tmp_path / "meal-cache", maw_license="MIT"),
        record_ledger=False,
        prey_digest_dir=output,
        prey_objects=objects,
    )
    assert result.meal_dir is not None
