from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hungry_crab.cache import Slug
from hungry_crab.sniff import (
    VERDICT_CAREFUL,
    VERDICT_EAT,
    VERDICT_HUMAN,
    VERDICT_IDEAS,
    build_report,
    format_report,
)

NOW = datetime(2025, 6, 1, tzinfo=UTC)
SLUG = Slug("example", "prey")


def _repo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "description": "A prey repository",
        "default_branch": "main",
        "stargazers_count": 1200,
        "forks_count": 80,
        "open_issues_count": 12,
        "size": 20_000,
        "archived": False,
        "fork": False,
        "pushed_at": "2025-05-20T10:00:00Z",
        "created_at": "2020-01-01T00:00:00Z",
        "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
        "topics": ["crab", "tooling"],
        "has_wiki": True,
        "has_discussions": False,
    }
    base.update(overrides)
    return base


def test_permissive_prey_is_eatable() -> None:
    report = build_report(SLUG, _repo(), {"TypeScript": 900, "CSS": 100}, now=NOW)
    assert report.verdict == VERDICT_EAT
    assert report.license_spdx == "MIT"
    assert report.license_class == "permissive"
    assert report.modes_by_maw_class["gpl"] == "COPY"
    assert report.warnings == []
    assert report.suggestions == ["crab catch example/prey"]
    assert list(report.languages) == ["TypeScript", "CSS"]
    text = format_report(report)
    assert "verdict EAT" in text
    assert "TypeScript 90%" in text
    assert "Next: crab catch example/prey" in text


def test_copyleft_prey_needs_care_and_maw_mode() -> None:
    gpl = {"key": "gpl-3.0", "name": "GNU General Public License v3.0", "spdx_id": "GPL-3.0"}
    report = build_report(SLUG, _repo(license=gpl), {}, maw_license="MIT", now=NOW)
    assert report.verdict == VERDICT_CAREFUL
    assert report.license_spdx == "GPL-3.0-only"
    assert report.mode == "REIMPLEMENT"
    assert "Mode for maw (MIT): REIMPLEMENT" in format_report(report)


def test_custom_license_goes_to_a_human() -> None:
    other = {"key": "other", "name": "Other", "spdx_id": "NOASSERTION"}
    report = build_report(SLUG, _repo(license=other), {}, now=NOW)
    assert report.verdict == VERDICT_HUMAN
    assert report.license_spdx is None
    assert any("human" in warning for warning in report.warnings)


def test_missing_license_means_ideas_only() -> None:
    report = build_report(SLUG, _repo(license=None), {}, now=NOW)
    assert report.verdict == VERDICT_IDEAS
    assert report.modes_by_maw_class["permissive"] == "IDEAS_ONLY"


def test_giant_archived_fork_gets_warnings() -> None:
    repo = _repo(
        size=400 * 1024,
        archived=True,
        fork=True,
        parent={"full_name": "upstream/prey"},
        pushed_at="2022-01-01T00:00:00Z",
    )
    report = build_report(SLUG, repo, {}, now=NOW)
    joined = "\n".join(report.warnings)
    assert "archived" in joined
    assert "upstream/prey" in joined
    assert "large repository" in joined
    assert "no pushes for" in joined
    assert report.suggestions == ["crab catch example/prey --since 2y"]
    huge = build_report(SLUG, _repo(size=2 * 1024 * 1024), {}, now=NOW)
    assert huge.suggestions == ["crab catch example/prey --shallow --since 2y"]
