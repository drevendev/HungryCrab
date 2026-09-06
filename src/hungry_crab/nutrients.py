"""Nutrient cards: the unit of value the crab serves.

A card is a fact (what the prey has and the host lacks) plus judgment slots (why it matters for
this host, how to do it) that a model fills in later. Ids are stable across runs: host-relative
nutrients are ``crab:<category>:<key>`` regardless of which prey suggested them, so the ledger
and the issue markers deduplicate across prey; prey-specific lessons carry the prey in the key.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .typeutil import as_list

CATEGORIES: tuple[str, ...] = (
    "security",
    "ci",
    "tests",
    "tooling",
    "ai-config",
    "hygiene",
    "docs",
    "deps",
    "history-lesson",
    "issue-lesson",
    "architecture",
    "code",
)
ARTIFACTS: tuple[str, ...] = ("pr", "issue", "idea")
EFFORTS: tuple[str, ...] = ("S", "M", "L")
RISKS: tuple[str, ...] = ("low", "medium", "high")
STATUSES: tuple[str, ...] = ("proposed", "accepted", "rejected", "served", "merged", "ignored")
ACCEPTED_STATUSES: frozenset[str] = frozenset({"accepted", "served", "merged"})


def make_id(category: str, key: str) -> str:
    return f"crab:{category}:{key}"


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def slugify(text: str, *, limit: int = 60) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts)[:limit].strip("-") or "x"


@dataclass
class Evidence:
    path: str
    url: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "url": self.url, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        url = data.get("url")
        return cls(
            path=str(data.get("path", "")),
            url=url if isinstance(url, str) else None,
            note=str(data.get("note", "")),
        )


@dataclass
class Candidate:
    category: str
    key: str
    title: str
    what: str
    prey_state: str = ""
    host_state: str = ""
    artifact: str = "issue"
    effort: str = "M"
    risk: str = "low"
    value: float = 0.5
    applicability: float = 1.0
    evidence: list[Evidence] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    license_mode: str = "HUMAN"
    score: float = 0.0
    why_for_host: str = ""
    how: str = ""
    status: str = "proposed"
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return make_id(self.category, self.key)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = self.id
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        known = {item.name for item in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known and key != "evidence"}
        card = cls(**kwargs)
        card.evidence = [
            Evidence.from_dict(item)
            for item in as_list(data.get("evidence"))
            if isinstance(item, dict)
        ]
        return card


def merge_notes(card: Candidate, notes: dict[str, Any]) -> Candidate:
    """Apply model-written fields (title, why_for_host, how, artifact, ...) onto a card."""
    for key in ("title", "what", "why_for_host", "how", "artifact", "effort", "risk"):
        value = notes.get(key)
        if isinstance(value, str) and value.strip():
            setattr(card, key, value.strip())
    return card
