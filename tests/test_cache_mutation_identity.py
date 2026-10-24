"""Real synthetic clones must prove provenance before catch can change existing data."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.fetch.catch import CatchOptions, catch, catch_wiki
from hungry_crab.fetch.git import GitRunner


def _snapshot(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("operation", ["refresh", "force", "policy", "channels", "remove"])
@pytest.mark.parametrize("fault", ["missing", "corrupt", "wrong_slug", "origin", "two_origins"])
def test_refused_mutation_preserves_all_checkout_and_metadata_bytes(
    npm_app: Path, tmp_path: Path, operation: str, fault: str
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    (paths.repo / "uncommitted.txt").write_text("private unsaved work", encoding="utf-8")
    if fault in {"missing", "corrupt", "wrong_slug"}:
        paths.catch_file.unlink()
        if fault == "corrupt":
            paths.catch_file.write_bytes(b"not JSON")
        elif fault == "wrong_slug":
            paths.catch_file.write_text('{"slug":"someone/else"}', encoding="utf-8")
    else:
        git = GitRunner(paths.repo)
        git.run(
            "config",
            "--add" if fault == "two_origins" else "--replace-all",
            "remote.origin.url",
            str(tmp_path / "unrelated-source"),
        )
    before = _snapshot(paths.root)
    if operation == "remove":
        assert main(["--cache-dir", str(cache), "cache", "rm", str(slug)]) == 1
    else:
        with pytest.raises(CrabError):
            catch(
                slug,
                CatchOptions(force=operation == "force", shallow=operation == "policy", wiki=False),
                cache_root=cache,
                source_url=str(npm_app),
                channels_only=operation == "channels",
            )
    assert _snapshot(paths.root) == before
    assert not list(paths.root.glob(".repo-*"))


def test_an_unowned_lookalike_is_not_adopted_by_a_forced_catch(
    npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "checkouts"
    slug = Slug("fixture", "identity")
    paths = prey_paths(slug, cache)
    paths.root.mkdir(parents=True)
    GitRunner(paths.root).run("clone", "--quiet", str(npm_app), str(paths.repo))
    (paths.repo / "uncommitted.txt").write_text("private work", encoding="utf-8")
    before = _snapshot(paths.root)
    with pytest.raises(CrabError, match="no catch or sniff record"):
        catch(slug, CatchOptions(force=True, wiki=False), cache_root=cache, source_url=str(npm_app))
    assert _snapshot(paths.root) == before


def test_a_valid_legacy_catch_record_still_proves_the_clone(npm_app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    first = catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    (paths.repo / ".git/crab-catch.json").unlink()
    second = catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    assert second.updated and first.sha == second.sha
    assert json.loads(paths.catch_file.read_text())["slug"] == str(slug)


def test_refresh_never_fetches_an_additional_unverified_remote(
    npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    first = catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    git = GitRunner(prey_paths(slug, cache).repo)
    git.run("remote", "add", "unrelated", str(tmp_path / "must-not-be-contacted"))
    second = catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    assert second.updated and first.sha == second.sha


@pytest.mark.parametrize("kind", ["include", "rewrite", "environment"])
def test_effective_remote_overrides_cannot_bypass_identity(
    kind: str, npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    git = GitRunner(paths.repo)
    secret_url = "https://user:private-credential@example.invalid/unrelated.git"
    if kind == "include":
        extra = tmp_path / "included.config"
        extra.write_text(f'[remote "origin"]\nurl = {secret_url}\n', encoding="utf-8")
        git.run("config", "include.path", str(extra))
    elif kind == "rewrite":
        git.run("config", f"url.{secret_url}.insteadOf", str(npm_app))
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.url")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", secret_url)
    before = _snapshot(paths.root)
    with pytest.raises(CrabError, match="effective origin") as error:
        catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    assert "private-credential" not in str(error.value) + str(error.value.hint)
    assert _snapshot(paths.root) == before


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_a_cache_clone_alias_is_refused_before_mutation(
    kind: str, npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    external = tmp_path / "outside-checkout"
    paths.repo.rename(external)
    if kind == "junction":
        if sys.platform != "win32":
            pytest.skip("Windows junction")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(paths.repo), str(external)],
            capture_output=True,
            check=True,
        )
    else:
        try:
            paths.repo.symlink_to(external, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks unavailable")
    before = _snapshot(external)
    with pytest.raises(CrabError, match="link or reparse"):
        catch(slug, CatchOptions(force=True, wiki=False), cache_root=cache, source_url=str(npm_app))
    assert _snapshot(external) == before


@pytest.mark.parametrize("fault", ["marker", "origin"])
def test_standalone_wiki_replacement_requires_ownership_and_identity(
    fault: str, npm_app: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "wiki"
    catch_wiki(destination, str(npm_app))
    if fault == "marker":
        (destination / ".git/crab-catch.json").unlink()
    else:
        GitRunner(destination).run("remote", "set-url", "origin", str(tmp_path / "other"))
    before = _snapshot(destination)
    with pytest.raises(CrabError):
        catch_wiki(destination, str(npm_app))
    assert _snapshot(destination) == before


@pytest.mark.parametrize("fault", ["worktree", "git_dir"])
def test_external_git_locations_cannot_redirect_a_verified_origin(
    fault: str, npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    external = tmp_path / "outside"
    GitRunner(tmp_path).run("clone", "--quiet", str(npm_app), str(external))
    (external / "private.txt").write_text("preserve unrelated checkout", encoding="utf-8")
    if fault == "worktree":
        GitRunner(paths.repo).run("config", "core.worktree", str(external))
    else:
        monkeypatch.setenv("GIT_DIR", str(external / ".git"))
    before = _snapshot(paths.root)
    outside = _snapshot(external)
    with pytest.raises(CrabError, match="external Git metadata or working tree"):
        catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    assert _snapshot(paths.root) == before and _snapshot(external) == outside
