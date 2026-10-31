"""Provider search adapters and one-hop frontiers; metadata is never licence proof."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from .cache import Slug
from .errors import CrabError
from .typeutil import as_dict, as_list


class SearchClient(Protocol):
    def get(self, path: str, *, allow_missing: bool = False) -> Any: ...


@dataclass
class SearchBudget:
    maximum: int = 16
    used: int = 0

    def charge(self) -> None:
        if (
            type(self.maximum) is not int
            or not 1 <= self.maximum <= 16
            or self.used >= self.maximum
        ):
            raise CrabError("provider search request budget exhausted")
        self.used += 1


def search(
    query: str,
    *,
    provider: str = "github",
    surface: str = "repository",
    budget: SearchBudget | None = None,
    client: SearchClient | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Exactly one bounded provider request with explicit capability/partial state."""
    if provider not in {"github", "gitlab"} or surface not in {"repository", "code"}:
        raise CrabError("unsupported search provider or surface")
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > 256
        or any(ord(c) < 32 for c in query)
    ):
        raise CrabError("search query must be bounded nonempty text")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CrabError("search limit must be in 1..100")
    result: dict[str, Any] = {
        "provider": provider,
        "surface": surface,
        "query": query,
        "status": "not-acquired",
        "candidates": [],
        "requests": 0,
        "license_proof": False,
        "index_coverage": "unknown",
    }
    if provider == "gitlab" and surface == "code":
        result.update(status="unsupported", reason="global-code-search-capability-not-established")
        return result
    if client is None:
        if provider == "github":
            from .fetch.github import GitHubClient

            client = GitHubClient(retries=0)
        else:
            from .fetch.gitlab import GitLabClient

            client = GitLabClient()
    owned_budget = budget or SearchBudget()
    owned_budget.charge()
    result["requests"] = 1
    params = (
        {"q": query, "per_page": limit}
        if provider == "github"
        else {"search": query, "per_page": limit, "simple": "true"}
    )
    endpoint = (
        ("search/code" if surface == "code" else "search/repositories")
        if provider == "github"
        else "projects"
    )
    try:
        raw = client.get(endpoint + "?" + urlencode(params))
    except CrabError as exc:
        from .hunt import _provider_diagnostic

        result.update(
            status="unavailable",
            reason="authentication-rate-limit-or-provider-error",
            error=_provider_diagnostic(exc.message),
        )
        return result
    data = as_dict(raw)
    rows = as_list(data.get("items")) if provider == "github" else as_list(raw)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in rows[:limit]:
        item = as_dict(value)
        repository = as_dict(item.get("repository")) if surface == "code" else item
        name = (
            repository.get("full_name")
            if provider == "github"
            else repository.get("path_with_namespace")
        )
        if not isinstance(name, str):
            continue
        try:
            slug = Slug.parse(name if provider == "github" else "https://gitlab.com/" + name)
        except CrabError:
            continue
        if str(slug).casefold() in seen:
            continue
        seen.add(str(slug).casefold())
        private = (
            repository.get("private")
            if provider == "github"
            else repository.get("visibility") != "public"
        )
        if private is not False:
            # Search alone does not authorise exporting private repository facts.
            continue
        path = item.get("path") if surface == "code" else None
        candidates.append(
            {
                "repository": str(slug),
                "url": slug.url,
                "provider": provider,
                "discovery_path": path if isinstance(path, str) and len(path) <= 500 else None,
                "sha": None,
                "qualified": False,
                "reason": "moving-index-hit; acquire and verify an immutable revision",
                "blob_sha_is_not_commit": surface == "code",
            }
        )
    result.update(
        status="partial" if (data.get("incomplete_results") or len(rows) >= limit) else "acquired",
        candidates=candidates,
        index_coverage="provider-index-only",
    )
    return result


def verified_frontier(
    roots: list[dict[str, Any]],
    resolve: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    maximum: int = 8,
) -> dict[str, Any]:
    """One verified hop, no package install, URL guessing or recursive expansion."""
    if type(maximum) is not int or not 1 <= maximum <= 32 or len(roots) > 100:
        raise CrabError("frontier budget must be bounded")
    edges: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, root in enumerate(roots):
        if index >= maximum:
            omitted.append({"index": index, "reason": "frontier-budget"})
            continue
        if root.get("relationship") not in {"dependency", "adjacent-tool", "consumer"}:
            omitted.append({"index": index, "reason": "unsupported-relationship"})
            continue
        resolved = resolve(root)
        if not resolved or resolved.get("verified") is not True or not resolved.get("evidence"):
            omitted.append({"index": index, "reason": "identity-not-verified"})
            continue
        try:
            slug = Slug.parse(str(resolved.get("repository", "")))
        except CrabError:
            omitted.append({"index": index, "reason": "invalid-repository-identity"})
            continue
        key = str(slug).casefold()
        if key in seen or key == str(root.get("from", "")).casefold():
            omitted.append({"index": index, "reason": "cycle-or-duplicate"})
            continue
        sha = resolved.get("sha")
        if not isinstance(sha, str) or re.fullmatch(r"[a-f0-9]{40,64}", sha) is None:
            omitted.append({"index": index, "reason": "immutable-revision-unavailable"})
            continue
        seen.add(key)
        edges.append(
            {
                "from": root.get("from"),
                "relationship": root["relationship"],
                "repository": str(slug),
                "sha": sha,
                "evidence": resolved["evidence"],
                "depth": 1,
                "head_only": resolved.get("head_only", False),
            }
        )
    return {
        "edges": edges,
        "omissions": omitted,
        "maximum_depth": 1,
        "requests": min(len(roots), maximum),
    }


def rank_cohort(
    rows: list[dict[str, Any]], *, limit: int = 10, seen: set[str] | None = None
) -> dict[str, Any]:
    """Greedy marginal coverage after relevance; keep all occurrences and reasons."""
    if len(rows) > 200 or type(limit) is not int or not 1 <= limit <= 50:
        raise CrabError("cohort ranking exceeds candidate/selection bounds")
    remaining = list(enumerate(rows))
    covered: set[str] = set()
    lineage: set[str] = set()
    selected: list[dict[str, Any]] = []
    prior = {value.casefold() for value in (seen or set())}
    while remaining and len(selected) < limit:
        scored: list[tuple[float, str, int, dict[str, Any], set[str]]] = []
        for index, row in remaining:
            relevance = row.get("relevance", 0)
            if type(relevance) not in {float, int} or not 0 < relevance <= 1:
                continue
            needs = {str(value) for value in as_list(row.get("needs"))[:20]}
            marginal = needs - covered
            repo = str(row.get("repository", ""))
            family = row.get("verified_lineage")
            if family is not None and not isinstance(family, str):
                raise CrabError("verified lineage must be an evidence-backed string identity")
            penalty = (0.2 if repo.casefold() in prior else 0.0) + (
                0.3 if family and family in lineage else 0.0
            )
            score = float(relevance) + min(len(marginal), 8) * 0.15 - penalty
            scored.append((score, repo, index, row, marginal))
        if not scored:
            break
        score, _, index, row, marginal = sorted(scored, key=lambda r: (-r[0], r[1], r[2]))[0]
        selected.append(
            {
                **row,
                "coverage_contribution": sorted(marginal),
                "cohort_score": round(score, 4),
                "previously_seen": str(row.get("repository", "")).casefold() in prior,
                "equivalence_status": "proposed-unless-evidenced",
            }
        )
        covered.update(marginal)
        if isinstance(row.get("verified_lineage"), str):
            lineage.add(row["verified_lineage"])
        remaining = [(i, r) for i, r in remaining if i != index]
    return {
        "selected": selected,
        "remaining": [row for _, row in remaining],
        "covered_needs": sorted(covered),
        "raw_occurrences": len(rows),
        "votes_migrated": False,
    }
