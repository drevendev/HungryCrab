"""The ledger: what was eaten, what was proposed, and what the maw decided.

The ledger is the crab's memory for one maw. It makes repeated meals idempotent (a nutrient
that was rejected or served is not proposed again) and it is the raw material for ``crab tune``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config_edit import atomic_text
from .errors import CrabError
from .file_lock import state_lock
from .nutrients import ACCEPTED_STATUSES, STATUSES, Candidate
from .typeutil import as_dict, as_list

LEDGER_SCHEMA = "hungry-crab.ledger/1"
NEGATIVE_STATUSES = frozenset({"rejected", "ignored"})


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"duplicate ledger JSON member {key!r}")
        data[key] = value
    return data


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


@dataclass
class LedgerEntry:
    """One nutrient as this maw has seen it.

    Ids are maw-relative, so the same nutrient is proposed by many prey. ``prey`` and ``sha``
    name the one that proposed it *first* and are never overwritten: that is the prey that found
    it, and the one ``crab tune`` credits a decision to. Later sightings move ``last_prey``,
    ``last_sha``, ``last_seen`` and ``score``, and count in ``sightings``.
    """

    id: str
    category: str
    key: str
    title: str
    status: str = "proposed"
    prey: str = ""
    sha: str = ""
    score: float = 0.0
    serve_as: str = "issue"
    first_seen: str = ""
    last_seen: str = ""
    last_prey: str = ""
    last_sha: str = ""
    sightings: int = 1
    decided_at: str | None = None
    reason: str = ""
    url: str | None = None
    feedback: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra")
        return {**extra, **data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        known = {item.name for item in fields(cls)} - {"extra"}
        kwargs = {key: value for key, value in data.items() if key in known and key != "extra"}
        kwargs["extra"] = {key: value for key, value in data.items() if key not in known}
        for required in ("id", "category", "key", "title"):
            kwargs.setdefault(required, "")
        try:
            kwargs["sightings"] = max(1, int(kwargs.get("sightings") or 1))
        except (TypeError, ValueError):
            kwargs["sightings"] = 1
        entry = cls(**kwargs)
        if (
            any(
                not isinstance(getattr(entry, name), str)
                for name in ("id", "category", "key", "title", "prey", "sha", "status")
            )
            or not entry.id
        ):
            raise CrabError("ledger id must be non-empty and entry metadata must be strings")
        if (
            isinstance(entry.score, bool)
            or not isinstance(entry.score, int | float)
            or not math.isfinite(entry.score)
        ):
            raise CrabError("ledger entry score must be a finite number")
        if entry.feedback not in {None, "accepted", "merged", "rejected"}:
            raise CrabError("ledger contains invalid confirmed feedback")
        if entry.status not in STATUSES:
            raise CrabError(f"ledger contains unknown status {entry.status!r}")
        # A ledger written before sightings were recorded knows one prey per entry: the last
        # one, because every meal overwrote it. That is still the best guess for both.
        entry.last_prey = entry.last_prey or entry.prey
        entry.last_sha = entry.last_sha or entry.sha
        return entry

    @classmethod
    def from_candidate(cls, card: Candidate, *, now: datetime | None = None) -> LedgerEntry:
        stamp = _stamp(now)
        prey = str(card.trace.get("prey", ""))
        sha = str(card.trace.get("sha", ""))
        return cls(
            id=card.id,
            category=card.category,
            key=card.key,
            title=card.title,
            status=card.status if card.status in STATUSES else "proposed",
            prey=prey,
            sha=sha,
            score=card.score,
            serve_as=card.serve_as,
            first_seen=stamp,
            last_seen=stamp,
            last_prey=prey,
            last_sha=sha,
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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra")
        return {**extra, **data}

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
            extra={
                key: value
                for key, value in data.items()
                if key not in {f.name for f in fields(cls)} - {"extra"}
            },
        )


class Ledger:
    def __init__(self, path: Path | None, *, maw: str = "") -> None:
        self.path = path
        self.maw = maw
        self.entries: dict[str, LedgerEntry] = {}
        self.meals: list[Meal] = []
        self.extra: dict[str, Any] = {}
        self._snapshot: bytes | None = None

    @classmethod
    def load(cls, path: Path | None, *, maw: str = "") -> Ledger:
        ledger = cls(path, maw=maw)
        if path is None or not path.is_file():
            return ledger
        try:
            raw = path.read_bytes()
            loaded = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
            ledger._snapshot = hashlib.sha256(raw).digest()
            if not isinstance(loaded, dict):
                raise ValueError("ledger must be an object")
            data = loaded
        except (OSError, ValueError) as exc:
            raise CrabError(f"ledger {path} is not valid JSON: {exc}") from exc
        ledger.maw = str(data.get("maw") or maw)
        if data.get("schema", LEDGER_SCHEMA) != LEDGER_SCHEMA:
            raise CrabError(
                f"unsupported ledger schema {data.get('schema')!r}; refusing to rewrite it"
            )
        for name in ("entries", "meals"):
            if not isinstance(data.get(name, []), list) or any(
                not isinstance(item, dict) for item in data.get(name, [])
            ):
                raise CrabError(f"ledger {name} must be a list of objects")
        ledger.extra = {
            key: value
            for key, value in data.items()
            if key not in {"schema", "maw", "updated_at", "entries", "meals"}
        }
        for item in as_list(data.get("entries")):
            try:
                entry = LedgerEntry.from_dict(as_dict(item))
            except (TypeError, ValueError) as exc:
                raise CrabError("malformed ledger entry; refusing to rewrite it") from exc
            if entry.id:
                if entry.id in ledger.entries:
                    raise CrabError(f"ledger contains duplicate nutrient id {entry.id!r}")
                ledger.entries[entry.id] = entry
        for item in as_list(data.get("meals")):
            try:
                ledger.meals.append(Meal.from_dict(as_dict(item)))
            except (TypeError, ValueError) as exc:
                raise CrabError("malformed ledger meal; refusing to rewrite it") from exc
        return ledger

    def to_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            **self.extra,
            "schema": LEDGER_SCHEMA,
            "maw": self.maw,
            "updated_at": _stamp(now),
            "meals": [meal.to_dict() for meal in self.meals],
            "entries": [self.entries[key].to_dict() for key in sorted(self.entries)],
        }

    def save(self, *, now: datetime | None = None) -> Path | None:
        if self.path is None:
            return None
        with state_lock(self.path):
            current = (
                hashlib.sha256(self.path.read_bytes()).digest() if self.path.exists() else None
            )
            if current != self._snapshot:
                raise CrabError(
                    "ledger changed since it was loaded; refusing to lose decisions",
                    hint="reload the ledger and reconcile before retrying",
                )
            text = (
                json.dumps(self.to_dict(now=now), indent=2, ensure_ascii=False, allow_nan=False)
                + "\n"
            )
            atomic_text(self.path, text)
            self._snapshot = hashlib.sha256(text.encode("utf-8")).digest()
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
            existing.last_prey = str(card.trace.get("prey", existing.last_prey))
            existing.last_sha = str(card.trace.get("sha", existing.last_sha))
            existing.sightings += 1
            if not existing.prey:
                # An entry that never learnt who proposed it: the earliest known prey is this.
                existing.prey, existing.sha = existing.last_prey, existing.last_sha
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
        if status in {"accepted", "merged", "rejected"}:
            entry.feedback = status
        elif status in {"proposed", "ignored"}:
            entry.feedback = None
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
