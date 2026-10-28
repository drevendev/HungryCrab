"""Bounded supervised query plans. Search hints never establish source behavior."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .artifact_read import read_bytes
from .errors import UsageError
from .loop_config import mapping, strings

PLAN_SCHEMA = "hungry-crab.hunt-plan/1"
CONTEXT_KEYS = ("root", "sha", "selected_ref", "worktree", "policy", "ledger")


def read_plan(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise UsageError("hunt plan contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        value = json.loads(read_bytes(path, 64_000).decode("utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise UsageError("cannot read hunt plan as bounded UTF-8 JSON") from exc
    return mapping(value, "hunt plan", {"schema", "receiving_context", "hypotheses"})


def behavior_seeds(plan: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    data = mapping(plan, "hunt plan", {"schema", "receiving_context", "hypotheses"})
    selected = data.get("receiving_context")
    if (
        data.get("schema") != PLAN_SCHEMA
        or not isinstance(selected, dict)
        or any(key not in selected or selected[key] != context.get(key) for key in CONTEXT_KEYS)
        or context.get("worktree") in (None, "unknown")
    ):
        raise UsageError("hunt plan must match the current known receiving context")
    rows = data.get("hypotheses")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
        raise UsageError("hunt plan requires 1..20 hypotheses")
    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        row = mapping(raw, "hunt hypothesis", {"id", "facet", "behavior", "queries", "priority"})
        for key in ("id", "facet", "behavior"):
            value = row.get(key)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 256
                or any(ord(c) < 32 for c in value)
            ):
                raise UsageError(f"hunt hypothesis {key} must be bounded non-empty text")
        if row["id"] in seen:
            raise UsageError("hunt hypothesis ids must be unique")
        seen.add(row["id"])
        terms = strings(row.get("queries"), "hunt hypothesis queries")
        if not 1 <= len(terms) <= 4 or any(
            re.fullmatch(r'[\w .+/#"-]{1,120}', term) is None for term in terms
        ):
            raise UsageError(
                "hypothesis queries require 1..4 bounded keyword phrases "
                "without provider qualifiers"
            )
        priority = row.get("priority", 0.5)
        if (
            type(priority) not in (int, float)
            or not 0 <= priority <= 1
            or not math.isfinite(priority)
        ):
            raise UsageError("hypothesis priority must be finite and in 0..1")
        seeds.append(
            {
                "id": row["id"],
                "facet": row["facet"],
                "behavior": row["behavior"],
                "terms": [term + " in:readme" for term in dict.fromkeys(terms)],
                "priority": priority,
                "confidence": "unverified_behavior_hint",
            }
        )
    return sorted(seeds, key=lambda row: (-row["priority"], row["id"]))


def allocate_queries(
    seeds: list[dict[str, Any]], languages: list[str], *, neutral: bool, min_stars: int
) -> list[dict[str, Any]]:
    """Visit distinct facets before synonyms; rotate the first language across hypotheses."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for seed in seeds:
        grouped.setdefault(seed["facet"], []).append(seed)
    ordered = [
        rows[index]
        for index in range(max(map(len, grouped.values()), default=0))
        for rows in grouped.values()
        if index < len(rows)
    ]
    pools: list[str | None] = list(languages)
    if neutral or not pools:
        pools.append(None)
    result: list[dict[str, Any]] = []
    for synonym in range(max((len(seed["terms"]) for seed in ordered), default=0)):
        for offset in range(len(pools)):
            for index, seed in enumerate(ordered):
                if synonym >= len(seed["terms"]):
                    continue
                language = pools[(index + offset) % len(pools)]
                qualifier = f" language:{language}" if language else ""
                query = (
                    f"{seed['terms'][synonym]}{qualifier} "
                    f"archived:false fork:false stars:>={min_stars}"
                )
                result.append(
                    {
                        "query": query,
                        "hypothesis": seed["id"],
                        "facet": seed["facet"],
                        "behavior": seed.get("behavior"),
                        "language": language,
                        "priority": seed["priority"],
                        "confidence": seed["confidence"],
                    }
                )
    return result
