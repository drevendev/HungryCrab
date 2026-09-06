"""The ledger: what was eaten, what was proposed, and what the maw decided.

The ledger is the crab's memory for one maw. It makes repeated meals idempotent (a nutrient
that was rejected or served is not proposed again) and it is the raw material for ``crab tune``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import CrabError
from .nutrients import ACCEPTED_STATUSES, STATUSES, Candidate
from .typeutil import as_dict, as_list

LEDGER_SCHEMA = "hungry-crab.ledger/1"
NEGATIVE_STATUSES = frozenset({"rejected", "ignored"})


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


@dataclass
class LedgerEntry:
    id: str
    category: str
    key: str
    title: str
    status: str = "proposed"
    prey: str = ""
    sha: str = ""
    score: float = 0.0
    artifact: str = "issue"
    first_seen: str = ""
    last_seen: str = ""
    decided_at: str | None = None
    reason: str = ""
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        known = {item.name for item in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        for required in ("id", "category", "key", "title"):
            kwargs.setdefault(required, "")
        return cls(**kwargs)

    @classmethod
    def from_candidate(cls, card: Candidate, *, now: datetime | None = None) -> LedgerEntry:
        stamp = _stamp(now)
        return cls(
            id=card.id,
            category=card.category,
            key=card.key,
            title=card.title,
            status=card.status if card.status in STATUSES else "proposed",
            prey=str(card.provenance.get("prey", "")),
            sha=str(card.provenance.get("sha", "")),
            score=card.score,
            artifact=card.artifact,
            first_seen=stamp,
            last_seen=stamp,
        )


@dataclass
class Meal:
    prey: str
    sha: str
    url: str | None
    license: str | None
    mode: str
    date: str
    candidates: int
    new: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Meal:
        return cls(
            prey=str(data.get("prey", "")),
            sha=str(data.get("sha", "")),
            url=data.get("url") if isinstance(data.get("url"), str) else None,
            license=data.get("license") if isinstance(data.get("license"), str) else None,
            mode=str(data.get("mode", "")),
            date=str(data.get("date", "")),
            candidates=int(data.get("candidates", 0) or 0),
            new=int(data.get("new", 0) or 0),
        )


class Ledger:
    def __init__(self, path: Path | None, *, maw: str = "") -> None:
        self.path = path
        self.maw = maw
        self.entries: dict[str, LedgerEntry] = {}
        self.meals: list[Meal] = []

    @classmethod
    def load(cls, path: Path | None, *, maw: str = "") -> Ledger:
        ledger = cls(path, maw=maw)
        if path is None or not path.is_file():
            return ledger
        try:
            data = as_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise CrabError(f"ledger {path} is not valid JSON: {exc}") from exc
        ledger.maw = str(data.get("maw") or maw)
        for item in as_list(data.get("entries")):
            entry = LedgerEntry.from_dict(as_dict(item))
            if entry.id:
                ledger.entries[entry.id] = entry
        for item in as_list(data.get("meals")):
            ledger.meals.append(Meal.from_dict(as_dict(item)))
        return ledger

    def to_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "schema": LEDGER_SCHEMA,
            "maw": self.maw,
            "updated_at": _stamp(now),
            "meals": [meal.to_dict() for meal in self.meals],
            "entries": [self.entries[key].to_dict() for key in sorted(self.entries)],
        }

    def save(self, *, now: datetime | None = None) -> Path | None:
        if self.path is None:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(now=now), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return self.path

    def ensure(self, card: Candidate, *, now: datetime | None = None) -> LedgerEntry:
        entry = self.entries.get(card.id)
        if entry is None:
            entry = LedgerEntry.from_candidate(card, now=now)
            self.entries[card.id] = entry
        return entry

    def record_meal(
        self, menu: dict[str, Any], candidates: Iterable[Candidate], *, now: datetime | None = None
    ) -> int:
        """Add new candidates as ``proposed``, refresh the rest; return how many were new."""
        stamp = _stamp(now)
        new = 0
        count = 0
        for card in candidates:
            count += 1
            existing = self.entries.get(card.id)
            if existing is None:
                self.entries[card.id] = LedgerEntry.from_candidate(card, now=now)
                new += 1
                continue
            existing.last_seen = stamp
            existing.score = card.score
            existing.prey = str(card.provenance.get("prey", existing.prey))
            existing.sha = str(card.provenance.get("sha", existing.sha))
        prey = as_dict(menu.get("prey"))
        verdict = as_dict(menu.get("verdict"))
        self.meals.append(
            Meal(
                prey=str(prey.get("label", "")),
                sha=str(prey.get("sha", "")),
                url=prey.get("url") if isinstance(prey.get("url"), str) else None,
                license=prey.get("license") if isinstance(prey.get("license"), str) else None,
                mode=str(verdict.get("mode", "")),
                date=stamp,
                candidates=count,
                new=new,
            )
        )
        return new

    def mark(
        self,
        nutrient_id: str,
        status: str,
        *,
        reason: str = "",
        url: str | None = None,
        now: datetime | None = None,
    ) -> LedgerEntry:
        if status not in STATUSES:
            raise CrabError(f"unknown status {status!r}", hint=f"use one of: {', '.join(STATUSES)}")
        entry = self.entries.get(nutrient_id)
        if entry is None:
            raise CrabError(
                f"unknown nutrient id {nutrient_id!r}",
                hint="ids are listed in menu.md after `crab compare`",
            )
        entry.status = status
        entry.decided_at = _stamp(now)
        if reason:
            entry.reason = reason
        if url:
            entry.url = url
        return entry

    def hidden_ids(self) -> dict[str, str]:
        """Nutrients the menu should not show again, with the reason."""
        hidden: dict[str, str] = {}
        for entry in self.entries.values():
            if entry.status == "rejected":
                hidden[entry.id] = "ledger: rejected" + (
                    f" ({entry.reason})" if entry.reason else ""
                )
            elif entry.status in ("served", "merged"):
                hidden[entry.id] = f"ledger: {entry.status}" + (
                    f" {entry.url}" if entry.url else ""
                )
            elif entry.status == "ignored":
                hidden[entry.id] = "ledger: ignored"
        return hidden

    def decisions(self) -> list[LedgerEntry]:
        return [
            e for e in self.entries.values() if e.status in ACCEPTED_STATUSES | NEGATIVE_STATUSES
        ]

    def stats(self) -> dict[str, Any]:
        by_status: Counter[str] = Counter(e.status for e in self.entries.values())
        by_category: dict[str, Counter[str]] = {}
        by_key: dict[str, Counter[str]] = {}
        by_prey: dict[str, Counter[str]] = {}
        for entry in self.entries.values():
            by_category.setdefault(entry.category, Counter())[entry.status] += 1
            by_key.setdefault(entry.key, Counter())[entry.status] += 1
            if entry.prey:
                by_prey.setdefault(entry.prey, Counter())[entry.status] += 1
        return {
            "entries": len(self.entries),
            "meals": len(self.meals),
            "by_status": dict(by_status),
            "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
            "by_key": {k: dict(v) for k, v in sorted(by_key.items())},
            "by_prey": {k: dict(v) for k, v in sorted(by_prey.items())},
        }
