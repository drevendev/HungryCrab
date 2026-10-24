"""API publication and whole-entry removal must not affect unrelated files or clones."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.sniff import sniff


@pytest.mark.parametrize("operation", ["sniff", "issues"])
def test_api_publication_does_not_truncate_a_hardlinked_outside_file(
    operation: str, npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    api = prey_paths(slug, cache).api
    api.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"unrelated private file")
    output = api / ("repo.json" if operation == "sniff" else "issues.jsonl")
    os.link(outside, output)
    client = SimpleNamespace(
        transport="fixture",
        token=None,
        repo=lambda _: {},
        languages=lambda _: {},
        get=lambda *_a, **_k: [],
    )
    if operation == "sniff":
        sniff(slug, cache_root=cache, client=client)  # type: ignore[arg-type]
    else:
        catch(
            slug,
            CatchOptions(wiki=False, issues=1),
            cache_root=cache,
            source_url=str(npm_app),
            github=client,
            channels_only=True,
        )  # type: ignore[arg-type]
    assert outside.read_bytes() == b"unrelated private file"
    assert not output.samefile(outside)


@pytest.mark.parametrize(
    "name",
    [
        "repo.json",
        "languages.json",
        "sniff.json",
        "issues.jsonl",
        "discussions.json",
        "reviews.json",
        "runs.json",
    ],
)
def test_api_output_links_are_refused_before_any_cache_change(
    name: str, npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    paths.api.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"unrelated private file")
    try:
        (paths.api / name).symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks unavailable")
    before = {
        p.relative_to(paths.root).as_posix(): p.read_bytes()
        for p in paths.root.rglob("*")
        if p.is_file()
    }
    client = SimpleNamespace(
        transport="fixture",
        token=None,
        repo=lambda *_: pytest.fail("provider reached"),
        get=lambda *_a, **_k: pytest.fail("provider reached"),
    )
    with pytest.raises(CrabError):
        if name in {"repo.json", "languages.json", "sniff.json"}:
            sniff(slug, cache_root=cache, client=client)  # type: ignore[arg-type]
        else:
            options = CatchOptions(wiki=False, **{name.split(".")[0]: 1})
            catch(
                slug,
                options,
                cache_root=cache,
                source_url=str(npm_app),
                github=client,
                channels_only=True,
            )  # type: ignore[arg-type]
    assert outside.read_bytes() == b"unrelated private file"
    assert {
        p.relative_to(paths.root).as_posix(): p.read_bytes()
        for p in paths.root.rglob("*")
        if p.is_file()
    } == before


@pytest.mark.parametrize("fault", ["unmarked", "retargeted"])
def test_cache_rm_refuses_an_unrelated_wiki_checkout(
    fault: str, npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    GitRunner(tmp_path).run("clone", "--quiet", str(npm_app), str(paths.wiki))
    (paths.wiki / "private.txt").write_bytes(b"uncommitted wiki work")
    if fault == "unmarked":
        GitRunner(paths.wiki).run("remote", "set-url", "origin", slug.wiki_clone_url)
    else:
        (paths.wiki / ".git/crab-catch.json").write_text(
            json.dumps({"schema": 1, "url": slug.wiki_clone_url}), encoding="utf-8"
        )
    before = {
        p.relative_to(paths.root).as_posix(): p.read_bytes()
        for p in paths.root.rglob("*")
        if p.is_file()
    }
    assert main(["--cache-dir", str(cache), "cache", "rm", str(slug)]) == 1
    assert {
        p.relative_to(paths.root).as_posix(): p.read_bytes()
        for p in paths.root.rglob("*")
        if p.is_file()
    } == before


@pytest.mark.parametrize("legacy", [False, True])
def test_cache_rm_accepts_a_legitimately_acquired_wiki(
    legacy: bool, npm_app: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, cache_root=cache, source_url=str(npm_app), wiki_source_url=str(npm_app))
    paths = prey_paths(slug, cache)
    if legacy:
        (paths.wiki / ".git/crab-catch.json").unlink()
    assert main(["--cache-dir", str(cache), "cache", "rm", str(slug)]) == 0
    assert not paths.root.exists()


def test_api_file_reparse_metadata_is_refused(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    output = prey_paths(slug, cache).api / "repo.json"
    output.parent.mkdir()
    output.write_bytes(b"preserve")
    real_lstat = Path.lstat

    def reparse(path: Path) -> os.stat_result | SimpleNamespace:
        info = real_lstat(path)
        return (
            SimpleNamespace(st_mode=info.st_mode, st_file_attributes=1024)
            if path == output
            else info
        )

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(CrabError, match="reparse"):
        sniff(slug, cache_root=cache)
    assert output.read_bytes() == b"preserve"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction")
def test_api_directory_junction_cannot_redirect_sniff(npm_app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    slug = Slug("fixture", "identity")
    catch(slug, CatchOptions(wiki=False), cache_root=cache, source_url=str(npm_app))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "repo.json"
    sentinel.write_bytes(b"unrelated private file")
    api = prey_paths(slug, cache).api
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(api), str(outside)], capture_output=True, check=True
    )
    with pytest.raises(CrabError, match="reparse"):
        sniff(slug, cache_root=cache)
    assert sentinel.read_bytes() == b"unrelated private file"
