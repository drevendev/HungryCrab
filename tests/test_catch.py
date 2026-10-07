from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fixture_builder import git as fixture_git

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.errors import ExternalCommandError, UsageError
from hungry_crab.fetch.catch import CatchOptions, catch, clone_arguments, parse_since
from hungry_crab.fetch.git import git_env

NOW = datetime(2025, 6, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2y", date(2023, 6, 2)),
        ("6m", date(2024, 12, 3)),
        ("90d", date(2025, 3, 3)),
        ("4w", date(2025, 5, 4)),
        ("2024-01-15", date(2024, 1, 15)),
    ],
)
def test_parse_since(text: str, expected: date) -> None:
    assert parse_since(text, now=NOW) == expected


def test_parse_since_rejects_nonsense() -> None:
    with pytest.raises(UsageError):
        parse_since("yesterday", now=NOW)


def test_clone_arguments() -> None:
    assert clone_arguments(CatchOptions()) == ["clone", "--quiet"]
    assert clone_arguments(CatchOptions(shallow=True)) == [
        "clone", "--quiet", "--depth", "1", "--single-branch",
    ]  # fmt: skip
    assert clone_arguments(CatchOptions(since="2y"), now=NOW) == [
        "clone", "--quiet", "--shallow-since=2023-06-02", "--no-single-branch",
    ]  # fmt: skip
    assert clone_arguments(CatchOptions(shallow=True, since="90d"), now=NOW) == [
        "clone", "--quiet", "--shallow-since=2025-03-03", "--single-branch",
    ]  # fmt: skip


def test_catch_clones_then_refreshes_from_a_local_source(npm_app: Path, tmp_path: Path) -> None:
    slug = Slug("example", "crab-cove")
    cache = tmp_path / "cache"
    first = catch(slug, cache_root=cache, source_url=str(npm_app), now=NOW)
    paths = prey_paths(slug, cache)
    assert Path(first.repo_dir) == paths.repo
    assert (paths.repo / "package.json").is_file()
    assert first.updated is False
    assert first.default_branch == "main"
    assert first.shallow is False
    assert len(first.sha) == 40
    recorded = json.loads(paths.catch_file.read_text(encoding="utf-8"))
    assert recorded["slug"] == "example/crab-cove"
    assert recorded["sha"] == first.sha

    second = catch(slug, cache_root=cache, source_url=str(npm_app), now=NOW)
    assert second.updated is True
    assert second.sha == first.sha

    forced = catch(slug, CatchOptions(force=True), cache_root=cache, source_url=str(npm_app))
    assert forced.updated is False
    assert forced.sha == first.sha


class _FakeGitHub:
    def get(self, path: str, *, allow_missing: bool = False) -> object:
        if path.startswith("search/issues"):
            return {"items": []}
        return [
            {"number": 2, "title": "Second", "state": "open", "labels": [], "html_url": "u2"},
            {"number": 1, "title": "First", "state": "closed", "labels": [], "html_url": "u1"},
        ]


def test_catch_can_fetch_issues(npm_app: Path, tmp_path: Path) -> None:
    slug = Slug("example", "crab-cove")
    cache = tmp_path / "cache"
    result = catch(
        slug,
        CatchOptions(issues=10),
        cache_root=cache,
        source_url=str(npm_app),
        github=_FakeGitHub(),  # type: ignore[arg-type]
    )
    assert result.issues_fetched == 2
    lines = (prey_paths(slug, cache).api / "issues.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["number"] == 2
    recorded = json.loads(prey_paths(slug, cache).catch_file.read_text(encoding="utf-8"))
    assert recorded["issues_fetched"] == 2


def test_prey_clones_never_fetch_lfs_content() -> None:
    """The one setting a later size preflight would rest on.

    `crab sniff` warns from the GitHub API `size` field, which counts the packed git objects and
    not LFS content, and GitHub allows a single LFS object of 2-5 GB. Cloning a working tree with
    smudging on therefore has no upper bound derivable from anything the crab knows before it
    starts. The crab has no use for the blobs either way: miners read files as data and nothing
    is ever built or run, so a pointer says as much as the object it stands in for.
    """
    assert git_env()["GIT_LFS_SKIP_SMUDGE"] == "1"


@pytest.mark.parametrize(
    ("shallow", "since"), [(False, None), (True, None), (True, "90d"), (True, "2100-01-01")]
)
@pytest.mark.parametrize("delete_old_branch", [False, True])
def test_cached_catch_follows_changed_remote_default_and_digest_tree(
    tmp_path: Path, shallow: bool, since: str | None, delete_old_branch: bool
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    fixture_git(source, "init", "-b", "main")
    (source / "README.md").write_text("# Main tree\n", encoding="utf-8")
    fixture_git(source, "add", ".")
    fixture_git(source, "commit", "-m", "feat: initial tree", date="2026-01-01T12:00:00Z")
    remote = tmp_path / "remote.git"
    fixture_git(tmp_path, "clone", "--bare", str(source), str(remote))
    slug = Slug("example", "changing-default")
    cache = tmp_path / "cache"
    options = CatchOptions(shallow=shallow, since=since)
    now = datetime(2026, 10, 7, tzinfo=UTC)
    first = catch(slug, options, cache_root=cache, source_url=remote.as_uri(), now=now)

    fixture_git(source, "checkout", "-b", "next")
    (source / "next.py").write_text("NEXT_TREE = True\n", encoding="utf-8")
    fixture_git(source, "add", ".")
    fixture_git(source, "commit", "-m", "feat: new default tree", date="2026-10-02T12:00:00Z")
    fixture_git(source, "push", str(remote), "next")
    fixture_git(remote, "symbolic-ref", "HEAD", "refs/heads/next")
    if delete_old_branch:
        fixture_git(remote, "update-ref", "-d", "refs/heads/main")

    second = catch(slug, options, cache_root=cache, source_url=remote.as_uri(), now=now)
    repo = Path(second.repo_dir)
    assert second.updated and second.default_branch == "next"
    assert second.sha == fixture_git(source, "rev-parse", "HEAD").strip()
    assert second.sha != first.sha
    assert second.shallow is shallow
    assert second.history_window_applied is (since == "90d" if since else None)
    assert fixture_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip().endswith("/next")
    if shallow:
        assert fixture_git(repo, "config", "--get", "remote.origin.fetch").strip() == (
            "+refs/heads/next:refs/remotes/origin/next"
        )
    digest = run_digest(
        Target(path=repo),
        DigestOptions(out=tmp_path / "digest", cache_root=cache, now=NOW, miners=["inventory"]),
    )
    inventory = json.loads((digest.out_dir / "inventory.json").read_text(encoding="utf-8"))
    assert digest.manifest["prey"]["sha"] == second.sha
    assert any(entry["path"] == "next.py" for entry in inventory["top_level"])


def test_failed_remote_probe_preserves_cached_tree_and_record(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    fixture_git(source, "init", "-b", "main")
    (source / "README.md").write_text("# Last valid tree\n", encoding="utf-8")
    fixture_git(source, "add", ".")
    fixture_git(source, "commit", "-m", "feat: initial tree")
    remote = tmp_path / "remote.git"
    fixture_git(tmp_path, "clone", "--bare", str(source), str(remote))
    slug = Slug("example", "unavailable-default")
    cache = tmp_path / "cache"
    opts = CatchOptions(shallow=True)
    first = catch(slug, opts, cache_root=cache, source_url=remote.as_uri())
    paths = prey_paths(slug, cache)
    original_record = paths.catch_file.read_bytes()
    original_config = (paths.repo / ".git" / "config").read_bytes()
    fixture_git(paths.repo, "remote", "set-url", "origin", (tmp_path / "missing.git").as_uri())
    failed_config = (paths.repo / ".git" / "config").read_bytes()

    with pytest.raises(ExternalCommandError, match="could not refresh") as error:
        catch(slug, opts, cache_root=cache, source_url=remote.as_uri())

    assert error.value.hint and "preserved" in error.value.hint
    assert paths.catch_file.read_bytes() == original_record
    assert (paths.repo / "README.md").read_text(encoding="utf-8") == "# Last valid tree\n"
    assert fixture_git(paths.repo, "rev-parse", "HEAD").strip() == first.sha
    assert (paths.repo / ".git" / "config").read_bytes() == failed_config
    assert original_config != failed_config


def test_changed_clone_policy_honours_requested_history(npm_app: Path, tmp_path: Path) -> None:
    slug = Slug("example", "bounds")
    url = npm_app.as_uri()  # local-path clones ignore depth; file transport does not
    cache = tmp_path / "cache"
    full = catch(slug, cache_root=cache, source_url=url)
    shallow = catch(slug, CatchOptions(shallow=True), cache_root=cache, source_url=url)
    assert not full.shallow and shallow.shallow and full.sha == shallow.sha
    restored = catch(slug, cache_root=cache, source_url=url)
    assert not restored.shallow


def test_invalid_since_and_failed_reclone_preserve_last_valid_clone(
    npm_app: Path, tmp_path: Path
) -> None:
    slug = Slug("example", "preserve")
    cache = tmp_path / "cache"
    first = catch(slug, cache_root=cache, source_url=str(npm_app))
    metadata = prey_paths(slug, cache).catch_file.read_bytes()
    with pytest.raises(UsageError):
        catch(
            slug,
            CatchOptions(force=True, since="invalid"),
            cache_root=cache,
            source_url=str(npm_app),
        )
    with pytest.raises(ExternalCommandError):
        catch(
            slug, CatchOptions(force=True), cache_root=cache, source_url=str(tmp_path / "missing")
        )
    assert prey_paths(slug, cache).catch_file.read_bytes() == metadata
    assert (prey_paths(slug, cache).repo / "package.json").is_file()
    assert len(first.sha) == 40


def test_stale_prey_uses_a_tree_snapshot_on_clone_and_refresh(
    npm_app: Path, tmp_path: Path
) -> None:
    slug = Slug("example", "stale")
    opts = CatchOptions(shallow=True, since="90d")
    now = datetime(2026, 10, 2, tzinfo=UTC)
    cache = tmp_path / "cache"
    first = catch(slug, opts, cache_root=cache, source_url=npm_app.as_uri(), now=now)
    assert first.shallow and first.history_window_applied is False
    second = catch(slug, opts, cache_root=cache, source_url=npm_app.as_uri(), now=now)
    assert second.updated and second.sha == first.sha
    assert second.history_window_applied is False
