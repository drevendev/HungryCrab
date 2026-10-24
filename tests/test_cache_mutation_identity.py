"""Real synthetic clones must prove provenance before catch can change existing data."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.fetch.catch import CatchOptions, catch, catch_wiki
from hungry_crab.fetch.git import GitRunner
from hungry_crab.sniff import sniff


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
    # Build the fake credential at runtime so the repository's secret-scan corpus stays clean.
    secret_url = "https://user:" + "private-credential@example.invalid/unrelated.git"
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


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_git_object_store_alias_is_refused(kind: str, npm_app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    objects = paths.repo / ".git/objects"
    external = tmp_path / "outside-objects"
    objects.rename(external)
    if kind == "junction":
        if sys.platform != "win32":
            pytest.skip("Windows junction")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(objects), str(external)],
            capture_output=True,
            check=True,
        )
    else:
        try:
            objects.symlink_to(external, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks unavailable")
    before = _snapshot(external)
    with pytest.raises(CrabError, match="Git metadata contains a link or reparse"):
        catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    assert _snapshot(external) == before


def test_a_file_at_the_cache_entry_is_a_safe_refusal(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    path = prey_paths(slug, cache).root
    path.parent.mkdir(parents=True)
    path.write_bytes(b"unrelated file")
    with pytest.raises(CrabError, match=r"cache mutation path|cache directory"):
        catch(slug, CatchOptions(wiki=False), cache_root=cache)
    assert path.read_bytes() == b"unrelated file"


@pytest.mark.parametrize(
    "key",
    [
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_QUARANTINE_PATH",
        "GIT_TEMPLATE_DIR",
    ],
)
def test_inherited_git_paths_are_refused_even_before_a_first_clone(
    key: str, npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "outside"
    external.mkdir()
    (external / "private.txt").write_bytes(b"untouched")
    monkeypatch.setenv(key, str(external))
    cache = tmp_path / "cache"
    with pytest.raises(CrabError, match="external Git metadata or working tree"):
        catch(
            Slug("fixture", "identity"),
            CatchOptions(wiki=False),
            cache_root=cache,
            source_url=str(npm_app),
        )
    assert not cache.exists()
    assert _snapshot(external) == {"private.txt": b"untouched"}


def test_sniff_cannot_claim_an_unowned_existing_checkout(npm_app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    paths = prey_paths(slug, cache)
    paths.root.parent.mkdir(parents=True)
    GitRunner(tmp_path).run("clone", "--quiet", str(npm_app), str(paths.root))
    (paths.root / "private.txt").write_text("uncommitted work", encoding="utf-8")
    before = _snapshot(paths.root)
    client = SimpleNamespace(transport="fixture", repo=lambda _: {}, languages=lambda _: {})
    with pytest.raises(CrabError):
        sniff(slug, cache_root=cache, client=client)  # type: ignore[arg-type]
    assert _snapshot(paths.root) == before


def test_sniff_record_does_not_prove_ownership_of_an_existing_clone(
    npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    paths = prey_paths(slug, cache)
    paths.api.mkdir(parents=True)
    (paths.api / "sniff.json").write_text(json.dumps({"slug": str(slug)}), encoding="utf-8")
    GitRunner(tmp_path).run("clone", "--quiet", str(npm_app), str(paths.repo))
    (paths.repo / "private.txt").write_text("uncommitted work", encoding="utf-8")
    before = _snapshot(paths.root)
    with pytest.raises(CrabError, match="no crab ownership record"):
        catch(slug, CatchOptions(force=True, wiki=False), cache_root=cache, source_url=str(npm_app))
    assert _snapshot(paths.root) == before


def test_legacy_sniff_marker_cannot_authorize_deleting_a_human_checkout(
    npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    paths = prey_paths(slug, cache)
    paths.root.parent.mkdir(parents=True)
    GitRunner(tmp_path).run("clone", "--quiet", str(npm_app), str(paths.root))
    paths.api.mkdir()
    (paths.api / "sniff.json").write_text(json.dumps({"slug": str(slug)}), encoding="utf-8")
    before = _snapshot(paths.root)
    assert main(["--cache-dir", str(cache), "cache", "rm", str(slug)]) == 1
    assert _snapshot(paths.root) == before


def test_a_fresh_sniff_can_still_be_followed_by_catch(npm_app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    client = SimpleNamespace(transport="fixture", repo=lambda _: {}, languages=lambda _: {})
    sniff(slug, cache_root=cache, client=client)  # type: ignore[arg-type]
    result = catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    assert result.sha == GitRunner(npm_app).head_sha()


@pytest.mark.parametrize("record", ["api/sniff.json", "catch.json"])
def test_a_legacy_marker_cannot_authorize_removing_a_bare_repository(
    record: str, npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    paths = prey_paths(slug, cache)
    paths.root.parent.mkdir(parents=True)
    GitRunner(tmp_path).run("clone", "--bare", "--quiet", str(npm_app), str(paths.root))
    marker = paths.root / record
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"slug": str(slug)}), encoding="utf-8")
    before = _snapshot(paths.root)
    assert main(["--cache-dir", str(cache), "cache", "rm", str(slug)]) == 1
    assert _snapshot(paths.root) == before
