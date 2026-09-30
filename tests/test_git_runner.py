"""A repository's own configuration cannot make the crab start a program (AGENTS.md rule 3).

A local directory eaten as prey brings its `.git/config` with it, and the digest runs git there:
`diff HEAD` and `ls-files` for the worktree fingerprint, `log` for the history. The payload
below only appends a line to a marker file in the test's own directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import FIXED_NOW

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest
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
