"""Bad profiles cannot spend; known literal terms cannot bypass semantic planning."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
import qualification_cycle as cycle


@pytest.mark.parametrize(
    "change",
    [
        {"host": {}},
        {"reserve": None},
        {"reserve": True},
        {"reserve": 0},
        {"reserve": 30001},
        {"model_tokens": 30001},
        {"model_tokens": True},
        {"seconds": 1800},
    ],
)
def test_bad_profile_is_refused_before_commands_meter_or_model(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    profile = {
        "host": {"model": "owned-model", "effort": "low", "minimum_assessment_reserve": 6500},
        "limits": {"model_tokens": 30000, "seconds": 2700},
    }
    if "host" in change:
        profile["host"] = change["host"]
    elif "reserve" in change:
        profile["host"]["minimum_assessment_reserve"] = change["reserve"]
    else:
        profile["limits"].update(change)

    def forbidden(*_: Any, **__: Any) -> None:
        pytest.fail("Invalid profile reached an effect or native meter")

    monkeypatch.setattr(cycle, "Commands", forbidden)
    monkeypatch.setattr(cycle, "Meter", forbidden)
    with pytest.raises(ValueError):
        cycle.Trial("owned-candidate-1", profile)


@pytest.mark.parametrize("explicit", [False, True])
def test_configured_semantic_planner_runs_with_or_without_known_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    trial = cycle.Trial.__new__(cycle.Trial)
    trial.arm = "candidate"
    trial.root = tmp_path
    trial.frozen = {"host": {"candidate_metadata_seeds": True, "candidate_read_planner": True}}
    trial.needs = [{"id": "owned", "queries": ["lunar descriptor"], "verification_mode": "design"}]
    if explicit:
        trial.needs[0]["terms"] = ["optional"]
    trial.pool = {
        "source": {
            "id": "source",
            "repository": "owned/source",
            "sha": "a" * 40,
            "view": str(tmp_path),
            "description": "Lunar descriptor",
        }
    }
    trial.commands = SimpleNamespace(
        json=lambda *_: {
            "needs": [
                {
                    "need": "owned",
                    "leads": [
                        {
                            "repository": "owned/source",
                            "sha": "a" * 40,
                            "terms": ["optional"],
                            "reason": "hint",
                        }
                    ],
                }
            ]
        }
    )
    trial.state = {"phase": "planning"}
    calls = []

    def qualify(seeds: dict[str, Any]) -> dict[str, Any]:
        calls.append(seeds)
        return seeds

    monkeypatch.setattr(trial, "refine_seeds", qualify)
    monkeypatch.setattr(trial, "save", lambda: None)
    assert trial.planner()
    assert len(calls) == 1 and trial.state["phase"] == "cases"
