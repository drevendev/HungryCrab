from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.errors import ExternalCommandError
from hungry_crab.feeder import EatOptions, _acquire, eat
from hungry_crab.fetch.acquisition import AcquisitionBudget, AcquisitionLimitError
from hungry_crab.fetch.catch import CatchResult
from hungry_crab.fetch.git import GitRunner
from hungry_crab.fetch.github import GitHubClient
from hungry_crab.maw import MawConfig


@pytest.fixture
def quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AcquisitionBudget:
    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    return AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 4096)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _cohort(
    quota: AcquisitionBudget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> tuple[Slug, Path, GitHubClient]:
    slug = Slug("fixture", "prey")
    maw_slug = Slug("fixture", "maw")
    maw = tmp_path / "maw"
    maw.mkdir()
    (maw / ".crab.yml").write_text("license: MIT\n", encoding="utf-8")
    old = prey_paths(slug, quota.cache)
    for name, body in {
        "repo/sentinel": "old repository",
        "wiki/Home.md": "# Old wiki",
        "api/repo.json": '{"description":"old metadata"}',
        "api/languages.json": '{"Rust":1}',
        "api/sniff.json": '{"slug":"fixture/prey"}',
        "api/issues.jsonl": '{"title":"old issue"}',
        "catch.json": '{"slug":"fixture/prey","sha":"old"}',
        "digests/retained.json": '{"immutable":"old evidence"}',
    }.items():
        path = old.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    wiki = prey_paths(maw_slug, quota.cache).wiki
    wiki.mkdir(parents=True)
    (wiki / "Home.md").write_text("# Old maw wiki", encoding="utf-8")
    http = quota.cache / "http"
    http.mkdir()
    (http / "retained.json").write_text("old HTTP cache", encoding="utf-8")
    monkeypatch.setattr("hungry_crab.feeder.maw_slug", lambda _: maw_slug)
    monkeypatch.setattr("hungry_crab.feeder.relationship_for", lambda *_: "foreign")

    def repo(client: GitHubClient, source: Slug) -> dict[str, Any]:
        assert client.cache_dir is not None
        client.cache_dir.mkdir(parents=True, exist_ok=True)
        (client.cache_dir / "probe.json").write_text("new HTTP cache", encoding="utf-8")
        return {"size": 1, "has_wiki": True, "description": "new metadata"}

    monkeypatch.setattr(GitHubClient, "repo", repo)
    monkeypatch.setattr(GitHubClient, "languages", lambda *_: {"Python": 2})

    def caught(*args: Any, **kwargs: Any) -> CatchResult:
        # A failed catch preserves its own Git transaction; the regression is sniff's writes.
        if failure == "main":
            raise AcquisitionLimitError("main download budget")
        if failure == "prey_wiki":
            raise AcquisitionLimitError("prey wiki disk capacity")
        if failure == "cancel":
            raise KeyboardInterrupt
        staged = prey_paths(slug, kwargs["cache_root"])
        staged.repo.mkdir(parents=True, exist_ok=True)
        (staged.repo / "sentinel").write_text("new repository", encoding="utf-8")
        staged.wiki.mkdir(exist_ok=True)
        (staged.wiki / "Home.md").write_text("# New wiki", encoding="utf-8")
        result = CatchResult(
            str(slug),
            slug.clone_url,
            str(staged.repo),
            "a" * 40,
            "main",
            True,
            None,
            True,
            "2026-10-24T00:00:00+00:00",
        )
        staged.catch_file.write_text(json.dumps(result.to_dict()), encoding="utf-8")
        return result

    def caught_wiki(destination: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if failure == "maw_wiki":
            raise AcquisitionLimitError("maw wiki download budget")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "Home.md").write_text("# New maw wiki", encoding="utf-8")
        return {"status": "available", "sha": "b" * 40}

    monkeypatch.setattr("hungry_crab.feeder.catch", caught)
    monkeypatch.setattr("hungry_crab.feeder.catch_wiki", caught_wiki)
    return slug, maw, GitHubClient(prefer_gh=False, cache_dir=http)


@pytest.mark.parametrize("failure", ["main", "prey_wiki", "maw_wiki", "cancel"])
def test_actual_sniff_and_late_wiki_failure_preserve_every_old_cache_byte(
    quota: AcquisitionBudget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    slug, maw, client = _cohort(quota, tmp_path, monkeypatch, failure)
    before = _snapshot(quota.cache)
    expected = KeyboardInterrupt if failure == "cancel" else AcquisitionLimitError
    out = tmp_path / "bundle"
    with pytest.raises(expected), quota.scope():
        eat(Target(slug=slug), maw, EatOptions(cache_root=quota.cache, out=out), github=client)
    assert _snapshot(quota.cache) == before
    assert not out.exists() and not list(quota.volume.glob(".acquisition-*"))
    assert client.cache_dir == quota.cache / "http"


def test_success_publishes_one_cohort_and_retains_unrelated_digest_and_http_evidence(
    quota: AcquisitionBudget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, maw, client = _cohort(quota, tmp_path, monkeypatch, "")
    with quota.scope():
        acquired, wiki = _acquire(
            Target(slug=slug),
            maw,
            EatOptions(),
            quota.cache,
            client,
            MawConfig.load(maw),
            lambda _: None,
        )
    current = prey_paths(slug, quota.cache)
    assert json.loads((current.api / "repo.json").read_text())["description"] == "new metadata"
    assert (current.repo / "sentinel").read_text() == "new repository"
    assert (current.wiki / "Home.md").read_text() == "# New wiki"
    assert wiki == prey_paths(Slug("fixture", "maw"), quota.cache).wiki
    assert (wiki / "Home.md").read_text() == "# New maw wiki"
    assert (current.digests / "retained.json").exists()
    assert (quota.cache / "http/retained.json").read_text() == "old HTTP cache"
    assert (quota.cache / "http/probe.json").read_text() == "new HTTP cache"
    assert acquired["catch"]["repo_dir"] == str(current.repo)
    assert json.loads(current.catch_file.read_text())["repo_dir"] == str(current.repo)
    assert not list(quota.volume.glob(".acquisition-*"))


def test_cancellation_during_commit_rolls_back_earlier_cache_entries(
    quota: AcquisitionBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = quota.volume / "staged"
    staged.mkdir()
    quota.cache.mkdir()
    for name in ("a", "b"):
        (staged / name).write_text("new", encoding="utf-8")
        (quota.cache / name).write_text("old", encoding="utf-8")
    rename = Path.rename

    def interrupted(source: Path, destination: Path) -> Path:
        if source == staged / "b":
            raise KeyboardInterrupt
        return rename(source, destination)

    monkeypatch.setattr(Path, "rename", interrupted)
    with pytest.raises(KeyboardInterrupt):
        quota.publish_cache([(staged / name, quota.cache / name) for name in ("a", "b")])
    assert _snapshot(quota.cache) == {"a": b"old", "b": b"old"}
    assert _snapshot(staged) == {"a": b"new", "b": b"new"}
    assert not list(quota.volume.glob(".acquisition-*"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX scoped execution")
def test_filter_probe_obeys_real_stdout_cap_without_running_any_driver(
    quota: AcquisitionBudget, tmp_path: Path
) -> None:
    root = tmp_path / "owned-git-fixture"
    root.mkdir()
    git = GitRunner(root)
    git.run("init", "-q")
    with (root / ".git/config").open("a", encoding="utf-8") as config:
        for index in range(9000):
            config.write(f'\n[filter "{"a" * 1024}{index}"]\n clean = never-run-this-driver\n')
    with (
        pytest.raises(ExternalCommandError, match=r"filter configuration.*read limits"),
        quota.scope(),
    ):
        GitRunner(root).run("rev-parse", "HEAD")
