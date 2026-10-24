"""Regression tests for conservative cache removal; no external prey is run."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.cli import main
from hungry_crab.errors import CrabError, ExternalCommandError
from hungry_crab.fetch import catch as catch_module
from hungry_crab.fetch.catch import CatchOptions, catch, rmtree_force


def _link_directory(link: Path, target: Path, kind: str) -> None:
    """Point ``link`` at ``target`` through a symlink or a native Windows junction."""
    if kind == "junction":
        if sys.platform != "win32":
            pytest.skip("junctions exist only on Windows")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (
            f"Windows junction creation unexpectedly failed: {completed.stderr[:300]}"
        )
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable here")


def _record(prey_root: Path, slug: str, name: str = "catch.json") -> None:
    """The record a catch (``catch.json``) or a sniff (``api/sniff.json``) leaves behind."""
    record = prey_root / name
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"slug": slug}) + "\n", encoding="utf-8")


class RefusedIssues:
    """A provider whose issue listing is refused, as a rate limit or a missing scope does."""

    token = None
    transport = "https"

    def get(self, path: str, *, allow_missing: bool = False) -> object:
        raise ExternalCommandError(f"GET {path} failed with HTTP 403")


def test_readonly_retry_never_chmods_an_outside_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"private file")
    outside.chmod(stat.S_IREAD)
    before = stat.S_IMODE(outside.stat().st_mode)
    staged = tmp_path / "staged"
    staged.mkdir()
    linked = staged / "linked.txt"
    os.link(outside, linked)

    def failure(_path: Path, **handlers: object) -> None:
        handler = handlers.get("onexc") or handlers.get("onerror")
        assert callable(handler)
        handler(lambda _: None, str(linked), PermissionError(13, "readonly"))

    monkeypatch.setattr(catch_module.shutil, "rmtree", failure)
    monkeypatch.setattr(catch_module, "_CHMOD_NOFOLLOW", True)
    try:
        with pytest.raises(CrabError, match="hardlinked"):
            rmtree_force(staged, cache_root=tmp_path)
        assert outside.read_bytes() == b"private file"
        assert stat.S_IMODE(outside.stat().st_mode) == before
    finally:
        outside.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_a_catch_that_fails_after_its_clone_can_still_be_removed(
    npm_app: Path, tmp_path: Path
) -> None:
    cache_root = tmp_path / "cache"
    slug = Slug("me", "project")
    paths = prey_paths(slug, cache_root)
    with pytest.raises(ExternalCommandError, match="HTTP 403"):
        catch(
            slug,
            CatchOptions(issues=10, wiki=False),
            cache_root=cache_root,
            source_url=str(npm_app),
            github=RefusedIssues(),  # type: ignore[arg-type]
        )
    assert (paths.repo / ".git").is_dir()
    record = json.loads(paths.catch_file.read_text(encoding="utf-8"))
    assert record == {"slug": "me/project", "status": "incomplete"}

    assert main(["--cache-dir", str(cache_root), "cache", "rm", "me/project"]) == 0
    assert not paths.root.exists()


def test_a_provisional_record_is_completed_by_the_next_catch(npm_app: Path, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    slug = Slug("me", "project")
    paths = prey_paths(slug, cache_root)
    with pytest.raises(ExternalCommandError):
        catch(
            slug,
            CatchOptions(issues=10, wiki=False),
            cache_root=cache_root,
            source_url=str(npm_app),
            github=RefusedIssues(),  # type: ignore[arg-type]
        )
    caught = catch(slug, CatchOptions(wiki=False), cache_root=cache_root, source_url=str(npm_app))
    record = json.loads(paths.catch_file.read_text(encoding="utf-8"))
    assert "status" not in record and record["sha"] == caught.sha


def test_a_failed_first_clone_leaves_no_record_behind(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    slug = Slug("me", "project")
    missing = tmp_path / "no-such-repository"
    with pytest.raises(ExternalCommandError):
        catch(slug, CatchOptions(wiki=False), cache_root=cache_root, source_url=str(missing))
    assert not prey_paths(slug, cache_root).root.exists()


def test_a_catch_never_claims_a_directory_it_did_not_create(npm_app: Path, tmp_path: Path) -> None:
    cache_root = tmp_path / "checkouts"
    root = prey_paths(Slug("me", "project"), cache_root).root
    (root / "notes").mkdir(parents=True)
    with pytest.raises(CrabError, match="no catch or sniff record"):
        catch(
            Slug("me", "project"),
            CatchOptions(issues=10, wiki=False),
            cache_root=cache_root,
            source_url=str(npm_app),
            github=RefusedIssues(),  # type: ignore[arg-type]
        )
    assert not (root / "catch.json").exists()
    with pytest.raises(CrabError, match="no catch or sniff record"):
        rmtree_force(root, cache_root=cache_root)


def test_a_forced_catch_that_cannot_move_the_old_clone_is_an_error(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows an open file blocks the rename of its directory (WinError 5)."""
    cache_root = tmp_path / "cache"
    slug = Slug("me", "project")
    paths = prey_paths(slug, cache_root)
    catch(slug, CatchOptions(wiki=False), cache_root=cache_root, source_url=str(npm_app))
    real_rename = Path.rename

    def held_open(self: Path, target: Path) -> Path:
        if self == paths.repo:
            raise PermissionError(13, "Access is denied", str(self))
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", held_open)
    with pytest.raises(CrabError, match="cannot replace") as error:
        catch(
            slug,
            CatchOptions(force=True, wiki=False),
            cache_root=cache_root,
            source_url=str(npm_app),
        )
    assert error.value.hint is not None and "Close programs" in error.value.hint
    assert (paths.repo / ".git").is_dir()
    assert sorted(p.name for p in paths.root.iterdir()) == ["catch.json", "repo"]


def test_a_forced_catch_with_a_file_held_open_is_an_error(npm_app: Path, tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("only Windows refuses to rename a directory with a file held open")
    cache_root = tmp_path / "cache"
    slug = Slug("me", "project")
    paths = prey_paths(slug, cache_root)
    catch(slug, CatchOptions(wiki=False), cache_root=cache_root, source_url=str(npm_app))
    held = next(path for path in paths.repo.iterdir() if path.is_file())
    with held.open("rb"), pytest.raises(CrabError, match="cannot replace"):
        catch(
            slug,
            CatchOptions(force=True, wiki=False),
            cache_root=cache_root,
            source_url=str(npm_app),
        )
    assert (paths.repo / ".git").is_dir()


@pytest.mark.parametrize("nofollow", [True, False])
def test_the_retry_never_changes_a_mode_through_a_link(
    nofollow: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failed entry can turn into a link between its check and the chmod."""
    cache_root = tmp_path / "cache"
    staged = cache_root / ".repo-staged"
    staged.mkdir(parents=True)
    failed = staged / "object"
    failed.write_text("x", encoding="utf-8")
    chmods: list[dict[str, object]] = []
    retried: list[str] = []

    def record_chmod(_path: Path, _mode: int, **options: object) -> None:
        chmods.append(options)

    def fail_once(path: Path, **handlers: object) -> None:
        handler = handlers.get("onexc") or handlers.get("onerror")
        assert callable(handler)
        handler(retried.append, str(failed), PermissionError(13, "denied"))

    monkeypatch.setattr(catch_module, "_CHMOD_NOFOLLOW", nofollow)
    monkeypatch.setattr(Path, "chmod", record_chmod)
    monkeypatch.setattr(catch_module.shutil, "rmtree", fail_once)
    rmtree_force(staged, cache_root=cache_root)
    assert retried == [str(failed)]
    if nofollow:
        assert chmods == [{"follow_symlinks": False}]
    else:
        # Windows before 3.13 changes the entry itself; elsewhere chmod would follow a link.
        assert chmods == ([{}] if sys.platform == "win32" else [])


def test_cache_rm_refuses_a_checkout_the_crab_did_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``CRAB_CACHE_DIR`` can be a folder of checkouts laid out as ``github/<owner>/<repo>``."""
    cache_root = tmp_path / "checkouts"
    checkout = prey_paths(Slug("me", "project"), cache_root).root
    (checkout / ".git").mkdir(parents=True)
    (checkout / "work.txt").write_text("uncommitted work", encoding="utf-8")

    assert main(["--cache-dir", str(cache_root), "cache", "rm", "me/project"]) == 1
    error = capsys.readouterr().err
    assert "entry is itself a working checkout" in error and "aside yourself" in error
    assert (checkout / "work.txt").read_text(encoding="utf-8") == "uncommitted work"


@pytest.mark.parametrize(
    "record",
    [
        {"slug": "someone/else"},
        {"name": "me/project"},
        ["me/project"],
    ],
)
def test_a_record_must_name_the_prey_it_is_found_in(record: object, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    root = prey_paths(Slug("me", "project"), cache_root).root
    (root / "repo").mkdir(parents=True)
    (root / "catch.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(CrabError, match="no catch or sniff record"):
        rmtree_force(root, cache_root=cache_root)
    assert (root / "repo").is_dir()


@pytest.mark.parametrize(
    "slug,name,recorded",
    [
        (Slug("me", "project"), "catch.json", "Me/Project"),
        (Slug("me", "project"), "api/sniff.json", "me/project"),
        (Slug("group/sub", "repo", "gitlab.com"), "catch.json", "gitlab.com/group/sub/repo"),
    ],
)
def test_cache_rm_removes_what_a_catch_or_sniff_recorded(
    slug: Slug, name: str, recorded: str, tmp_path: Path
) -> None:
    cache_root = tmp_path / "cache"
    root = prey_paths(slug, cache_root).root
    (root / "digests" / "x").mkdir(parents=True)
    _record(root, recorded, name)
    assert main(["--cache-dir", str(cache_root), "cache", "rm", str(slug)]) == 0
    assert not root.exists()


def test_a_directory_below_a_recorded_prey_is_the_crabs(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    root = prey_paths(Slug("me", "project"), cache_root).root
    (root / "repo" / ".git").mkdir(parents=True)
    _record(root, "me/project")
    rmtree_force(root / "repo", cache_root=cache_root)
    assert not (root / "repo").exists() and (root / "catch.json").is_file()


def test_an_open_file_ends_in_an_error_naming_what_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if sys.platform != "win32":
        pytest.skip("only Windows refuses to delete a file another handle holds open")
    cache_root = tmp_path / "cache"
    root = prey_paths(Slug("me", "project"), cache_root).root
    page = root / "digests" / "page.md"
    page.parent.mkdir(parents=True)
    _record(root, "me/project")
    with page.open("w", encoding="utf-8"):
        assert main(["--cache-dir", str(cache_root), "cache", "rm", "me/project"]) == 1
    error = capsys.readouterr().err
    assert f"what is left stays in {root}" in error
    assert "Close programs holding files" in error
    assert page.is_file()


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_a_link_inside_an_entry_is_removed_and_never_followed(kind: str, tmp_path: Path) -> None:
    external = tmp_path / "outside"
    external.mkdir()
    sentinel = external / "human-notes.txt"
    sentinel.write_bytes(b"never touch this file")
    sentinel.chmod(stat.S_IREAD)
    locked_mode = stat.S_IMODE(sentinel.stat().st_mode)
    cache_root = tmp_path / "cache"
    root = prey_paths(Slug("me", "project"), cache_root).root
    (root / "repo").mkdir(parents=True)
    _record(root, "me/project")
    try:
        _link_directory(root / "repo" / "outside-link", external, kind)
        rmtree_force(root, cache_root=cache_root)
        assert not root.exists()
        assert sentinel.read_bytes() == b"never touch this file"
        assert stat.S_IMODE(sentinel.stat().st_mode) == locked_mode
    finally:
        sentinel.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_cache_removal_never_chmods_external_symlink_target(tmp_path: Path) -> None:
    external = tmp_path / "outside"
    external.mkdir()
    sentinel = external / "human-notes.txt"
    sentinel.write_bytes(b"never touch this file")
    original_mode = stat.S_IMODE(sentinel.stat().st_mode)
    sentinel.chmod(stat.S_IREAD)
    locked_mode = stat.S_IMODE(sentinel.stat().st_mode)

    cache = tmp_path / "cache-owned"
    cache.mkdir()
    (cache / "normal.txt").write_text("disposable")
    link = cache / "outside-link"
    try:
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symlinks are unavailable here")
        rmtree_force(cache, cache_root=tmp_path)
        assert not cache.exists()
        assert sentinel.read_bytes() == b"never touch this file"
        assert stat.S_IMODE(sentinel.stat().st_mode) == locked_mode
    finally:
        sentinel.chmod(original_mode)


def test_refuse_removal_when_the_cache_path_is_a_link(tmp_path: Path) -> None:
    external = tmp_path / "real-human-directory"
    external.mkdir()
    (external / "important.txt").write_text("human", encoding="utf-8")
    link = tmp_path / "fake-cache"
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable here")
    with pytest.raises(CrabError, match="link/reparse"):
        rmtree_force(link, cache_root=tmp_path)
    assert (external / "important.txt").read_text(encoding="utf-8") == "human"


def test_locked_cache_failure_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache-owned"
    cache.mkdir()
    sentinel = cache / "locked.txt"
    sentinel.write_text("keep until lock clears", encoding="utf-8")

    def deny_removal(_path: Path, **_handlers: object) -> None:
        raise PermissionError("simulated open file")

    monkeypatch.setattr("hungry_crab.fetch.catch.shutil.rmtree", deny_removal)
    with pytest.raises(CrabError, match="cannot safely remove cached files") as error:
        rmtree_force(cache, cache_root=tmp_path)
    assert error.value.hint is not None and "Close programs" in error.value.hint
    assert sentinel.read_text(encoding="utf-8") == "keep until lock clears"


def test_refuse_the_cache_root_itself_and_paths_outside_it(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for target in (root, elsewhere, root / ".." / "elsewhere"):
        with pytest.raises(CrabError, match="not inside the cache"):
            rmtree_force(target, cache_root=root)
    assert root.is_dir()
    assert elsewhere.is_dir()


def test_cache_root_beneath_symlinked_parent_is_allowed(tmp_path: Path) -> None:
    physical = tmp_path / "physical-cache-disk"
    physical.mkdir()
    alias = tmp_path / "cache-alias"
    _link_directory(alias, physical, "symlink")

    cache_root = alias / "hungry-crab"
    target = cache_root / "github" / "owner" / "repo"
    target.mkdir(parents=True)
    (target / "cache.txt").write_text("disposable", encoding="utf-8")
    _record(target, "owner/repo")
    rmtree_force(target, cache_root=cache_root)
    assert not target.exists()
    assert physical.is_dir()


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_link_inside_the_cache_root_is_refused(kind: str, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    external = tmp_path / "external"
    victim = external / "owner" / "repo"
    victim.mkdir(parents=True)
    sentinel = victim / "human-notes.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    _record(victim, "owner/repo")
    alias = cache_root / "github"
    _link_directory(alias, external, kind)

    with pytest.raises(CrabError, match="link/reparse"):
        rmtree_force(alias / "owner" / "repo", cache_root=cache_root)
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_reparse_point_inside_the_cache_is_refused_on_every_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root = tmp_path / "cache"
    target = cache_root / "github" / "owner" / "repo"
    target.mkdir(parents=True)
    suspect = cache_root / "github"
    real_lstat = Path.lstat

    def wrapped_lstat(path: Path, *args: object, **kwargs: object) -> object:
        record = real_lstat(path, *args, **kwargs)
        if path == suspect:
            return SimpleNamespace(st_mode=record.st_mode, st_file_attributes=0x400)
        return record

    monkeypatch.setattr(Path, "lstat", wrapped_lstat)
    with pytest.raises(CrabError, match="link/reparse"):
        rmtree_force(target, cache_root=cache_root)
    assert target.is_dir()


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_every_cache_deletion_works_under_a_linked_cache_parent(
    kind: str, npm_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disk = tmp_path / "data-disk"
    disk.mkdir()
    home_cache = tmp_path / "home-cache"
    _link_directory(home_cache, disk, kind)
    cache_root = home_cache / "hungry-crab"
    slug = Slug("example", "crab-cove")
    paths = prey_paths(slug, cache_root)

    catch(slug, cache_root=cache_root, source_url=str(npm_app))
    forced = catch(slug, CatchOptions(force=True), cache_root=cache_root, source_url=str(npm_app))
    assert forced.updated is False
    assert (paths.repo / ".git").is_dir()

    assert main(["--cache-dir", str(cache_root), "cache", "rm", str(slug)]) == 0
    assert "removed" in capsys.readouterr().out
    assert not paths.root.exists()
    assert (disk / "hungry-crab" / "github").is_dir()

    paths.repo.mkdir(parents=True)
    (paths.repo / "interrupted-clone.txt").write_text("debris", encoding="utf-8")
    with pytest.raises(CrabError, match="no catch or sniff record"):
        catch(slug, cache_root=cache_root, source_url=str(npm_app))
    assert (paths.repo / "interrupted-clone.txt").read_text() == "debris"
