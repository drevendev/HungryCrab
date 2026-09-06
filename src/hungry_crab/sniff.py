"""``crab sniff``: API-only reconnaissance. Is the prey worth eating, and under which mode?"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cache import Slug, prey_paths
from .fetch.github import GitHubClient
from .licensing import (
    LicenseClass,
    Relationship,
    classify,
    decide,
    modes_by_maw_class,
    normalize,
)

GIANT_KB = 300 * 1024
HUGE_KB = 1024 * 1024
STALE_DAYS = 730

VERDICT_EAT = "EAT"
VERDICT_CAREFUL = "EAT_CAREFULLY"
VERDICT_IDEAS = "IDEAS_ONLY"
VERDICT_HUMAN = "HUMAN"


def _noop(_: str) -> None:
    return None


@dataclass
class SniffReport:
    slug: str
    url: str
    description: str | None
    default_branch: str
    stars: int
    forks: int
    open_issues: int
    size_kb: int
    archived: bool
    fork: bool
    parent: str | None
    created_at: str | None
    pushed_at: str | None
    license_spdx: str | None
    license_name: str | None
    license_class: str
    languages: dict[str, int]
    topics: list[str]
    has_wiki: bool
    has_discussions: bool
    verdict: str
    modes_by_maw_class: dict[str, str]
    maw_license: str | None
    mode: str | None
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def build_report(
    slug: Slug,
    repo: dict[str, Any],
    languages: dict[str, int],
    *,
    maw_license: str | None = None,
    relationship: Relationship | str = Relationship.FOREIGN,
    now: datetime | None = None,
) -> SniffReport:
    current = now or datetime.now(UTC)
    license_info = repo.get("license")
    raw_spdx: str | None = None
    license_name: str | None = None
    if isinstance(license_info, dict):
        spdx_value = license_info.get("spdx_id")
        raw_spdx = spdx_value if isinstance(spdx_value, str) else None
        name_value = license_info.get("name")
        license_name = name_value if isinstance(name_value, str) else None

    if raw_spdx and raw_spdx.upper() == "NOASSERTION":
        spdx: str | None = None
        cls = LicenseClass.UNKNOWN
    else:
        spdx = normalize(raw_spdx)
        cls = classify(spdx)

    if cls is LicenseClass.UNKNOWN:
        verdict = VERDICT_HUMAN
    elif cls is LicenseClass.NONE:
        verdict = VERDICT_IDEAS
    elif cls in (
        LicenseClass.PERMISSIVE,
        LicenseClass.PERMISSIVE_NOTICE,
        LicenseClass.DOCS_ATTRIBUTION,
    ):
        verdict = VERDICT_EAT
    elif cls in (
        LicenseClass.FILE_COPYLEFT,
        LicenseClass.LGPL,
        LicenseClass.GPL,
        LicenseClass.AGPL,
        LicenseClass.DOCS_SHARE_ALIKE,
    ):
        verdict = VERDICT_CAREFUL
    else:
        verdict = VERDICT_IDEAS

    warnings: list[str] = []
    suggestions: list[str] = []
    size_kb = _int(repo.get("size"))
    if cls is LicenseClass.NONE:
        warnings.append("no license detected: all rights reserved by default, ideas only")
    elif cls is LicenseClass.UNKNOWN:
        warnings.append(
            f"custom or unrecognised license ({license_name or raw_spdx}): a human must decide"
        )
    if repo.get("archived"):
        warnings.append("archived repository: practices may be stale")
    parent_value = repo.get("parent")
    parent = parent_value.get("full_name") if isinstance(parent_value, dict) else None
    if repo.get("fork"):
        warnings.append(
            f"this is a fork; consider eating the upstream {parent}" if parent else "this is a fork"
        )
    pushed = _parse_time(repo.get("pushed_at"))
    if pushed is not None:
        idle = (current - pushed).days
        if idle > STALE_DAYS:
            warnings.append(f"no pushes for {idle} days")
    if size_kb >= HUGE_KB:
        warnings.append(f"huge repository (~{size_kb / 1024:.0f} MB)")
        suggestions.append(f"crab catch {slug} --shallow --since 2y")
    elif size_kb >= GIANT_KB:
        warnings.append(f"large repository (~{size_kb / 1024:.0f} MB)")
        suggestions.append(f"crab catch {slug} --since 2y")
    else:
        suggestions.append(f"crab catch {slug}")

    topics_value = repo.get("topics")
    topics = (
        [t for t in topics_value if isinstance(t, str)] if isinstance(topics_value, list) else []
    )
    branch_value = repo.get("default_branch")
    description = repo.get("description")

    return SniffReport(
        slug=str(slug),
        url=slug.url,
        description=description if isinstance(description, str) else None,
        default_branch=branch_value if isinstance(branch_value, str) else "main",
        stars=_int(repo.get("stargazers_count")),
        forks=_int(repo.get("forks_count")),
        open_issues=_int(repo.get("open_issues_count")),
        size_kb=size_kb,
        archived=bool(repo.get("archived")),
        fork=bool(repo.get("fork")),
        parent=parent if isinstance(parent, str) else None,
        created_at=repo.get("created_at") if isinstance(repo.get("created_at"), str) else None,
        pushed_at=repo.get("pushed_at") if isinstance(repo.get("pushed_at"), str) else None,
        license_spdx=spdx,
        license_name=license_name,
        license_class=cls.value,
        languages=dict(sorted(languages.items(), key=lambda kv: -kv[1])),
        topics=topics,
        has_wiki=bool(repo.get("has_wiki")),
        has_discussions=bool(repo.get("has_discussions")),
        verdict=verdict,
        modes_by_maw_class=modes_by_maw_class(
            spdx if cls is not LicenseClass.UNKNOWN else "NOASSERTION"
        ),
        maw_license=maw_license,
        mode=decide(spdx, maw_license, relationship=relationship).mode.value
        if maw_license
        else None,
        warnings=warnings,
        suggestions=suggestions,
        fetched_at=current.isoformat(timespec="seconds"),
    )


def format_report(report: SniffReport) -> str:
    lines = [f"{report.slug}  {report.url}"]
    if report.description:
        lines.append(f"  {report.description[:200]}")
    license_text = report.license_spdx or report.license_name or "none"
    lines.append(f"License: {license_text} ({report.license_class})  ->  verdict {report.verdict}")
    modes = ", ".join(f"{k} maw: {v}" for k, v in report.modes_by_maw_class.items())
    lines.append(f"Modes: {modes}")
    if report.maw_license:
        lines.append(f"Mode for maw ({report.maw_license}): {report.mode}")
    lines.append(
        f"Size: {report.size_kb / 1024:.1f} MB | stars {report.stars} | forks {report.forks} | "
        f"open issues {report.open_issues} | default branch {report.default_branch}"
    )
    if report.pushed_at:
        lines.append(f"Last push: {report.pushed_at}")
    total = sum(report.languages.values()) or 1
    if report.languages:
        shares = ", ".join(
            f"{name} {count * 100 / total:.0f}%"
            for name, count in list(report.languages.items())[:5]
        )
        lines.append(f"Languages: {shares}")
    if report.topics:
        lines.append("Topics: " + ", ".join(report.topics[:10]))
    for warning in report.warnings:
        lines.append(f"! {warning}")
    for suggestion in report.suggestions:
        lines.append(f"Next: {suggestion}")
    return "\n".join(lines)


def sniff(
    slug: Slug,
    *,
    client: GitHubClient | None = None,
    cache_root: Path | None = None,
    maw_license: str | None = None,
    relationship: Relationship | str = Relationship.FOREIGN,
    now: datetime | None = None,
    log: Callable[[str], None] = _noop,
) -> SniffReport:
    """Fetch metadata, store the raw API responses in the cache, and build the report."""
    api = client or GitHubClient()
    log(f"sniffing {slug} via {api.transport}")
    repo = api.repo(slug)
    languages = api.languages(slug)
    report = build_report(
        slug, repo, languages, maw_license=maw_license, relationship=relationship, now=now
    )
    paths = prey_paths(slug, cache_root)
    paths.api.mkdir(parents=True, exist_ok=True)
    (paths.api / "repo.json").write_text(json.dumps(repo, indent=2) + "\n", encoding="utf-8")
    (paths.api / "languages.json").write_text(
        json.dumps(languages, indent=2) + "\n", encoding="utf-8"
    )
    (paths.api / "sniff.json").write_text(
        json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return report
