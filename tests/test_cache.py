from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.cache import Slug, Target, maw_paths, prey_paths, resolve_target
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


@pytest.mark.parametrize(
    ("text", "owner", "repo", "host"),
    [
        ("github/.github", "github", ".github", "github.com"),
        ("github/_community", "github", "_community", "github.com"),
        ("https://github.com/microsoft/.github", "microsoft", ".github", "github.com"),
        ("https://github.com/o/.dotfiles", "o", ".dotfiles", "github.com"),
        ("git@github.com:o/_tools.git", "o", "_tools", "github.com"),
        ("https://github.com/o/.hidden.git", "o", ".hidden", "github.com"),
        ("gitlab.com/gitlab-org/_test", "gitlab-org", "_test", "gitlab.com"),
        ("https://gitlab.com/group/.config", "group", ".config", "gitlab.com"),
        ("gitlab.com/group/_nested/.dotrepo", "group/_nested", ".dotrepo", "gitlab.com"),
        ("git@gitlab.com:group/_repo", "group", "_repo", "gitlab.com"),
    ],
)
def test_slug_parse_accepts_leading_dot_or_underscore(
    text: str, owner: str, repo: str, host: str
) -> None:
    assert Slug.parse(text) == Slug(owner, repo, host)


@pytest.mark.parametrize(
    "text",
    [
        "owner/.",
        "owner/..",
        "owner/-repo",
        "-owner/repo",
        "_owner/repo",
        "https://github.com/o/-repo",
        "gitlab.com/group/.",
        "gitlab.com/group/..",
        "gitlab.com/group/-repo",
        "gitlab.com/group/-sub/repo",
        "owner/repo.",
        "owner./repo",
        "owner/.github.",
        "https://github.com/o/.hidden.",
        "git@github.com:o/_repo..git",
        "gitlab.com/group/repo.",
        "gitlab.com/group./repo",
        "gitlab.com/group/.../repo",
    ],
)
def test_slug_parse_rejects_unsafe_names(text: str) -> None:
    with pytest.raises(UsageError):
        Slug.parse(text)


def test_resolve_target_accepts_leading_dot_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_target("github/.github") == Target(slug=Slug("github", ".github"))


def test_prey_paths_layout(tmp_path: Path) -> None:
    paths = prey_paths(Slug("owner", "repo"), tmp_path)
    assert paths.root == tmp_path / "github" / "owner" / "repo"
    assert paths.repo == paths.root / "repo"
    assert paths.api == paths.root / "api"
    assert paths.digests == paths.root / "digests"
    assert paths.catch_file == paths.root / "catch.json"


def test_maw_paths_are_stable_and_distinct(tmp_path: Path) -> None:
    first = maw_paths(tmp_path / "alpha", tmp_path)
    again = maw_paths(tmp_path / "alpha", tmp_path)
    other = maw_paths(tmp_path / "beta", tmp_path)
    assert first.root == again.root
    assert first.root != other.root
    assert first.root.parent == tmp_path / "maws"
    assert first.root.name.startswith("alpha-")


def test_resolve_target_prefers_existing_directory(tmp_path: Path) -> None:
    local = resolve_target(str(tmp_path))
    assert local.is_local and local.path == tmp_path.resolve()
    assert local.label == tmp_path.name
    remote = resolve_target("owner/repo")
    assert remote == Target(slug=Slug("owner", "repo"))
    assert remote.label == "owner/repo"


def test_existing_file_is_not_a_remote_repository(tmp_path: Path) -> None:
    path = tmp_path / "README.md"
    path.write_text("# A file, not a prey directory\\n", encoding="utf-8")
    with pytest.raises(UsageError, match="not a directory"):
        resolve_target(str(path))


@pytest.mark.parametrize("missing", ["./missing", "../missing", "C:\\\\missing", "~/_missing"])
def test_missing_explicit_target_never_becomes_github(missing: str) -> None:
    with pytest.raises(UsageError, match="no such directory"):
        resolve_target(missing)


def test_missing_absolute_target_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="no such directory"):
        resolve_target(str(tmp_path / "does-not-exist"))


def test_missing_plain_local_name_is_not_a_repo() -> None:
    with pytest.raises(UsageError, match="no such directory"):
        resolve_target("missing")
    with pytest.raises(UsageError, match="must not be empty"):
        resolve_target("")


def test_ambiguous_owner_repo_logs_remote_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    messages: list[str] = []
    target = resolve_target("vendor/lib", log=messages.append)
    assert target == Target(slug=Slug("vendor", "lib"))
    assert len(messages) == 1
    assert "not a directory here" in messages[0]
    assert "github.com/vendor/lib" in messages[0]
