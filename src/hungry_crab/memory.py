"""Deterministic, maw-local learning from confirmed nutrient decisions."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .compare.scoring import Scoring
from .errors import UsageError
from .ledger import Ledger, LedgerEntry
from .loop_config import mapping, positive

POSITIVE = frozenset({"accepted", "merged"})
NEGATIVE = frozenset({"rejected"})


def decision(entry: LedgerEntry) -> str:
    return entry.feedback or entry.status


@dataclass
class MemorySettings:
    enabled: bool = True
    min_decisions: int = 3
    strength: float = 0.3

    @classmethod
    def load(cls, value: object) -> MemorySettings:
        data = mapping(value, "memory", {"enabled", "min_decisions", "strength"})
        enabled = data.get("enabled", True)
        strength = data.get("strength", 0.3)
        if not isinstance(enabled, bool):
            raise UsageError("memory.enabled must be a boolean")
        if (
            isinstance(strength, bool)
            or not isinstance(strength, int | float)
            or not math.isfinite(strength)
            or not 0 <= strength <= 0.5
        ):
            raise UsageError("memory.strength must be a finite number between 0 and 0.5")
        return cls(
            enabled,
            positive(data.get("min_decisions", 3), "memory.min_decisions"),
            float(strength),
        )


def confirmed(ledger: Ledger) -> list[LedgerEntry]:
    """One current decision per stable id; serving, sightings and ignores are not votes."""
    return sorted(
        (e for e in ledger.entries.values() if decision(e) in POSITIVE | NEGATIVE),
        key=lambda e: e.id,
    )


def counts(entries: list[LedgerEntry]) -> dict[str, int]:
    return {
        "accepted": sum(decision(e) in POSITIVE for e in entries),
        "rejected": sum(decision(e) in NEGATIVE for e in entries),
    }


def factor(stats: dict[str, int], settings: MemorySettings) -> float:
    total = stats["accepted"] + stats["rejected"]
    if not settings.enabled or total < settings.min_decisions:
        return 1.0
    # Two neutral prior observations bound small samples; the maximum change is strength.
    posterior = (stats["accepted"] + 1) / (total + 2)
    return round(1 + settings.strength * (2 * posterior - 1), 6)


@dataclass
class MemoryReport:
    enabled: bool
    decisions: int
    fingerprint: str
    min_decisions: int
    strength: float
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "hungry-crab.memory/1", **asdict(self)}


def learn(
    ledger: Ledger,
    scoring: Scoring,
    settings: MemorySettings,
    *,
    overrides: dict[str, Any] | None = None,
) -> tuple[Scoring, MemoryReport]:
    """Recompute from the baseline, never accumulate updates or override owner weights."""
    entries = confirmed(ledger)
    snapshot = [(e.id, decision(e)) for e in entries]
    fingerprint = hashlib.sha256(json.dumps(snapshot, separators=(",", ":")).encode()).hexdigest()
    report = MemoryReport(
        settings.enabled, len(entries), fingerprint, settings.min_decisions, settings.strength
    )
    result = scoring.merged(None)
    manual = (overrides or {}).get("categories", {})
    groups: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.category, []).append(entry)
    for category, group in sorted(groups.items()):
        stats = counts(group)
        multiplier = factor(stats, settings)
        current = scoring.categories.get(category, 0.5)
        owner_override = category in manual
        learned = current if owner_override else round(min(1.0, max(0.0, current * multiplier)), 6)
        result.categories[category] = learned
        report.categories[category] = {
            **stats,
            "factor": multiplier,
            "baseline": current,
            "effective": learned,
            "owner_override": owner_override,
        }
    return result, report
