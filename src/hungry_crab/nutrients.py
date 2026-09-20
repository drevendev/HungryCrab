"""Nutrient cards: the unit of value the crab serves.

A card is a fact (what the prey has and the maw lacks) plus judgment slots (why it matters for
this maw, how to do it) that a model fills in later. Ids are stable across runs: maw-relative
nutrients are ``crab:<category>:<key>`` regardless of which prey suggested them, so the ledger
and the issue markers deduplicate across prey; prey-specific lessons carry the prey in the key.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .licensing.origin import ContentOrigin, cap_mode_for_origin, normalize_origin
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
# A category may be declared before anything produces it, but never silently: a knob in
# `.crab.yml` and a weight in `scoring.yml` that no meal can reach look exactly like a category
# somebody forgot. Every entry here names the milestone that owes the producer, and
# `tests/test_categories.py` refuses a declared category that is neither produced nor listed.
DEFERRED_CATEGORIES: dict[str, str] = {
    "code": "0.4 Deep Bite: symbol-level nutrients from tree-sitter symbols and the call graph",
}
SERVE_AS: tuple[str, ...] = ("pr", "issue", "idea")
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
    maw_state: str = ""
    serve_as: str = "issue"
    effort: str = "M"
    risk: str = "low"
    value: float = 0.5
    uptake: float = 1.0
    evidence: list[Evidence] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    origin: str = ContentOrigin.LICENSED.value
    license_reason: str = ""
    license_mode: str = "HUMAN"
    score: float = 0.0
    why: str = ""
    how: str = ""
    status: str = "proposed"
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Issue titles and discussion prose are not licensed by the repository. Treat legacy
        # issue-lesson cards that predate the origin field as commenter-origin too.
        if self.category == "issue-lesson" and self.origin == ContentOrigin.LICENSED.value:
            self.origin = ContentOrigin.COMMENTERS.value
        else:
            self.origin = normalize_origin(self.origin).value
        self._enforce_origin_policy()
        self.license_mode = self.license_mode

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "origin":
            object.__setattr__(self, name, normalize_origin(value).value)
            return
        if name == "license_mode":
            origin = getattr(self, "origin", ContentOrigin.LICENSED.value)
            capped = cap_mode_for_origin(value, origin)
            object.__setattr__(self, name, capped.mode.value)
            object.__setattr__(self, "license_reason", capped.reason)
            self._refresh_trace()
            return
        if name == "trace" and isinstance(value, dict):
            enriched = dict(value)
            origin = getattr(self, "origin", ContentOrigin.LICENSED.value)
            enriched["content_origin"] = origin
            reason = getattr(self, "license_reason", "")
            if reason:
                enriched["origin_license_reason"] = reason
            object.__setattr__(self, name, enriched)
            return
        object.__setattr__(self, name, value)

    def _refresh_trace(self) -> None:
        trace = getattr(self, "trace", None)
        if not isinstance(trace, dict):
            return
        trace["content_origin"] = getattr(self, "origin", ContentOrigin.LICENSED.value)
        reason = getattr(self, "license_reason", "")
        if reason:
            trace["origin_license_reason"] = reason
        else:
            trace.pop("origin_license_reason", None)

    def _enforce_origin_policy(self) -> None:
        if self.origin == ContentOrigin.LICENSED.value:
            return
        # The card may carry the need and the evidence link, but never third-party prose. Keep
        # the wording intentionally generic so a sentinel issue title cannot travel through a
        # menu or a model-written note under IDEAS_ONLY.
        self.title = "Issue-derived demand signal"
        self.what = (
            "Issue metadata indicates unmet demand in the prey. Follow the linked issue evidence "
            "to understand the need; commenter text is intentionally not carried into this card."
        )

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
        card._enforce_origin_policy()
        card._refresh_trace()
        return card


def merge_notes(card: Candidate, notes: dict[str, Any]) -> Candidate:
    """Apply model-written fields (title, why, how, serve_as, ...) onto a card."""
    for key in ("title", "what", "why", "how", "serve_as", "effort", "risk"):
        value = notes.get(key)
        if isinstance(value, str) and value.strip():
            setattr(card, key, value.strip())
    card._enforce_origin_policy()
    return card
