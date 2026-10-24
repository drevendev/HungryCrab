from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.errors import CrabError, ExternalCommandError
from hungry_crab.feeder import _check_digest
from hungry_crab.fetch.catch import catch
from hungry_crab.fetch.git import SAFE_CONFIG, GitRunner, git_env
from hungry_crab.miners.base import MineContext
from hungry_crab.miners.wiki import WikiMiner, _git_bytes


def _object(git: GitRunner, *args: str, data: bytes) -> str:
    result = subprocess.run(
        [git.exe, *SAFE_CONFIG, "-C", str(git.cwd), *args],
        input=data,
        capture_output=True,
        env=git_env(),
        check=True,
        timeout=30,
    )
    return result.stdout.decode().strip()


def _snapshot(root: Path, files: dict[str, bytes]) -> str:
    """Build a synthetic wiki through plumbing, including names Windows cannot check out."""
    root.mkdir(exist_ok=True)
    git = GitRunner(root)
    git.run("init", "--quiet")
    nodes: dict[str, Any] = {}
    for path, content in files.items():
        node = nodes
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = content

    def tree(node: dict[str, Any]) -> str:
        entries = []
        for name, content in sorted(node.items()):
            if isinstance(content, dict):
                mode, kind, oid = "040000", "tree", tree(content)
            else:
                mode, kind = "100644", "blob"
                oid = _object(git, "hash-object", "-w", "--stdin", data=content)
            entries.append(f"{mode} {kind} {oid}\t{name}\0".encode())
        return _object(git, "mktree", "-z", data=b"".join(entries))

    sha = git.run(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid.example",
        "commit-tree",
        tree(nodes),
        "-m",
        "docs: synthetic committed wiki",
    ).strip()
    git.run("update-ref", "HEAD", sha)
    return sha


def _context(root: Path, sha: str) -> MineContext:
    return MineContext(
        root=root,
        sha=sha,
        ref="HEAD",
        label="fixture/wiki",
        wiki_root=root,
        wiki_info={"status": "available", "sha": sha},
    )


def test_names_formats_and_byte_counts_come_from_git_objects(tmp_path: Path) -> None:
    files = {
        "Home.md": b"# Committed\r\n\r\nPrivate body stays in the Git object.\r\n",
        "Colon:name.md": b"# Colon\n",
        "Question?.md": b"# Question\n",
        "Star*.md": b"# Star\n",
        "directory./Nested.md": b"# Nested\n",
        **{
            f"Format{suffix}": b"# Structured name\n"
            for suffix in (".creole", ".rdoc", ".pod", ".adoc", ".mkdn", ".wiki")
        },
    }
    root = tmp_path / "objects"
    sha = _snapshot(root, files)
    result = WikiMiner().run(_context(root, sha))
    pages = {page["path"]: page for page in result.data["pages"]}
    assert set(pages) == set(files)
    assert result.data["coverage"]["healthy"]
    assert result.data["bytes"] == sum(map(len, files.values()))
    assert all(pages[path]["bytes"] == len(content) for path, content in files.items())
    assert pages["Format.mkdn"]["heading_count"] == 1
    assert pages["Format.creole"]["heading_count"] == 0
    assert "Private body" not in json.dumps(result.data)
    assert not (root / "Home.md").exists()


def test_recorded_commit_wins_over_later_head_and_working_tree(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    sha = _snapshot(root, {"Home.md": b"# Recorded\n"})
    _snapshot(root, {"Different.md": b"# Later head\n"})
    (root / "Home.md").write_text("# Private local edit\n", encoding="utf-8")
    result = WikiMiner().run(_context(root, sha))
    assert [p["path"] for p in result.data["pages"]] == ["Home.md"]
    assert result.data["pages"][0]["headings"] == [{"level": 1, "text": "Recorded"}]


def test_oversized_blob_is_counted_without_reading_its_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hungry_crab.miners import wiki

    root = tmp_path / "objects"
    sha = _snapshot(root, {"Large.md": b"x" * 20, "Small.md": b"# OK\n"})
    large = GitRunner(root).run("rev-parse", f"{sha}:Large.md").strip()
    actual = wiki._git_bytes

    def read(git: GitRunner, *args: str, limit: int) -> bytes:
        assert not (args[0] == "cat-file" and args[-1] == large), "oversized blob was read"
        return actual(git, *args, limit=limit)

    monkeypatch.setattr(wiki, "MAX_PAGE_BYTES", 8)
    monkeypatch.setattr(wiki, "_git_bytes", read)
    result = WikiMiner().run(_context(root, sha))
    assert not result.data["coverage"]["healthy"]
    assert result.data["coverage"]["oversized_pages"] == ["Large.md"]
    assert result.data["page_count"] == 2 and result.data["bytes"] == 25


def test_missing_blob_is_reported_as_visibility_loss(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    sha = _snapshot(root, {"Missing.md": b"# Missing\n", "Present.md": b"# Present\n"})
    oid = GitRunner(root).run("rev-parse", f"{sha}:Missing.md").strip()
    blob = root / ".git" / "objects" / oid[:2] / oid[2:]
    blob.chmod(0o600)  # Git marks loose objects read-only on Windows.
    blob.unlink()
    result = WikiMiner().run(_context(root, sha))
    assert not result.data["coverage"]["healthy"]
    assert result.data["coverage"]["unreadable_pages"] == ["Missing.md"]
    assert result.data["page_count"] == 2 and result.warnings


def test_blob_and_tree_pipes_enforce_byte_caps(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    sha = _snapshot(root, {"Page.md": b"# Larger than the synthetic cap\n"})
    git = GitRunner(root)
    oid = git.run("rev-parse", f"{sha}:Page.md").strip()
    for args in (("cat-file", "blob", oid), ("ls-tree", "-r", "-z", "-l", sha)):
        with pytest.raises(ExternalCommandError, match="read limits"):
            _git_bytes(git, *args, limit=8)


def test_dirty_wiki_checkout_does_not_change_commit_backed_digest(
    npm_app: Path, tmp_path: Path
) -> None:
    root = tmp_path / "objects"
    _snapshot(root, {"Home.md": b"# Committed\n"})
    options = DigestOptions(wiki_path=root, cache_root=tmp_path / "cache")
    first = run_digest(Target(path=npm_app), options)
    (root / "Home.md").write_text("# Private edit\n", encoding="utf-8")
    (root / "Untracked.md").write_text("# Private untracked page\n", encoding="utf-8")
    second = run_digest(Target(path=npm_app), options)
    assert second.cached and second.out_dir == first.out_dir
    assert first.manifest["wiki"]["source"] == "git-objects"
    assert "worktree" not in first.manifest["wiki"]


def test_present_wiki_becoming_missing_invalidates_cached_evidence(
    npm_app: Path, tmp_path: Path
) -> None:
    root = tmp_path / "wiki-source"
    _snapshot(root, {"Home.md": b"# Committed\n"})
    slug = Slug("fixture", "wiki-lifecycle")
    cache = tmp_path / "cache"
    catch(slug, cache_root=cache, source_url=str(npm_app), wiki_source_url=str(root))
    options = DigestOptions(cache_root=cache)
    first = run_digest(Target(slug=slug), options)
    assert not (prey_paths(slug, cache).wiki / "Home.md").exists()
    root.rename(tmp_path / "wiki-removed")
    acquired = catch(slug, cache_root=cache, source_url=str(npm_app), wiki_source_url=str(root))
    assert acquired.wiki and acquired.wiki["status"] == "missing"
    second = run_digest(Target(slug=slug), options)
    assert not second.cached and first.manifest["wiki"] != second.manifest["wiki"]
    wiki = json.loads((second.out_dir / "wiki.json").read_text(encoding="utf-8"))
    assert wiki["status"] == "missing" and not wiki["available"] and wiki["page_count"] == 0


def test_wiki_visibility_loss_blocks_feeder_export(npm_app: Path, tmp_path: Path) -> None:
    root = tmp_path / "objects"
    sha = _snapshot(root, {"Missing.md": b"# Missing\n"})
    oid = GitRunner(root).run("rev-parse", f"{sha}:Missing.md").strip()
    blob = root / ".git/objects" / oid[:2] / oid[2:]
    blob.chmod(0o600)
    blob.unlink()
    result = run_digest(
        Target(path=npm_app), DigestOptions(wiki_path=root, out=tmp_path / "digest")
    )
    with pytest.raises(CrabError, match="whole wiki"):
        _check_digest(result.out_dir, allow_loss=False)
