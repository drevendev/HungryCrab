from __future__ import annotations

import pytest

from hungry_crab.compare.scoring import Scoring
from hungry_crab.nutrients import Candidate


def _card(**overrides: object) -> Candidate:
    base: dict[str, object] = {
        "category": "ci",
        "key": "ci.cache",
        "title": "Cache",
        "what": "caches",
        "serve_as": "pr",
        "effort": "S",
        "risk": "low",
        "value": 0.9,
        "uptake": 1.0,
        "license_mode": "COPY",
    }
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def test_default_weights_load_from_package_data() -> None:
    scoring = Scoring.default()
    assert scoring.categories["ci"] == 0.9
    assert scoring.modes["COPY"] == 1.0
    assert scoring.effort["S"] == 1.0
    assert scoring.risk["low"] == 0.0
    assert scoring.uptake["other_stack"] < scoring.uptake["same_stack"]
    assert scoring.traits == {}


def test_score_formula() -> None:
    scoring = Scoring.default()
    assert scoring.score(_card()) == pytest.approx(0.81)
    assert scoring.score(_card(license_mode="IDEAS_ONLY")) == pytest.approx(0.32)
    assert scoring.score(_card(effort="L", risk="high")) == pytest.approx(0.16)
    assert scoring.score(_card(uptake=0.25, effort="M", risk="medium")) == pytest.approx(0.05)
    assert scoring.score(_card(category="unknown", value=0.0)) == 0.0


def test_overrides_merge_without_touching_the_default() -> None:
    default = Scoring.default()
    tuned = default.merged({"categories": {"ci": 0.5}, "traits": {"ci.cache": 1.0}})
    assert tuned.categories["ci"] == 0.5
    assert tuned.categories["tests"] == default.categories["tests"]
    assert tuned.traits == {"ci.cache": 1.0}
    assert default.categories["ci"] == 0.9
    assert tuned.score(_card()) == pytest.approx(0.5)
    assert tuned.to_dict()["categories"]["ci"] == 0.5
    assert "0.50 (category)" in tuned.explain(_card())
