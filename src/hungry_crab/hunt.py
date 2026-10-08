"""Read-only, gap-directed GitHub discovery; search hints never grant copying rights."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .cache import Slug, Target
from .compare.candidates import Side
from .compare.rules import TRAIT_RULES
from .compare.scoring import Scoring
from .digest import DigestOptions, run_digest
from .errors import CrabError, UsageError
from .fetch.github import GitHubClient
from .hunt_config import HuntSettings
from .ledger import Ledger
from .licensing import decide
from .maw import MawConfig, maw_slug
from .mdutil import inline
from .memory import confirmed, counts, factor, learn

LANGUAGES = {
    "python": "Python",
    "npm": "TypeScript",
    "rust": "Rust",
    "go": "Go",
    "ruby": "Ruby",
    "jvm": "Java",
    "dotnet": "C#",
    "php": "PHP",
}
ECOSYSTEMS = {
    "Python": "python",
    "TypeScript": "npm",
    "JavaScript": "npm",
    "Rust": "rust",
    "Go": "go",
    "Ruby": "ruby",
    "Java": "jvm",
    "C#": "dotnet",
    "PHP": "php",
}
TOPICS = {
    "tests": "testing",
    "ci": "continuous-integration",
    "security": "security",
    "tooling": "developer-tools",
    "docs": "documentation",
    "hygiene": "template",
    "ai-config": "ai",
    "deps": "library",
}


def gaps_for(side: Side, config: MawConfig, scoring: Scoring) -> list[dict[str, Any]]:
    gaps = []
    for rule in TRAIT_RULES:
        if config.hunger.get(rule.category, True) is False or side.traits.get(rule.trait):
            continue
        if any(not side.traits.get(name) for name in rule.needs_maw):
            continue
        gaps.append(
            {
                "key": rule.key,
                "category": rule.category,
                "trait": rule.trait,
                "priority": round(scoring.categories.get(rule.category, 0.5) * rule.value, 6),
            }
        )
    return sorted(gaps, key=lambda item: (-item["priority"], item["key"]))


def discover(
    side: Side,
    config: MawConfig,
    ledger: Ledger,
    settings: HuntSettings,
    *,
    client: GitHubClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """At most four requests and 100 results per request; no repository content is fetched."""
    settings = HuntSettings.load(asdict(settings))
    client = client or GitHubClient()
    now = now or datetime.now(UTC)
    scoring, memory = learn(
        ledger, Scoring.default().merged(config.scoring), config.memory, overrides=config.scoring
    )
    gaps = gaps_for(side, config, scoring)
    languages = sorted(LANGUAGES[eco] for eco in side.ecosystems if eco in LANGUAGES)
    queries = list(settings.queries)
    if not queries:
        topics = list(dict.fromkeys(TOPICS.get(gap["category"], "template") for gap in gaps))[:4]
        if not topics:
            topics = ["template"]
        language = (" language:" + languages[0]) if languages else ""
        queries = [
            f"topic:{topic}{language} archived:false fork:false stars:>={settings.min_stars}"
            for topic in topics
        ]
    seen = {meal.prey.casefold() for meal in ledger.meals}
    excluded = {str(Slug.parse(item)).casefold() for item in settings.exclude}
    own = maw_slug(config.root)
    if own:
        excluded.add(str(own).casefold())
    groups: dict[str, list[Any]] = {}
    for entry in confirmed(ledger):
        groups.setdefault(entry.prey.casefold(), []).append(entry)
    found: dict[str, dict[str, Any]] = {}
    evidence = []
    rejected: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for query in queries:
        response = client.get(
            "search/repositories?"
            + urlencode(
                {"q": query, "sort": "stars", "order": "desc", "per_page": settings.max_candidates}
            )
        )
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            raise CrabError("GitHub search returned an invalid repository response")
        items = response["items"]
        evidence.append(
            {
                "query": query,
                "total_count": response.get("total_count"),
                "returned": len(items),
                "incomplete_results": response.get("incomplete_results") is True,
                "truncated": int(response.get("total_count", 0)) > len(items),
            }
        )
        for item in items[: settings.max_candidates]:
            if not isinstance(item, dict):
                raise CrabError("GitHub search returned an invalid repository item")
            try:
                slug = Slug.parse(str(item.get("full_name", "")))
            except UsageError:
                reject("invalid_slug")
                continue
            name = str(slug)
            key = name.casefold()
            if slug.host != "github.com" or any(
                item.get(flag) is True for flag in ("archived", "fork", "disabled", "private")
            ):
                reject("inactive_or_nonpublic")
                continue
            if key in excluded or (key in seen and not settings.include_seen):
                reject("excluded_or_seen")
                continue
            stars = item.get("stargazers_count")
            size = item.get("size")
            if isinstance(stars, bool) or not isinstance(stars, int) or stars < settings.min_stars:
                reject("stars")
                continue
            known_size = isinstance(size, int) and not isinstance(size, bool) and size >= 0
            if (not known_size and not settings.allow_unknown_size) or (
                isinstance(size, int) and size > settings.max_repo_kb
            ):
                reject("size")
                continue
            license_data = item.get("license")
            spdx = license_data.get("spdx_id") if isinstance(license_data, dict) else None
            if not isinstance(spdx, str) or spdx in {"NOASSERTION", "OTHER"}:
                spdx = None
            if settings.licenses and spdx not in settings.licenses:
                reject("license_filter")
                continue
            repo_language = item.get("language")
            stack = ECOSYSTEMS.get(str(repo_language)) in side.ecosystems
            stats = counts(groups.get(key, []))
            multiplier = factor(stats, config.memory)
            score = round(
                (0.7 if stack else 0.35) * multiplier + min(0.2, math.log10(stars + 1) / 25), 6
            )
            if key not in found:
                verdict = decide(spdx, config.license or side.spdx).to_dict()
                found[key] = {
                    "prey": name,
                    "url": slug.url,
                    "stars": stars,
                    "size_kb": size if known_size else None,
                    "language": repo_language if repo_language in ECOSYSTEMS else None,
                    "license": spdx,
                    "license_review": "search metadata only; recheck pinned digest before serving",
                    "verdict": verdict,
                    "score": score,
                    "signals": {
                        "same_stack": stack,
                        "previous_decisions": stats,
                        "memory_factor": multiplier,
                    },
                    "queries": [],
                }
            found[key]["queries"].append(query)
    candidates = sorted(found.values(), key=lambda item: (-item["score"], item["prey"].casefold()))
    return {
        "schema": "hungry-crab.hunt/1",
        "generated_at": now.isoformat(timespec="seconds"),
        "maw": {"label": side.label, "sha": side.sha, "profile": config.profile},
        "memory": memory.to_dict(),
        "gaps": gaps,
        "queries": queries,
        "search": evidence,
        "filters": rejected,
        "settings": asdict(settings),
        "eligible": len(candidates),
        "candidates": candidates[: settings.limit],
        "truncated": len(candidates) > settings.limit
        or any(row["truncated"] or row["incomplete_results"] for row in evidence),
    }


def hunt(
    maw: Path,
    *,
    settings: HuntSettings | None = None,
    cache_root: Path | None = None,
    client: GitHubClient | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    config = MawConfig.load(maw)
    # Only the explicit maw supplies policy. The digest reads its files without running them.
    digest = run_digest(
        Target(path=config.root),
        DigestOptions(
            cache_root=cache_root, ignore=config.ignore, budget_policy=config.budget.policy
        ),
        log=log,
    )
    side = Side.load(digest.out_dir)
    ledger = Ledger.load(config.ledger_path(cache_root), maw=config.root.name)
    return discover(side, config, ledger, settings or config.hunt, client=client)


def format_hunt(report: dict[str, Any]) -> str:
    lines = [
        "# Hunt shortlist",
        "",
        (
            "Search metadata suggests where to look. "
            "Digest pinned prey to verify its gaps and license."
        ),
        "",
        "| Prey | Score | Stars | Language | License |",
        "|---|---|---|---|---|",
    ]
    for row in report["candidates"]:
        lines.append(
            f"| {inline(row['prey'])} | {row['score']:.3f} | {row['stars']} | "
            f"{inline(row['language'] or 'unknown')} | {inline(row['license'] or 'unknown')} |"
        )
    lines += [
        "",
        f"Eligible: {report['eligible']}. Search or shortlist truncated: {report['truncated']}.",
    ]
    return "\n".join(lines) + "\n"
