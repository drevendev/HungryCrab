from __future__ import annotations

from pathlib import Path

import hungry_crab.compare as compare
from hungry_crab.cache import Slug, Target
from hungry_crab.digest import DigestOptions
from hungry_crab.digest_location import DigestLocation


def test_meal_for_uses_logical_sha_not_generation_name(
    monkeypatch, tmp_path: Path
) -> None:
    sha = "a" * 40
    generation = tmp_path / "digests" / ".generations" / sha / "generation-b"
    target = Target(slug=Slug("owner", "repo"))
    options = DigestOptions(cache_root=tmp_path / "cache")

    def locate(_target: Target, _options: DigestOptions) -> DigestLocation:
        assert _target == target
        assert _options is options
        return DigestLocation(sha=sha, path=generation)

    monkeypatch.setattr(compare, "locate_digest_location", locate)

    meal = compare.meal_for(target, tmp_path / "maw", options)

    assert meal.name == f"owner-repo@{sha}"
    assert generation.name not in meal.name
