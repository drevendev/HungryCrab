"""The MCP scope promise: nothing outside the operator-approved roots reaches a client."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

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


def test_a_prey_outside_the_roots_is_refused_alike_and_never_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether a path beside the root exists must not change the answer, or be asked (#327)."""
    root = tmp_path / "approved"
    root.mkdir()
    (tmp_path / "outside-secret-project").mkdir()
    service = AnalysisService([root], tmp_path / "cache")
    monkeypatch.setattr(
        "hungry_crab.mcp_service.run_digest", lambda *_: pytest.fail("analysis ran")
    )
    probed: list[str] = []
    messages: set[str] = set()
    with monkeypatch.context() as patch:
        for name in ("exists", "resolve", "is_dir", "stat", "lstat"):
            real = getattr(Path, name)

            def spy(self: Path, *args: object, _real: Any = real, **kwargs: object) -> Any:
                probed.append(str(self))
                return _real(self, *args, **kwargs)

            patch.setattr(Path, name, spy)
        for value in ("x/../../outside-secret-project", "x/../../no-such-project", ".."):
            for call in (service.prey, service.digest):
                try:
                    call(value)
                except UsageError as refused:
                    messages.add(refused.message)
                else:
                    pytest.fail(f"{value!r} was accepted")
    assert probed == []
    assert messages == {"repository is outside the operator-approved MCP roots"}


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


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions exist only on Windows")
def test_a_junction_inside_the_prey_does_not_carry_the_digest_outside(tmp_path: Path) -> None:
    """The argument passes the root check; the walk must not then read beyond it (#323)."""
    import _winapi

    outside = tmp_path / "outside-private"
    outside.mkdir()
    (outside / "requirements.txt").write_text("internal-billing-sdk==4.2.0\n", encoding="utf-8")
    (outside / "salary-model.py").write_text("x = 1\n" * 50, encoding="utf-8")
    root = tmp_path / "approved"
    prey = root / "prey"
    prey.mkdir(parents=True)
    (prey / "README.md").write_text("# prey\n", encoding="utf-8")
    git(prey, "init", "--quiet")
    git(prey, "add", ".")
    git(prey, "commit", "--quiet", "-m", "init")
    (prey / ".git" / "info").mkdir(exist_ok=True)
    (prey / ".git" / "info" / "exclude").write_text("tools\n", encoding="utf-8")
    junction = prey / "tools"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        result = AnalysisService([root], tmp_path / "cache").digest("prey")
    finally:
        junction.rmdir()
    assert result["manifest"]["coverage"]["loss"]["symlinks_skipped"] == 1
    out = Path(result["digest_dir"])
    for artifact in out.iterdir():
        if artifact.is_file():
            text = artifact.read_text(encoding="utf-8", errors="replace")
            assert "internal-billing-sdk" not in text and "salary-model" not in text, artifact.name


def _link_directory(target: Path, link: Path) -> None:
    """A junction on Windows (no privilege needed), a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink_directory(link: Path) -> None:
    if sys.platform == "win32":
        link.rmdir()
    else:
        link.unlink()


def _drop_repository(root: Path) -> Path:
    """A repository under the root whose git data can then be moved piece by piece."""
    drop = root / "drop"
    drop.mkdir(parents=True)
    (drop / "a.py").write_text("y = 2\n", encoding="utf-8")
    git(drop, "init", "--quiet")
    return drop


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_alternates_borrowing_an_outside_object_store_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    """Plain text files an archive can carry: an alternate and a ref to an outside commit."""
    outside = _outside_repository(tmp_path)
    sha = git(outside, "rev-parse", "HEAD").strip()
    root = tmp_path / "approved"
    drop = _drop_repository(root)
    objects = drop / ".git" / "objects"
    borrowed = outside / ".git" / "objects"
    entry = Path(os.path.relpath(borrowed, objects)) if relative else borrowed
    (objects / "info" / "alternates").write_text(f"{entry.as_posix()}\n", encoding="utf-8")
    (drop / ".git" / "refs" / "heads" / "master").write_text(f"{sha}\n", encoding="utf-8")
    (drop / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)


def test_http_alternates_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "approved"
    drop = _drop_repository(root)
    info = drop / ".git" / "objects" / "info"
    (info / "http-alternates").write_text("https://example.invalid/objects\n", encoding="utf-8")
    _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)


def test_alternates_inside_the_root_are_accepted(tmp_path: Path) -> None:
    """`git clone --shared` within the roots borrows nothing the operator did not grant."""
    root = tmp_path / "approved"
    main = root / "main"
    main.mkdir(parents=True)
    git(main, "init", "--quiet")
    (main / "a.txt").write_text("a\n", encoding="utf-8")
    git(main, "add", ".")
    git(main, "commit", "--quiet", "-m", "init")
    git(root, "clone", "--quiet", "--shared", str(main), str(root / "shared"))
    assert (root / "shared" / ".git" / "objects" / "info" / "alternates").is_file()
    service = AnalysisService([root], tmp_path / "cache")
    assert service.local("shared") == (root / "shared").resolve()
    assert service.local("main") == main.resolve()
    (main / "sub").mkdir()
    assert service.local("main/sub") == (main / "sub").resolve()


@pytest.mark.parametrize("store", ["objects", "refs", "objects/pack", "refs/heads"])
def test_a_linked_object_or_ref_store_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: str
) -> None:
    """A store, or a directory inside one, linked to an outside repository needs no SHA."""
    outside = _outside_repository(tmp_path)
    root = tmp_path / "approved"
    drop = _drop_repository(root)
    link = drop / ".git" / store
    shutil.rmtree(link)
    _link_directory(outside / ".git" / store, link)
    try:
        _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)
    finally:
        _unlink_directory(link)


def test_a_linked_store_is_refused_even_when_it_points_inside_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved"
    drop = _drop_repository(root)
    elsewhere = root / "elsewhere-refs"
    shutil.copytree(drop / ".git" / "refs", elsewhere)
    link = drop / ".git" / "refs"
    shutil.rmtree(link)
    _link_directory(elsewhere, link)
    try:
        _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)
    finally:
        _unlink_directory(link)


def test_a_linked_packed_refs_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = _outside_repository(tmp_path)
    git(outside, "pack-refs", "--all")
    root = tmp_path / "approved"
    drop = _drop_repository(root)
    try:
        (drop / ".git" / "packed-refs").symlink_to(outside / ".git" / "packed-refs")
    except OSError:
        pytest.skip("file symlinks need a privilege this runner does not hold")
    _refuses_before_analysis(AnalysisService([root], tmp_path / "cache"), "drop", monkeypatch)
