"""The MCP scope promise: nothing outside the operator-approved roots reaches a client."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fixture_builder import git

from hungry_crab.errors import UsageError
from hungry_crab.mcp_service import AnalysisService


def _outside_repository(tmp_path: Path) -> Path:
    """A repository beside the approved root, with a commit the client must never see."""
    outside = tmp_path / "outside" / "secret"
    outside.mkdir(parents=True)
    git(outside, "init", "--quiet")
    (outside / "plan.txt").write_text("x\n", encoding="utf-8")
    git(outside, "add", ".")
    git(outside, "commit", "--quiet", "-m", "SECRET-COMMIT-SUBJECT")
    return outside


def _refuses_before_analysis(
    service: AnalysisService, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hungry_crab.mcp_service.run_digest", lambda *_: pytest.fail("analysis ran")
    )
    with pytest.raises(UsageError, match="outside"):
        service.local(value)
    with pytest.raises(UsageError, match="outside"):
        service.digest(value)


def test_a_gitfile_pointing_outside_every_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `.git` file moves the history without moving the work tree (#322)."""
    outside = _outside_repository(tmp_path)
    root = tmp_path / "approved"
    drop = root / "drop"
    drop.mkdir(parents=True)
    (drop / ".git").write_text(f"gitdir: {(outside / '.git').as_posix()}\n", encoding="utf-8")
    _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)


def test_a_common_directory_outside_every_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree-style git directory under the root whose `commondir` lies outside it."""
    outside = _outside_repository(tmp_path)
    root = tmp_path / "approved"
    gitdir, drop = root / "gitdir", root / "drop"
    gitdir.mkdir(parents=True)
    drop.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (gitdir / "commondir").write_text(f"{(outside / '.git').as_posix()}\n", encoding="utf-8")
    (drop / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n", encoding="utf-8")
    _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)


def test_a_linked_git_directory_outside_every_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = _outside_repository(tmp_path)
    root = tmp_path / "approved"
    drop = root / "drop"
    drop.mkdir(parents=True)
    link = drop / ".git"
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside / ".git"), str(link))
    else:
        link.symlink_to(outside / ".git", target_is_directory=True)
    try:
        _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)
    finally:
        if sys.platform == "win32":
            link.rmdir()
        else:
            link.unlink()


def test_a_worktree_whose_repository_is_under_the_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    main = root / "main"
    main.mkdir(parents=True)
    git(main, "init", "--quiet")
    (main / "a.txt").write_text("a\n", encoding="utf-8")
    git(main, "add", ".")
    git(main, "commit", "--quiet", "-m", "init")
    git(main, "worktree", "add", "--quiet", str(root / "wt"))
    assert AnalysisService([root], tmp_path / "cache").local("wt") == (root / "wt").resolve()
