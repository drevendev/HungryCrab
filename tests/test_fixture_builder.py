from __future__ import annotations

from pathlib import Path

from fixture_builder import git


def test_npm_fixture_has_the_expected_shape(npm_app: Path) -> None:
    branches = set(git(npm_app, "branch", "--format=%(refname:short)").split())
    assert branches == {"main", "feature/dark-mode", "chore/deps"}
    tags = set(git(npm_app, "tag").split())
    assert tags == {"v0.1.0", "v0.2.0", "v1.0.0", "v1.0.1"}
    assert git(npm_app, "rev-list", "--count", "main").strip() == "13"
    assert (npm_app / "CLAUDE.md").is_file()
    assert (npm_app / ".gitignore").is_file()
    assert not (npm_app / "CLAUDE.md.fixture").exists()
    assert git(npm_app, "status", "--porcelain").strip() == ""


def test_touched_files_carry_comment_lines(npm_app: Path) -> None:
    store = (npm_app / "src/lib/store.ts").read_text(encoding="utf-8")
    assert store.count("// touched") == 5
