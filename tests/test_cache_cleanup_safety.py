"""Regression tests for conservative cache removal; no external prey is run."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.cli import main
from hungry_crab.errors import CrabError
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
    rmtree_force(target, cache_root=cache_root)
    assert not target.exists()
    assert physical.is_dir()


def test_link_inside_the_cache_root_is_refused(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    external = tmp_path / "external"
    victim = external / "owner" / "repo"
    victim.mkdir(parents=True)
    sentinel = victim / "human-notes.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    alias = cache_root / "github"
    _link_directory(alias, external, "symlink")

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
    recaught = catch(slug, cache_root=cache_root, source_url=str(npm_app))
    assert recaught.updated is False
    assert (paths.repo / ".git").is_dir()
    assert not (paths.repo / "interrupted-clone.txt").exists()
