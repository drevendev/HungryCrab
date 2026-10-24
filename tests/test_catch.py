from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fixture_builder import git as fixture_git

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.cli import main
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.errors import CrabError, ExternalCommandError, UsageError
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


@pytest.mark.parametrize(
    "text",
    ["3000y", "99999999999d", "4" * 4301 + "d", "4" * 4301 + "w"],
)
def test_parse_since_rejects_values_beyond_the_date_range(text: str) -> None:
    """A relative value that would reach before year 1, or a digit count beyond
    Python 3.11+'s integer-string limit, is a usage error, not a traceback."""
    with pytest.raises(UsageError, match="cannot parse --since"):
        parse_since(text, now=NOW)


@pytest.mark.parametrize("text", ["3000y", "99999999999d", "4" * 4301 + "d"])
@pytest.mark.parametrize("existing_cache", [False, True])
def test_since_overflow_is_exit_two_without_touching_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
    existing_cache: bool,
) -> None:
    cache = tmp_path / "cache"
    if existing_cache:
        cache.mkdir()
        (cache / "keep.json").write_bytes(b"preserve cached evidence")

    def no_git(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid --since reached Git")

    monkeypatch.setattr("hungry_crab.fetch.catch.GitRunner", no_git)
    assert main(["--cache-dir", str(cache), "catch", "example/prey", "--since", text]) == 2
    error = capsys.readouterr().err
    assert "cannot parse --since" in error and "use 2y, 6m, 90d" in error
    assert "Traceback" not in error
    if existing_cache:
        assert list(cache.iterdir()) == [cache / "keep.json"]
        assert (cache / "keep.json").read_bytes() == b"preserve cached evidence"
    else:
        assert not cache.exists()


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
    remote.rename(tmp_path / "unavailable.git")

    with pytest.raises(ExternalCommandError, match="could not refresh") as error:
        catch(slug, opts, cache_root=cache, source_url=remote.as_uri())

    assert error.value.hint and "preserved" in error.value.hint
    assert paths.catch_file.read_bytes() == original_record
    assert (paths.repo / "README.md").read_text(encoding="utf-8") == "# Last valid tree\n"
    assert fixture_git(paths.repo, "rev-parse", "HEAD").strip() == first.sha
    assert (paths.repo / ".git" / "config").read_bytes() == original_config


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
    with pytest.raises(CrabError, match="origin differs"):
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


@pytest.mark.parametrize("existing_metadata", [False, True])
def test_failed_clone_removes_partial_prey_without_losing_other_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_metadata: bool
) -> None:
    slug = Slug("example", "failed-prey")
    root = tmp_path / "cache"
    paths = prey_paths(slug, root)
    if existing_metadata:
        paths.root.mkdir(parents=True)
        (paths.root / "keep.json").write_text("preserve", encoding="utf-8")

    class BrokenGit:
        def __init__(self, path: Path, timeout: int | None = None, **kwargs: object) -> None:
            self.path = path

        def run(self, *args: str) -> None:
            staged = Path(args[-1])
            staged.mkdir(parents=True, exist_ok=True)
            (staged / "partial").write_text("incomplete", encoding="utf-8")
            raise ExternalCommandError("simulated failed clone")

    monkeypatch.setattr("hungry_crab.fetch.catch.GitRunner", BrokenGit)
    expected = CrabError if existing_metadata else ExternalCommandError
    message = "no catch or sniff record" if existing_metadata else "simulated failed clone"
    with pytest.raises(expected, match=message):
        catch(slug, cache_root=root, source_url=str(tmp_path / "missing.git"), now=NOW)
    assert not paths.repo.exists()
    assert not paths.catch_file.exists()
    if existing_metadata:
        assert (paths.root / "keep.json").read_text(encoding="utf-8") == "preserve"
    else:
        assert not paths.root.exists()


def test_invalid_since_does_not_create_cache_directory(tmp_path: Path) -> None:
    slug = Slug("example", "failed-prey")
    root = tmp_path / "cache"
    with pytest.raises(UsageError, match="cannot parse --since"):
        catch(
            slug,
            CatchOptions(since="nonsense"),
            cache_root=root,
            source_url=str(tmp_path / "missing.git"),
            now=NOW,
        )
    assert not prey_paths(slug, root).root.exists()


def test_default_switch_preserves_date_bound_on_diverged_history(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    fixture_git(source, "init", "-b", "main")
    (source / "README.md").write_text("# Old root\n", encoding="utf-8")
    fixture_git(source, "add", ".")
    fixture_git(source, "commit", "-m", "feat: old root", date="2020-01-01T12:00:00Z")
    old_sha = fixture_git(source, "rev-parse", "HEAD").strip()
    (source / "main.py").write_text("MAIN = True\n", encoding="utf-8")
    fixture_git(source, "add", ".")
    fixture_git(source, "commit", "-m", "feat: main tip", date="2026-10-01T12:00:00Z")
    remote = tmp_path / "remote.git"
    fixture_git(tmp_path, "clone", "--bare", str(source), str(remote))
    options = CatchOptions(shallow=True, since="90d")
    slug = Slug("example", "diverged-default")
    now = datetime(2026, 10, 7, tzinfo=UTC)
    first = catch(slug, options, cache_root=tmp_path / "cache", source_url=remote.as_uri(), now=now)
    assert fixture_git(Path(first.repo_dir), "rev-list", "--count", "HEAD").strip() == "1"

    fixture_git(source, "checkout", "-b", "next", old_sha)
    (source / "next.py").write_text("NEXT = True\n", encoding="utf-8")
    fixture_git(source, "add", ".")
    fixture_git(source, "commit", "-m", "feat: diverged tip", date="2026-10-02T12:00:00Z")
    expected = fixture_git(source, "rev-parse", "HEAD").strip()
    fixture_git(source, "push", str(remote), "next")
    fixture_git(remote, "symbolic-ref", "HEAD", "refs/heads/next")
    fixture_git(remote, "update-ref", "-d", "refs/heads/main")

    second = catch(
        slug, options, cache_root=tmp_path / "cache", source_url=remote.as_uri(), now=now
    )
    assert second.sha == expected
    assert second.default_branch == "next"
    assert second.history_window_applied is True
    assert fixture_git(Path(second.repo_dir), "rev-list", "--count", "HEAD").strip() == "1"
    assert fixture_git(Path(second.repo_dir), "log", "--format=%cs", "HEAD").splitlines() == [
        "2026-10-02"
    ]


class _AnonymousGitHub(_FakeGitHub):
    token: str | None = None


def test_a_failed_wiki_probe_never_fails_the_catch(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without credentials GitHub answers a probe for a missing wiki with a login prompt."""
    slug = Slug("example", "no-wiki")
    cache = tmp_path / "cache"
    monkeypatch.setattr(Slug, "clone_url", property(lambda _: str(npm_app)))

    def prompt(*_: object, **__: object) -> dict[str, object]:
        raise ExternalCommandError(
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled"
        )

    monkeypatch.setattr("hungry_crab.fetch.catch.catch_wiki", prompt)
    client = _AnonymousGitHub()
    result = catch(slug, cache_root=cache, github=client)  # type: ignore[arg-type]
    assert result.wiki is not None and result.wiki["status"] == "unavailable"
    assert "could not read Username" in str(result.wiki["reason"])
    recorded = json.loads(prey_paths(slug, cache).catch_file.read_text(encoding="utf-8"))
    assert recorded["wiki"]["status"] == "unavailable"
    marker = prey_paths(slug, cache).repo / ".git" / "crab-test-marker"
    marker.write_text("same clone", encoding="utf-8")
    again = catch(slug, cache_root=cache, github=client)  # type: ignore[arg-type]
    assert again.updated and marker.is_file(), "the next catch refreshes the recorded clone"


def test_an_explicit_wiki_source_still_fails_loudly(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> dict[str, object]:
        raise ExternalCommandError("fatal: unable to access the wiki")

    monkeypatch.setattr("hungry_crab.fetch.catch.catch_wiki", broken)
    with pytest.raises(ExternalCommandError):
        catch(
            Slug("example", "wiki-source"),
            cache_root=tmp_path / "cache",
            source_url=str(npm_app),
            wiki_source_url=str(tmp_path / "wiki"),
        )


def test_a_relative_since_refreshes_on_the_next_day(npm_app: Path, tmp_path: Path) -> None:
    """`--since 90d` names a new date every day; the clone must not be replaced daily."""
    slug = Slug("example", "daily")
    cache = tmp_path / "cache"
    options = CatchOptions(since="20y")
    url = npm_app.as_uri()
    catch(slug, options, cache_root=cache, source_url=url, now=datetime(2026, 10, 2, tzinfo=UTC))
    marker = prey_paths(slug, cache).repo / ".git" / "crab-test-marker"
    marker.write_text("same clone", encoding="utf-8")
    second = catch(
        slug, options, cache_root=cache, source_url=url, now=datetime(2026, 10, 3, tzinfo=UTC)
    )
    assert second.updated and marker.is_file()
    recorded = json.loads(prey_paths(slug, cache).catch_file.read_text(encoding="utf-8"))
    assert recorded["clone_policy"] == {"shallow": False, "since": "20y"}


def test_a_clone_without_a_policy_record_is_refreshed_not_replaced(
    npm_app: Path, tmp_path: Path
) -> None:
    slug = Slug("example", "legacy")
    cache = tmp_path / "cache"
    catch(slug, cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    record = json.loads(paths.catch_file.read_text(encoding="utf-8"))
    del record["clone_policy"]  # a cache written before the policy was recorded
    paths.catch_file.write_text(json.dumps(record), encoding="utf-8")
    marker = paths.repo / ".git" / "crab-test-marker"
    marker.write_text("same clone", encoding="utf-8")
    assert catch(slug, cache_root=cache, source_url=str(npm_app)).updated
    paths.catch_file.unlink()  # a catch that failed after cloning left no record at all
    with pytest.raises(CrabError, match="no catch or sniff record"):
        catch(slug, cache_root=cache, source_url=str(npm_app))
    assert marker.is_file()


def test_channels_only_keeps_the_cached_commit(npm_app: Path, tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    fixture_git(tmp_path, "clone", "--quiet", str(npm_app), str(upstream))
    slug = Slug("example", "pinned")
    cache = tmp_path / "cache"
    first = catch(slug, cache_root=cache, source_url=str(upstream))
    (upstream / "later.txt").write_text("upstream moved on\n", encoding="utf-8")
    fixture_git(upstream, "add", "later.txt")
    fixture_git(upstream, "commit", "--quiet", "-m", "later")
    sampled = catch(
        slug,
        CatchOptions(issues=2),
        cache_root=cache,
        source_url=str(upstream),
        github=_FakeGitHub(),  # type: ignore[arg-type]
        channels_only=True,
    )
    assert sampled.sha == first.sha and not sampled.updated
    assert sampled.issues_fetched == 2
    recorded = json.loads(prey_paths(slug, cache).catch_file.read_text(encoding="utf-8"))
    assert recorded["clone_policy"] == {"shallow": False, "since": None}
    assert catch(slug, cache_root=cache, source_url=str(upstream)).sha != first.sha
