"""``crab tune``: read the ledger and suggest which scoring weights to move, and how.

The suggestions are deliberately conservative and explainable: a category's weight moves only
after a few decisions, by a fixed step, and every line says which numbers it came from.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .compare.scoring import Scoring
from .host import HostConfig
from .ledger import NEGATIVE_STATUSES, Ledger
from .nutrients import ACCEPTED_STATUSES

STEP_UP = 0.1
STEP_DOWN = 0.15
FLOOR = 0.1
CEILING = 1.0
HIGH = 0.75
LOW = 0.25


@dataclass
class Suggestion:
    kind: str  # category | trait | appetite | prey
    target: str
    current: float | str | None
    suggested: float | str | None
    reason: str
    decisions: int
    acceptance: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TuneReport:
    decisions: int
    min_decisions: int
    suggestions: list[Suggestion] = field(default_factory=list)
    categories: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": self.decisions,
            "min_decisions": self.min_decisions,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "categories": self.categories,
        }

    def to_markdown(self) -> str:
        lines = ["# Tuning suggestions", ""]
        if self.decisions == 0:
            lines += [
                "No decisions in the ledger yet. Decide a few nutrients first:",
                "",
                '    crab ledger mark <id> accepted|rejected --reason "..."',
                "",
                "`crab serve --as issue` records `served` automatically.",
            ]
            return "\n".join(lines) + "\n"
        lines.append(
            f"{self.decisions} decisions in the ledger; a category needs at least "
            f"{self.min_decisions} before its weight moves."
        )
        lines.append("")
        if self.categories:
            lines += [
                "| Category | Accepted | Rejected | Proposed | Acceptance |",
                "|---|---|---|---|---|",
            ]
            for name, counts in sorted(self.categories.items()):
                accepted = counts.get("accepted", 0)
                rejected = counts.get("rejected", 0)
                decided = accepted + rejected
                rate = f"{accepted / decided * 100:.0f}%" if decided else "n/a"
                lines.append(
                    f"| {name} | {accepted} | {rejected} | {counts.get('proposed', 0)} | {rate} |"
                )
            lines.append("")
        if not self.suggestions:
            lines.append(
                "No change suggested: acceptance rates are in the middle or the samples are small."
            )
            return "\n".join(lines) + "\n"
        lines += ["## Suggestions", ""]
        for item in self.suggestions:
            change = (
                f"{item.current} -> {item.suggested}"
                if item.suggested is not None
                else str(item.current)
            )
            lines.append(f"- **{item.kind} {item.target}**: {change}. {item.reason}")
        lines += ["", "Apply the weight changes to .crab.yml with `crab tune --write`."]
        return "\n".join(lines) + "\n"


def _counts(entries: list[Any]) -> dict[str, int]:
    accepted = sum(1 for e in entries if e.status in ACCEPTED_STATUSES)
    rejected = sum(1 for e in entries if e.status in NEGATIVE_STATUSES)
    proposed = sum(1 for e in entries if e.status == "proposed")
    return {"accepted": accepted, "rejected": rejected, "proposed": proposed}


def analyse(ledger: Ledger, scoring: Scoring, *, min_decisions: int = 3) -> TuneReport:
    entries = list(ledger.entries.values())
    decided = [e for e in entries if e.status in ACCEPTED_STATUSES | NEGATIVE_STATUSES]
    report = TuneReport(decisions=len(decided), min_decisions=min_decisions)
    by_category: dict[str, list[Any]] = {}
    by_key: dict[str, list[Any]] = {}
    by_prey: dict[str, list[Any]] = {}
    for entry in entries:
        by_category.setdefault(entry.category, []).append(entry)
        by_key.setdefault(entry.key, []).append(entry)
        if entry.prey:
            by_prey.setdefault(entry.prey, []).append(entry)
    report.categories = {name: _counts(items) for name, items in sorted(by_category.items())}

    for name, items in sorted(by_category.items()):
        counts = _counts(items)
        total = counts["accepted"] + counts["rejected"]
        if total < min_decisions:
            continue
        rate = counts["accepted"] / total
        current = scoring.categories.get(name, 0.5)
        if rate >= HIGH and current < CEILING:
            report.suggestions.append(
                Suggestion(
                    "category", name, current, round(min(CEILING, current + STEP_UP), 2),
                    f"{counts['accepted']} of {total} decisions accepted ({rate * 100:.0f}%).",
                    total, round(rate, 2),
                )
            )  # fmt: skip
        elif rate <= LOW:
            if counts["accepted"] == 0 and total >= max(5, min_decisions):
                report.suggestions.append(
                    Suggestion(
                        "appetite", name, "on", "off",
                        f"none of {total} decisions accepted; consider switching the category off.",
                        total, 0.0,
                    )
                )  # fmt: skip
            if current > FLOOR:
                report.suggestions.append(
                    Suggestion(
                        "category", name, current, round(max(FLOOR, current - STEP_DOWN), 2),
                        f"only {counts['accepted']} of {total} decisions accepted "
                        f"({rate * 100:.0f}%).",
                        total, round(rate, 2),
                    )
                )  # fmt: skip

    for key, items in sorted(by_key.items()):
        counts = _counts(items)
        total = counts["accepted"] + counts["rejected"]
        if total < 2:
            continue
        current_value = scoring.traits.get(key, items[0].score if False else None)
        if counts["accepted"] == 0:
            report.suggestions.append(
                Suggestion(
                    "trait", key, current_value, 0.2,
                    f"rejected {total} times, never accepted; lower its value or ignore it.",
                    total, 0.0,
                )
            )  # fmt: skip
        elif counts["rejected"] == 0 and total >= 3:
            report.suggestions.append(
                Suggestion(
                    "trait", key, current_value, 1.0,
                    f"accepted {total} times without a rejection; give it the top value.",
                    total, 1.0,
                )
            )  # fmt: skip

    for prey, items in sorted(by_prey.items()):
        counts = _counts(items)
        total = counts["accepted"] + counts["rejected"]
        if total >= max(5, min_decisions) and counts["accepted"] / total <= 0.2:
            report.suggestions.append(
                Suggestion(
                    "prey", prey, None, None,
                    f"{counts['accepted']} of {total} nutrients from this prey were accepted; "
                    "it is a poor match for this host.",
                    total, round(counts["accepted"] / total, 2),
                )
            )  # fmt: skip
    return report


def apply(report: TuneReport, config: HostConfig) -> dict[str, Any]:
    """Write category and trait suggestions into ``.crab.yml`` under ``scoring``."""
    scoring = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in config.scoring.items()
    }
    categories = (
        dict(scoring.get("categories") or {}) if isinstance(scoring.get("categories"), dict) else {}
    )
    traits = dict(scoring.get("traits") or {}) if isinstance(scoring.get("traits"), dict) else {}
    changed = False
    for item in report.suggestions:
        if item.kind == "category" and isinstance(item.suggested, float):
            categories[item.target] = item.suggested
            changed = True
        elif item.kind == "trait" and isinstance(item.suggested, float):
            traits[item.target] = item.suggested
            changed = True
    if categories:
        scoring["categories"] = categories
    if traits:
        scoring["traits"] = traits
    if changed:
        config.write_scoring(scoring)
    return scoring
