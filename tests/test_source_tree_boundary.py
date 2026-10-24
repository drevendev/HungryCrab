"""Local analysis must not borrow parent history or read ignored personal files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from fixture_builder import git
from helpers import read_json, write_tree

import hungry_crab.licensing.detect as license_detection
from hungry_crab.cache import Slug, Target
from hungry_crab.compare import compare_digests
from hungry_crab.digest import DigestOptions, DigestResult, prepare_context, run_digest
from hungry_crab.errors import CrabError
from hungry_crab.fetch.git import GitRunner
from hungry_crab.licensing import Relationship
from hungry_crab.maw import MawConfig, maw_slug, relationship_for


def _repo(root: Path, files: dict[str, str]) -> Path:
    write_tree(root, files)
    # Keep the fixture helper's empty config inside this repo, including for nested init.
    (root / ".git").mkdir()
    git(root, "init", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-m", "test: initial tree")
    return root


def _options(tmp_path: Path) -> DigestOptions:
    return DigestOptions(out=tmp_path / "out", cache_root=tmp_path / "cache", now=FIXED_NOW)


def test_subdirectory_never_borrows_parent_git_identity(tmp_path: Path) -> None:
    parent = _repo(tmp_path / "parent", {"outside.py": "x = 1\n", "sub/app.py": "y = 2\n"})
    options = _options(tmp_path)
    target = Target(path=parent / "sub")
    first = run_digest(target, options)
    assert first.manifest["prey"]["sha"].startswith("nogit-")
    assert first.manifest["prey"]["git_scope"] == "filesystem-only"
    assert first.manifest["prey"]["ref"] == "worktree"
    assert read_json(first, "history.json")["available"] is False
    assert read_json(first, "branches.json")["available"] is False
    assert not run_digest(target, options).cached
    write_tree(parent, {"outside.py": "x = 3\n"})
    git(parent, "commit", "-am", "test: outside change")
    changed = run_digest(target, options)
    assert not changed.cached
    assert changed.manifest["prey"]["sha"] == first.manifest["prey"]["sha"]


def test_wiki_subdirectory_cannot_read_parent_git_objects(tmp_path: Path) -> None:
    parent = _repo(tmp_path / "parent", {"private.md": "# Private\n", "sub/page.md": "# Page\n"})
    options = _options(tmp_path)
    options.wiki_path = parent / "sub"
    with pytest.raises(CrabError, match="repository root"):
        prepare_context(Target(path=parent), options)


def test_subdirectory_cannot_borrow_parent_publication_identity_or_owner_trust(
    tmp_path: Path,
) -> None:
    parent = _repo(tmp_path / "parent", {"sub/app.py": "x = 1\n"})
    git(parent, "remote", "add", "origin", "https://github.com/acme/parent")
    assert maw_slug(parent) == Slug.parse("acme/parent")
    assert maw_slug(parent / "sub") is None
    assert (
        relationship_for(Slug.parse("acme/prey"), MawConfig.load(parent / "sub"))
        == Relationship.FOREIGN
    )


def test_ignored_files_never_become_repository_facts(tmp_path: Path) -> None:
    root = _repo(
        tmp_path / "repo",
        {
            ".gitignore": ".python-version\nCLAUDE.local.md\n.claude/settings.local.json\n"
            ".venv/\n.pytest_cache/\n**/__pycache__/\n.github/dependabot.yml\n",
            "app.py": "x = 1\n",
        },
    )
    write_tree(
        root,
        {
            ".python-version": "3.12\n",
            "CLAUDE.local.md": "# PRIVATE_PERSONAL_HEADING\n",
            ".claude/settings.local.json": '{"permissions":{"allow":["Bash(ls)"]}}',
            ".venv/private.py": "PRIVATE_LOCAL_DEPENDENCY = 1\n",
            ".pytest_cache/private.json": '{"private":true}',
            "src/__pycache__/private.pyc": "binary\0bytes",
            ".github/dependabot.yml": "version: 2\nupdates: []\n",
            "draft.py": "y = 2\n",
        },
    )
    result = run_digest(Target(path=root), _options(tmp_path))
    traits = read_json(result, "traits.json")["traits"]
    assert traits["has_python_version_file"] is False
    assert traits["has_claude_md"] is False
    assert traits["has_claude_settings"] is False
    inventory = read_json(result, "inventory.json")
    assert inventory["files"] == 3
    assert inventory["dirs"] == 0
    assert inventory["binary_files"] == 0
    assert inventory["bytes"] == sum(
        (root / p).stat().st_size for p in (".gitignore", "app.py", "draft.py")
    )
    assert {row["path"] for row in inventory["top_level"]} == {".gitignore", "app.py", "draft.py"}
    assert read_json(result, "ci.json")["dependabot"] is None
    output = "\n".join(
        p.read_text(encoding="utf-8")
        for p in result.out_dir.iterdir()
        if p.suffix in {".md", ".json"}
    )
    assert "PRIVATE_PERSONAL_HEADING" not in output
    assert "PRIVATE_LOCAL_DEPENDENCY" not in output
    ctx, _ = prepare_context(Target(path=root), _options(tmp_path))
    assert not ctx.exists(".github/dependabot.yml")
    assert ctx.read(".github/dependabot.yml") == ""


def test_unborn_repository_still_honours_gitignore(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "unborn", {".gitignore": "private.py\n", "private.py": "SECRET = 1\n"}
    )
    git(root, "init", "-b", "main")
    result = run_digest(Target(path=root), _options(tmp_path))
    assert result.manifest["prey"]["sha"].startswith("nogit-")
    assert result.manifest["prey"]["git_scope"] == "repository-root"
    assert read_json(result, "inventory.json")["files"] == 1
    assert read_json(result, "history.json")["available"] is False


def test_tracked_ignored_file_and_visible_unicode_draft_are_included(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo", {".gitignore": ".python-version\n", "app.py": "x = 1\n"})
    write_tree(root, {".python-version": "3.12\n", "draft (\u03b1).py": "y = 2\n"})
    git(root, "add", "-f", ".python-version")
    result = run_digest(Target(path=root), _options(tmp_path))
    assert read_json(result, "traits.json")["traits"]["has_python_version_file"] is True
    assert read_json(result, "inventory.json")["files"] == 4
    assert result.manifest["prey"]["worktree"] == "unknown"


def test_linked_worktree_and_nested_repository_keep_their_own_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo", {"app.py": "x = 1\n"})
    linked = tmp_path / "linked"
    git(root, "worktree", "add", "-b", "linked", str(linked))
    linked_ctx, _ = prepare_context(Target(path=linked), _options(tmp_path))
    assert linked_ctx.git is not None
    assert linked_ctx.sha == git(root, "rev-parse", "HEAD").strip()
    nested = _repo(root / "nested", {"private.py": "PRIVATE_FACT = 1\n"})
    nested_ctx, _ = prepare_context(Target(path=nested), _options(tmp_path))
    assert nested_ctx.git is not None
    assert nested_ctx.sha == git(nested, "rev-parse", "HEAD").strip()
    result = run_digest(Target(path=root), _options(tmp_path))
    assert read_json(result, "inventory.json")["files"] == 1
    ctx, _ = prepare_context(Target(path=root), _options(tmp_path))
    assert not ctx.exists("nested/private.py")
    assert ctx.read("nested/private.py") == ""


def test_git_visibility_failure_never_falls_back_to_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "repo", {"app.py": "x = 1\n"})
    original = GitRunner.try_run

    def unavailable(self: GitRunner, *args: str, timeout: float | None = None) -> str | None:
        if args[:2] == ("ls-files", "--cached"):
            return None
        return original(self, *args, timeout=timeout)

    monkeypatch.setattr(GitRunner, "try_run", unavailable)
    with pytest.raises(CrabError, match="analysis file set"):
        prepare_context(Target(path=root), _options(tmp_path))


def test_git_repository_without_git_is_not_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "repo", {".gitignore": "private.py\n", "app.py": "x = 1\n"})
    write_tree(root, {"private.py": "SECRET = 1\n"})
    monkeypatch.setattr(GitRunner, "available", lambda: False)
    with pytest.raises(CrabError, match="Git repository root"):
        prepare_context(Target(path=root), _options(tmp_path))


def test_licence_directory_cannot_reintroduce_ignored_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(
        tmp_path / "repo",
        {
            ".gitignore": "LICENSES/private.txt\n",
            "LICENSES/MIT.txt": "MIT License\n",
            "app.py": "x = 1\n",
        },
    )
    write_tree(root, {"LICENSES/private.txt": "private licence notes\n"})
    reads: list[Path] = []
    original = license_detection.read_text

    def observed(path: Path, *, limit: int) -> str:
        reads.append(path)
        return original(path, limit=limit)

    monkeypatch.setattr(license_detection, "read_text", observed)
    result = run_digest(Target(path=root), _options(tmp_path))
    assert root / "LICENSES/private.txt" not in reads
    assert "LICENSES/private.txt" not in read_json(result, "license.json")["license_files"]


def test_git_standard_exclusions_and_negation_apply_to_direct_reads(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo", {"app.py": "x = 1\n", ".gitignore": "*.cfg\n!public.cfg\n"})
    # Windows disallows newline filenames; spaces still exercise Git's NUL-delimited output.
    unusual_name = "line break.py" if os.name == "nt" else "line\nbreak.py"
    write_tree(
        root,
        {
            ".git/info/exclude": "personal.py\n",
            "personal.py": "PRIVATE = 1\n",
            "private.cfg": "private\n",
            "public.cfg": "public\n",
            unusual_name: "x = 2\n",
        },
    )
    ctx, _ = prepare_context(Target(path=root), _options(tmp_path))
    assert not ctx.exists("personal.py")
    assert not ctx.exists("private.cfg")
    assert ctx.read("personal.py") == ""
    assert ctx.read("public.cfg") == "public\n"
    assert ctx.read(unusual_name) == "x = 2\n"


def test_old_file_selection_policy_cannot_reuse_a_digest(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo", {"app.py": "x = 1\n"})
    options = _options(tmp_path)
    first = run_digest(Target(path=root), options)
    assert run_digest(Target(path=root), options).cached
    manifest = first.manifest
    del manifest["analysis_identity"]["file_selection"]
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert not run_digest(Target(path=root), options).cached


def test_direct_reads_cannot_escape_a_plain_tree(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "plain", {"app.py": "x = 1\n"})
    outside = write_tree(tmp_path / "private", {"private.py": "PRIVATE = 1\n"}) / "private.py"
    ctx, _ = prepare_context(Target(path=root), _options(tmp_path))
    for name in ("../private/private.py", str(outside)):
        assert not ctx.exists(name)
        assert ctx.read(name) == ""


def test_personal_files_do_not_hide_adoption_cards(
    tmp_path: Path, npm_digest: DigestResult, py_digest: DigestResult
) -> None:
    # The three fixture-backed traits have independent candidate builders.
    root = _repo(
        tmp_path / "repo",
        {
            "app.py": "x = 1\n",
            ".gitignore": ".python-version\n",
            "pyproject.toml": '[project]\nname = "maw"\nversion = "0.1.0"\n',
        },
    )
    write_tree(
        root,
        {
            ".python-version": "3.12\n",
            "CLAUDE.local.md": "# Private\n",
            ".claude/settings.local.json": "{}",
        },
    )
    result = run_digest(Target(path=root), _options(tmp_path))
    ai_cards = compare_digests(npm_digest.out_dir, result.out_dir)
    python_cards = compare_digests(py_digest.out_dir, result.out_dir)
    assert {"crab:ai-config:ai-config.claude-md", "crab:ai-config:ai-config.claude-settings"} <= {
        card.id for card in ai_cards.candidates
    }
    assert "crab:tooling:tooling.python-version-file" in {
        card.id for card in python_cards.candidates
    }


@pytest.mark.parametrize("committed", [False, True])
def test_personal_agent_files_are_excluded_even_without_gitignore(
    tmp_path: Path, committed: bool
) -> None:
    files = {
        "app.py": "x = 1\n",
        "CLAUDE.local.md": "# PRIVATE_HEADING\n",
        ".claude/settings.local.json": '{"defaultMode":"private"}',
        "nested/CLAUDE.local.md": "# OTHER_PRIVATE_HEADING\n",
    }
    root = _repo(tmp_path / "repo", files) if committed else write_tree(tmp_path / "plain", files)
    result = run_digest(Target(path=root), _options(tmp_path))
    assert read_json(result, "inventory.json")["files"] == 1
    ai = read_json(result, "ai.json")
    assert ai["instruction_files"] == []
    assert ai["settings"] == []
    assert ai["present"] == []
