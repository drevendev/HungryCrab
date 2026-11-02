"""Requested evidence reaches reassessment within the explicit local-round bound."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from qualification_cycle import Trial


@pytest.mark.parametrize("outcome", ["verified-gap", "request-more", "budget-refused"])
def test_two_requested_reads_are_assessed_or_remain_explicitly_unassessed(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    trial = Trial.__new__(Trial)
    trial.session = None
    trial.frozen = {"host": {"minimum_assessment_reserve": 1, "source_continuation_rounds": 2}}
    trial.meter = SimpleNamespace(remaining=30000, data={"usage_complete": True})  # type: ignore[assignment]
    trial.needs = [{"id": "need", "verification_mode": "design"}]
    trial.pool = {"donor": {}}
    lead = {"donor": "donor"}
    trial.state = {
        "phase": "research",
        "started": time.time(),
        "admissions": [],
        "outcomes": [],
        "plan": {"need": {"leads": [lead]}},
    }
    calls: list[tuple[str, str | None]] = []
    reads = []
    stops = []

    def decision(start: int, disposition: str = "request-more") -> dict[str, Any]:
        return {
            "disposition": disposition,
            "operations": [
                {"side": "donor", "path": "owned.py", "start": start, "end": start + 9},
            ]
            if disposition == "request-more"
            else [],
        }

    def assess(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        if kwargs.get("admission_only"):
            return {"admissible": True}
        stage = args[-1]
        basis = kwargs.get("continuation_of")
        calls.append((stage, basis[0] if basis else None))
        if stage.endswith("-2"):
            return None if outcome == "budget-refused" else decision(30, outcome)
        return decision(20 if basis else 10)

    def inspect(*args: Any) -> list[dict[str, Any]]:
        reads.append(args[2][0]["start"])
        return []

    def finish(answer: dict[str, Any], *_: Any) -> None:
        trial.state["outcomes"].append({"need": "need", "decision": answer})

    monkeypatch.setattr(trial, "assess", assess)
    monkeypatch.setattr(trial, "prepare_source", lambda *_: {"windows": []})
    monkeypatch.setattr(trial, "acquire", lambda *_: {"exploration": "owned"})
    monkeypatch.setattr(trial, "initial_source", lambda *_: [])
    monkeypatch.setattr(trial, "inspect", inspect)
    monkeypatch.setattr(trial, "save", lambda: None)
    monkeypatch.setattr(trial, "finish", finish)
    monkeypatch.setattr(trial, "stop", lambda reason: stops.append(reason))
    trial.run()
    assert reads == [10, 20]
    assert calls == [
        ("assess-0", None),
        ("assess-0-continued", "assess-0"),
        ("assess-0-continued-2", "assess-0-continued"),
    ]
    if outcome == "budget-refused":
        assert trial.state["unassessed_continuations"][0]["status"] == (
            "source-read; semantic reassessment unrun"
        )
        assert trial.state["outcomes"][0]["decision"]["disposition"] == "request-more"
    else:
        assert trial.state["outcomes"][0]["decision"]["disposition"] == outcome
    assert stops
