"""Deterministic pre-scoring of candidate nutrients.

Weights live in ``data/scoring.yml``; a host overrides them under ``scoring:`` in ``.crab.yml``.
The formula is intentionally simple so that ``crab tune`` can explain every suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from ..nutrients import Candidate
from ..typeutil import as_dict

SECTIONS = ("categories", "traits", "modes", "effort", "risk", "applicability")


def _floats(value: object) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, item in as_dict(value).items():
        if isinstance(item, int | float) and not isinstance(item, bool):
            out[str(key)] = float(item)
    return out


@dataclass
class Scoring:
    categories: dict[str, float]
    traits: dict[str, float]
    modes: dict[str, float]
    effort: dict[str, float]
    risk: dict[str, float]
    applicability: dict[str, float]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scoring:
        return cls(
            categories=_floats(data.get("categories")),
            traits=_floats(data.get("traits")),
            modes=_floats(data.get("modes")),
            effort=_floats(data.get("effort")),
            risk=_floats(data.get("risk")),
            applicability=_floats(data.get("applicability")),
        )

    @classmethod
    def default(cls) -> Scoring:
        text = resources.files("hungry_crab").joinpath("data/scoring.yml").read_text("utf-8")
        return cls.from_dict(as_dict(yaml.safe_load(text)))

    def merged(self, overrides: dict[str, Any] | None) -> Scoring:
        """A copy with every section updated from ``overrides`` (same layout as the YAML)."""
        if not overrides:
            return Scoring(**{name: dict(getattr(self, name)) for name in SECTIONS})
        merged: dict[str, dict[str, float]] = {}
        for name in SECTIONS:
            section = dict(getattr(self, name))
            section.update(_floats(overrides.get(name)))
            merged[name] = section
        return Scoring(**merged)

    def to_dict(self) -> dict[str, Any]:
        return {name: dict(getattr(self, name)) for name in SECTIONS}

    def value_for(self, candidate: Candidate) -> float:
        return self.traits.get(candidate.key, candidate.value)

    def applicability_for(self, kind: str) -> float:
        return self.applicability.get(kind, 1.0)

    def score(self, candidate: Candidate) -> float:
        category = self.categories.get(candidate.category, 0.5)
        mode = self.modes.get(candidate.license_mode, 0.3)
        effort = self.effort.get(candidate.effort, 0.75)
        risk = self.risk.get(candidate.risk, 0.1)
        raw = category * self.value_for(candidate) * candidate.applicability * mode * effort - risk
        return round(min(1.0, max(0.0, raw)), 2)

    def explain(self, candidate: Candidate) -> str:
        parts = [
            f"{self.categories.get(candidate.category, 0.5):.2f} (category)",
            f"{self.value_for(candidate):.2f} (value)",
            f"{candidate.applicability:.2f} (applicability)",
            f"{self.modes.get(candidate.license_mode, 0.3):.2f} ({candidate.license_mode})",
            f"{self.effort.get(candidate.effort, 0.75):.2f} (effort {candidate.effort})",
        ]
        risk = f"{self.risk.get(candidate.risk, 0.1):.2f} (risk {candidate.risk})"
        return " x ".join(parts) + " - " + risk
