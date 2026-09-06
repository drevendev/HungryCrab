from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.errors import UsageError
from hungry_crab.fetch.catch import CatchOptions, catch, clone_arguments, parse_since

NOW = datetime(2025, 6, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2y", date(2023, 6, 2)),
        ("6m", date(2024, 12, 3)),
        ("90d", date(2025, 3, 3)),
        ("4w", date(2025, 5, 4)),
        ("2024-01-15", date(2024, 1, 15)),
    ],
)
def test_parse_since(text: str, expected: date) -> None:
    assert parse_since(text, now=NOW) == expected


def test_parse_since_rejects_nonsense() -> None:
    with pytest.raises(UsageError):
        parse_since("yesterday", now=NOW)


def test_clone_arguments() -> None:
    assert clone_arguments(CatchOptions()) == ["clone", "--quiet"]
    assert clone_arguments(CatchOptions(shallow=True)) == [
        "clone", "--quiet", "--depth", "1", "--single-branch",
    ]  # fmt: skip
    assert clone_arguments(CatchOptions(since="2y"), now=NOW) == [
        "clone", "--quiet", "--shallow-since=2023-06-02", "--no-single-branch",
    ]  # fmt: skip
    assert clone_arguments(CatchOptions(shallow=True, since="90d"), now=NOW) == [
        "clone", "--quiet", "--shallow-since=2025-03-03", "--single-branch",
    ]  # fmt: skip


def test_catch_clones_then_refreshes_from_a_local_source(npm_app: Path, tmp_path: Path) -> None:
    slug = Slug("example", "crab-cove")
    cache = tmp_path / "cache"
    first = catch(slug, cache_root=cache, source_url=str(npm_app), now=NOW)
    paths = prey_paths(slug, cache)
    assert Path(first.repo_dir) == paths.repo
    assert (paths.repo / "package.json").is_file()
    assert first.updated is False
    assert first.default_branch == "main"
    assert first.shallow is False
    assert len(first.sha) == 40
    recorded = json.loads(paths.catch_file.read_text(encoding="utf-8"))
    assert recorded["slug"] == "example/crab-cove"
    assert recorded["sha"] == first.sha

    second = catch(slug, cache_root=cache, source_url=str(npm_app), now=NOW)
    assert second.updated is True
    assert second.sha == first.sha

    forced = catch(slug, CatchOptions(force=True), cache_root=cache, source_url=str(npm_app))
    assert forced.updated is False
    assert forced.sha == first.sha


class _FakeGitHub:
    def get(self, path: str, *, allow_missing: bool = False) -> object:
        if path.startswith("search/issues"):
            return {"items": []}
        return [
            {"number": 2, "title": "Second", "state": "open", "labels": [], "html_url": "u2"},
            {"number": 1, "title": "First", "state": "closed", "labels": [], "html_url": "u1"},
        ]


def test_catch_can_fetch_issues(npm_app: Path, tmp_path: Path) -> None:
    slug = Slug("example", "crab-cove")
    cache = tmp_path / "cache"
    result = catch(
        slug,
        CatchOptions(issues=10),
        cache_root=cache,
        source_url=str(npm_app),
        github=_FakeGitHub(),  # type: ignore[arg-type]
    )
    assert result.issues_fetched == 2
    lines = (prey_paths(slug, cache).api / "issues.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["number"] == 2
    recorded = json.loads(prey_paths(slug, cache).catch_file.read_text(encoding="utf-8"))
    assert recorded["issues_fetched"] == 2
