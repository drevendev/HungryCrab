"""Require normal release-tag CI to use an integrated commit (issue #279).

This is a release-procedure check, not proof of human approval or protection
against an actor who can rewrite the tagged tests or workflow.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MASTER = "refs/remotes/origin/master"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False, timeout=15
    )


def _tag_commit_reachable_from_master(repo: Path, tag_commit_sha: str) -> bool:
    """Fail closed if checkout, full history, or the remote master ref is missing."""
    if not _SHA.fullmatch(tag_commit_sha):
        return False
    head = _git(repo, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != tag_commit_sha:
        return False
    shallow = _git(repo, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        return False
    master = _git(repo, "rev-parse", "--verify", MASTER)
    if master.returncode != 0 or not _SHA.fullmatch(master.stdout.strip()):
        return False
    return _git(repo, "merge-base", "--is-ancestor", tag_commit_sha, MASTER).returncode == 0


def test_release_tag_commit_is_reachable_from_master() -> None:
    """Tag CI must fail if the release commit has not landed on master."""
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        pytest.skip("release-tag environment gate is only applicable to tag CI")
    assert _tag_commit_reachable_from_master(ROOT, os.environ.get("GITHUB_SHA", "")), (
        "Release tag's exact checked-out commit must be in the full history of origin/master. "
        "Tag CI requires actions/checkout with fetch-depth: 0 and the origin/master ref."
    )


def _setup_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "safe-git-history"
    repo.mkdir()
    assert _git(repo, "init", "-q", "-b", "master").returncode == 0
    assert _git(repo, "config", "user.name", "Synthetic Crab").returncode == 0
    assert _git(repo, "config", "user.email", "synthetic@invalid.example").returncode == 0
    (repo / "version").write_text("0.2.0.dev0\n", encoding="utf-8")
    assert _git(repo, "add", "version").returncode == 0
    assert _git(repo, "commit", "-qm", "baseline").returncode == 0
    assert _git(repo, "update-ref", MASTER, "master").returncode == 0
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, sha


def _commit(repo: Path, value: str) -> str:
    (repo / "version").write_text(value + "\n", encoding="utf-8")
    assert _git(repo, "add", "version").returncode == 0
    assert _git(repo, "commit", "-qm", value).returncode == 0
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_merged_release_commit_passes(tmp_path: Path) -> None:
    repo, _ = _setup_repo(tmp_path)
    release_sha = _commit(repo, "0.3.0")
    _commit(repo, "0.4.0.dev0")
    assert _git(repo, "update-ref", MASTER, "master").returncode == 0
    assert _git(repo, "checkout", "-q", "--detach", release_sha).returncode == 0
    assert _tag_commit_reachable_from_master(repo, release_sha)


def test_unmerged_release_commit_fails(tmp_path: Path) -> None:
    repo, _ = _setup_repo(tmp_path)
    assert _git(repo, "checkout", "-qb", "unmerged-release").returncode == 0
    foreign_sha = _commit(repo, "0.3.0")
    assert _tag_commit_reachable_from_master(repo, foreign_sha) is False


def test_missing_remote_ref_fails_closed(tmp_path: Path) -> None:
    repo, sha = _setup_repo(tmp_path)
    assert _git(repo, "update-ref", "-d", MASTER).returncode == 0
    assert _tag_commit_reachable_from_master(repo, sha) is False


def test_mismatched_checkout_and_malformed_sha_fail(tmp_path: Path) -> None:
    repo, sha = _setup_repo(tmp_path)
    newer_sha = _commit(repo, "0.3.0")
    assert sha != newer_sha
    assert _tag_commit_reachable_from_master(repo, sha) is False
    assert _tag_commit_reachable_from_master(repo, "not-a-sha") is False


def test_non_repository_fails_closed(tmp_path: Path) -> None:
    assert _tag_commit_reachable_from_master(tmp_path, "f" * 40) is False


def test_shallow_repo_fails_closed_even_if_tag_is_head(tmp_path: Path) -> None:
    """A shallow checkout cannot supply trustworthy full master ancestry proof."""
    repo, sha = _setup_repo(tmp_path)
    clone = tmp_path / "shallow-clone"
    clone_result = subprocess.run(
        ["git", "clone", "-q", "--depth=1", "--branch=master", repo.resolve().as_uri(), str(clone)],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert clone_result.returncode == 0, clone_result.stderr
    assert _git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == sha
    assert _tag_commit_reachable_from_master(clone, sha) is False


def test_remote_snapshot_without_release_fails_closed(tmp_path: Path) -> None:
    repo, baseline = _setup_repo(tmp_path)
    release_sha = _commit(repo, "0.3.0")
    assert _git(repo, "rev-parse", MASTER).stdout.strip() == baseline
    assert not _tag_commit_reachable_from_master(repo, release_sha)
    assert _git(repo, "update-ref", MASTER, release_sha).returncode == 0
    assert _tag_commit_reachable_from_master(repo, release_sha)
