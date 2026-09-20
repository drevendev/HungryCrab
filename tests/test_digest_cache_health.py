"""Regression guards for digest cache identity and causal miner health."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import copy_repo

import hungry_crab.digest as digest_module
from hungry_crab.cache import Target
from hungry_crab.digest import (
    DigestOptions,
    blocked_miners,
    failed_miners,
    incomplete_miners,
    run_digest,
    run_miners,
)
from hungry_crab.miners import MineContext, MinerResult


def _options(tmp_path: Path, *, md_budget: int | None = None) -> DigestOptions:
    return DigestOptions(
        out=tmp_path / "out",
        now=FIXED_NOW,
        cache_root=tmp_path / "cache",
        md_budget=md_budget,
    )


def test_non_git_directory_is_never_reused_by_path(npm_app: Path, tmp_path: Path) -> None:
    work = copy_repo(npm_app, tmp_path / "work")
    options = _options(tmp_path)

    first = run_digest(Target(path=work), options)
    assert not first.cached
    assert first.manifest["prey"]["worktree"] == "unknown"

    unchanged = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not unchanged.cached, "a directory path is not a content identity"

    (work / "README.md").write_text("changed under the same path\n", encoding="utf-8")
    changed = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not changed.cached


def test_markdown_budget_participates_in_cache_identity(npm_app: Path, tmp_path: Path) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    original = _options(tmp_path, md_budget=900)

    first = run_digest(Target(path=work), original)
    assert not first.cached
    same = run_digest(Target(path=work), DigestOptions(**original.__dict__))
    assert same.cached

    changed_options = _options(tmp_path, md_budget=901)
    changed = run_digest(Target(path=work), changed_options)
    assert not changed.cached
    assert changed.manifest["budget"]["per_markdown_file"] == 901

    unchanged_again = run_digest(Target(path=work), DigestOptions(**changed_options.__dict__))
    assert unchanged_again.cached


def test_unknown_worktree_fingerprint_never_becomes_identity(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    options = _options(tmp_path)
    first = run_digest(Target(path=work), options)
    assert not first.cached

    manifest_path = first.manifest_path
    cached = json.loads(manifest_path.read_text(encoding="utf-8"))
    cached["prey"]["worktree"] = "unknown"
    manifest_path.write_text(json.dumps(cached, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(digest_module, "worktree_fingerprint", lambda _git, _root: "unknown")
    again = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not again.cached, "two failed probes do not prove that the worktree bytes are equal"


class _RootFailure:
    name = "root"
    requires: tuple[str, ...] = ()
    json_file: str | None = None
    md_file: str | None = None

    def run(self, _ctx: MineContext) -> MinerResult:
        raise RuntimeError("pretend root failure")


class _BlockedDependent:
    name = "dependent"
    requires = ("root",)
    json_file: str | None = None
    md_file: str | None = None

    def run(self, _ctx: MineContext) -> MinerResult:
        raise AssertionError("a blocked miner must not execute")


class _IndependentSuccess:
    name = "independent"
    requires: tuple[str, ...] = ()
    json_file: str | None = None
    md_file: str | None = None

    def run(self, _ctx: MineContext) -> MinerResult:
        return MinerResult(name=self.name, data={})


def test_miner_health_preserves_failure_causality(tmp_path: Path) -> None:
    ctx = MineContext(root=tmp_path, sha="deadbeef", ref="test", label="fixture")
    records = run_miners(
        ctx,
        [_RootFailure(), _BlockedDependent(), _IndependentSuccess()],
        tmp_path,
    )
    manifest = {"miners": records}

    assert [record["status"] for record in records] == ["failed", "blocked", "ok"]
    assert records[1]["blocked_by"] == ["root"]
    assert failed_miners(manifest) == ["root"]
    assert blocked_miners(manifest) == ["dependent"]
    assert incomplete_miners(manifest) == ["root", "dependent"]
    assert "independent" not in incomplete_miners(manifest)
