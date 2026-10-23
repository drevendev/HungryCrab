"""A repository's own configuration cannot make the crab start a program (AGENTS.md rule 3).

A local directory eaten as prey brings its `.git/config` with it, and the digest runs git there:
`diff HEAD` and `ls-files` for the worktree fingerprint, `log` for the history. Its populated
submodules bring their own configs as well. The payload below only appends a line to a marker
file in the test's own directory.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import FIXED_NOW

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest, worktree_fingerprint
from hungry_crab.errors import ExternalCommandError
from hungry_crab.fetch.git import GitRunner, filter_drivers, neutral_filter_env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        check=False,
    )


def _hostile_prey(tmp_path: Path) -> tuple[Path, Path]:
    """A prey whose config names a program for every vector, with a dirty working tree."""
    marker = tmp_path / "ran.txt"
    script = tmp_path / "payload.sh"
    script.write_text(
        f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\ncat\n", encoding="utf-8", newline="\n"
    )
    command = f"sh {script.as_posix()}"
    repo = tmp_path / "prey"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitattributes").write_text(
        "*.py filter=evil diff=evil\n", encoding="utf-8", newline="\n"
    )
    (repo / "main.py").write_text("print(1)\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "init")
    # dirty, so git has to hash the working tree through the clean filter
    (repo / "main.py").write_text("print(2)\n", encoding="utf-8", newline="\n")
    config = [
        "[core]",
        f"\tfsmonitor = {command}",
        "[diff]",
        f"\texternal = {command}",
        '[diff "evil"]',
        f"\ttextconv = {command}",
        '[filter "evil"]',
        f"\tclean = {command}",
        f"\tprocess = {command}",
        "\trequired = true",
        "[log]",
        "\tshowSignature = true",
        "[gpg]",
        f"\tprogram = {command}",
    ]
    with (repo / ".git" / "config").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(config) + "\n")
    return repo, marker


def test_the_vectors_are_live_without_the_runner(tmp_path: Path) -> None:
    """The control: plain git runs the prey's programs, so the tests below prove something."""
    repo, marker = _hostile_prey(tmp_path)
    _git(repo, "diff", "HEAD")
    assert marker.is_file()


def test_the_runner_starts_none_of_the_programs_a_repository_names(tmp_path: Path) -> None:
    repo, marker = _hostile_prey(tmp_path)
    git = GitRunner(repo)
    for args in (
        ("diff", "HEAD"),
        ("ls-files", "--others"),
        ("status", "--porcelain"),
        ("log", "-p", "-1"),
        ("show", "HEAD"),
    ):
        git.try_run(*args)
    assert not marker.exists()


def test_a_digest_of_a_hostile_local_prey_runs_nothing(tmp_path: Path) -> None:
    repo, marker = _hostile_prey(tmp_path)
    run_digest(
        Target(path=repo),
        DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache"),
    )
    assert not marker.exists()


def _hostile_submodule_prey(tmp_path: Path) -> tuple[Path, Path]:
    """A clean superproject whose populated submodule's own config names a clean filter.

    The superproject's config names nothing, so the neutralised filter list is empty. The
    submodule file is stat-dirty (same bytes, old mtime), the state of any copied tree, so a
    git that looks inside the submodule hashes it through the filter. `.gitmodules` asks never
    to ignore the submodule, which outranks `diff.ignoreSubmodules` in any config.
    """
    marker = tmp_path / "ran.txt"
    script = tmp_path / "payload.sh"
    script.write_text(
        f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\ncat\n", encoding="utf-8", newline="\n"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    _git(sub, "init", "-q")
    (sub / ".gitattributes").write_text("*.txt filter=evil\n", encoding="utf-8", newline="\n")
    (sub / "a.txt").write_text("hello\n", encoding="utf-8", newline="\n")
    _git(sub, "add", "-A")
    _git(sub, "-c", "commit.gpgsign=false", "commit", "-qm", "sub")
    repo = tmp_path / "prey"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "top.txt").write_text("x\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "top")
    added = _git(repo, "-c", "protocol.file.allow=always", "submodule", "--quiet", "add", "../sub")
    assert added.returncode == 0, added.stderr
    with (repo / ".gitmodules").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\tignore = none\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "add sub")
    module_config = repo / ".git" / "modules" / "sub" / "config"
    _git(repo, "config", "-f", str(module_config), "filter.evil.clean", f"sh {script.as_posix()}")
    _stat_dirty(repo / "sub" / "a.txt")
    return repo, marker


def _stat_dirty(path: Path) -> None:
    os.utime(path, (1_600_000_000, 1_600_000_000))


def _traced_children(trace: Path) -> tuple[list[str], list[str]]:
    """The commands a ``GIT_TRACE`` file shows git running, and the children they started."""
    lines = trace.read_text(encoding="utf-8", errors="replace").splitlines()
    trace.unlink()
    ran = [line for line in lines if "trace: built-in: git" in line]
    children = [line for line in lines if "run_command:" in line or "start_command:" in line]
    return ran, children


def test_a_submodule_filter_is_live_without_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: plain git runs the submodule's filter from a child `git status`."""
    repo, marker = _hostile_submodule_prey(tmp_path)
    _git(repo, "diff", "HEAD")
    assert marker.is_file()
    trace = tmp_path / "trace.txt"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    for args in (("diff", "HEAD"), ("diff-files",), ("diff-index", "HEAD")):
        _stat_dirty(repo / "sub" / "a.txt")
        _git(repo, *args)
        _ran, children = _traced_children(trace)
        assert any("cd sub" in child for child in children), args


def test_the_runner_starts_no_git_inside_a_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, marker = _hostile_submodule_prey(tmp_path)
    trace = tmp_path / "trace.txt"
    git = GitRunner(repo)
    for args in (
        ("diff", "HEAD"),
        ("diff",),
        ("status", "--porcelain"),
        ("ls-files", "--others"),
        ("ls-files", "--stage"),
        ("log", "-p", "-1"),
        ("show", "HEAD"),
        ("diff-files",),
        ("diff-index", "HEAD"),
        ("-c", "core.abbrev=12", "diff", "HEAD"),
        ("-c", "core.abbrev=12", "diff-index", "HEAD"),
    ):
        _stat_dirty(repo / "sub" / "a.txt")
        monkeypatch.setenv("GIT_TRACE", str(trace))
        git.try_run(*args)
        monkeypatch.delenv("GIT_TRACE")
        ran, children = _traced_children(trace)
        command = next(arg for arg in args if not arg.startswith("-") and "=" not in arg)
        assert any(f" {command} " in f"{line} " for line in ran), (args, ran)
        assert children == [], (args, children)
        assert not marker.exists(), args
    # A gitlink is still a change between commits: the publication guards must see one.
    assert "sub" in git.run("diff", "--name-only", "HEAD~1", "HEAD").split()


def test_a_describe_that_would_look_inside_submodules_is_refused(tmp_path: Path) -> None:
    repo, marker = _hostile_submodule_prey(tmp_path)
    for flag in ("--dirty", "--broken=-broken"):
        with pytest.raises(ExternalCommandError, match="describe"):
            GitRunner(repo).run("describe", "--always", flag)
    assert not marker.exists()


def test_a_populated_submodule_makes_the_fingerprint_unknown(tmp_path: Path) -> None:
    repo, marker = _hostile_submodule_prey(tmp_path)
    assert worktree_fingerprint(GitRunner(repo), repo) == "unknown"
    _stat_dirty(repo / "sub" / "a.txt")
    run_digest(
        Target(path=repo),
        DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache"),
    )
    assert not marker.exists()


def test_an_unpopulated_submodule_keeps_a_clean_fingerprint(tmp_path: Path) -> None:
    repo, _marker = _hostile_submodule_prey(tmp_path)
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(repo), str(clone))
    assert (clone / "sub").is_dir() and not (clone / "sub" / ".git").exists()
    assert worktree_fingerprint(GitRunner(clone), clone) == "clean"


def test_files_in_an_unpopulated_gitlink_make_the_fingerprint_unknown(tmp_path: Path) -> None:
    """Git lists them neither as changes nor as untracked, but the inventory reads them."""
    repo, _marker = _hostile_submodule_prey(tmp_path)
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(repo), str(clone))
    (clone / "sub" / "dropped.py").write_text("x = 1\n", encoding="utf-8")
    git = GitRunner(clone)
    assert git.run("ls-files", "--others").strip() == ""
    assert worktree_fingerprint(git, clone) == "unknown"


def test_filter_driver_names_survive_dots_and_equals_signs() -> None:
    listing = "filter.lfs.clean\0filter.lfs.smudge\0filter.a.b.clean\0filter.x=y.process\0"
    assert filter_drivers(listing) == ["lfs", "a.b", "x=y"]
    assert filter_drivers("") == []


def test_neutral_filters_extend_an_existing_config_environment() -> None:
    env = neutral_filter_env(["x=y"], {"GIT_CONFIG_COUNT": "1"})
    assert env["GIT_CONFIG_COUNT"] == "5"
    assert env["GIT_CONFIG_KEY_1"] == "filter.x=y.clean"
    assert env["GIT_CONFIG_VALUE_1"] == ""
    assert env["GIT_CONFIG_KEY_4"] == "filter.x=y.required"
    assert env["GIT_CONFIG_VALUE_4"] == "false"
    assert neutral_filter_env([], {}) == {}
