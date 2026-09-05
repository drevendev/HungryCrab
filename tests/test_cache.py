from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.cache import Slug, Target, host_paths, prey_paths, resolve_target
from hungry_crab.errors import UsageError


@pytest.mark.parametrize(
    "text",
    [
        "drevendev/HungryCrab",
        "https://github.com/drevendev/HungryCrab",
        "https://github.com/drevendev/HungryCrab.git",
        "https://github.com/drevendev/HungryCrab/",
        "github.com/drevendev/HungryCrab",
        "git@github.com:drevendev/HungryCrab.git",
        "  drevendev/HungryCrab.git ",
    ],
)
def test_slug_parse_accepts_common_forms(text: str) -> None:
    slug = Slug.parse(text)
    assert (slug.owner, slug.repo) == ("drevendev", "HungryCrab")
    assert str(slug) == "drevendev/HungryCrab"
    assert slug.clone_url == "https://github.com/drevendev/HungryCrab.git"


@pytest.mark.parametrize("text", ["", "just-a-name", "a/b/c", "../x/y", "owner/re po"])
def test_slug_parse_rejects_garbage(text: str) -> None:
    with pytest.raises(UsageError):
        Slug.parse(text)


def test_prey_paths_layout(tmp_path: Path) -> None:
    paths = prey_paths(Slug("owner", "repo"), tmp_path)
    assert paths.root == tmp_path / "github" / "owner" / "repo"
    assert paths.repo == paths.root / "repo"
    assert paths.api == paths.root / "api"
    assert paths.digests == paths.root / "digests"
    assert paths.catch_file == paths.root / "catch.json"


def test_host_paths_are_stable_and_distinct(tmp_path: Path) -> None:
    first = host_paths(tmp_path / "alpha", tmp_path)
    again = host_paths(tmp_path / "alpha", tmp_path)
    other = host_paths(tmp_path / "beta", tmp_path)
    assert first.root == again.root
    assert first.root != other.root
    assert first.root.parent == tmp_path / "hosts"
    assert first.root.name.startswith("alpha-")


def test_resolve_target_prefers_existing_directory(tmp_path: Path) -> None:
    local = resolve_target(str(tmp_path))
    assert local.is_local and local.path == tmp_path.resolve()
    assert local.label == tmp_path.name
    remote = resolve_target("owner/repo")
    assert remote == Target(slug=Slug("owner", "repo"))
    assert remote.label == "owner/repo"
