from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import hungry_crab.compare as compare
import hungry_crab.digest_reader as digest_reader
from hungry_crab.cache import Slug, Target
from hungry_crab.digest import DigestOptions
from hungry_crab.errors import CrabError


def test_meal_for_uses_logical_sha(monkeypatch, tmp_path: Path) -> None:
    sha = "a" * 40
    target = Target(slug=Slug("owner", "repo"))
    options = DigestOptions(cache_root=tmp_path / "cache")

    def locate(_target: Target, _options: DigestOptions) -> str:
        assert _target == target
        assert _options is options
        return sha

    monkeypatch.setattr(compare, "locate_digest_sha", locate)

    meal = compare.meal_for(target, tmp_path / "maw", options)

    assert meal.name == f"owner-repo@{sha}"


def test_meal_for_does_not_resolve_malformed_digest_ref(monkeypatch, tmp_path: Path) -> None:
    sha = "b" * 40
    digests = tmp_path / "digests"
    canonical = digests / sha
    ref_path = digests / ".refs" / f"{sha}.json"
    ref_path.parent.mkdir(parents=True)
    ref_path.write_text("{not-json", encoding="utf-8")
    target = Target(slug=Slug("owner", "repo"))
    options = DigestOptions(cache_root=tmp_path / "cache")
    ctx = SimpleNamespace(sha=sha)

    def prepare(_target: Target, _options: DigestOptions):
        assert _target == target
        assert _options is options
        return ctx, canonical

    monkeypatch.setattr(digest_reader, "prepare_context", prepare)

    meal = compare.meal_for(target, tmp_path / "maw", options)

    assert meal.name == f"owner-repo@{sha}"
    with pytest.raises(CrabError, match="cannot read digest ref"):
        digest_reader.locate_digest_location(target, options)
