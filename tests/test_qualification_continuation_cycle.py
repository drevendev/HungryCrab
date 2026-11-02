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


def test_oversized_preparation_continues_to_a_distinct_need_in_the_invoked_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial = Trial.__new__(Trial)
    trial.session = Path("owned-session")
    trial.arm = "candidate"
    trial.frozen = {"host": {"minimum_assessment_reserve": 1, "defer_oversized_source": True}}
    trial.meter = SimpleNamespace(remaining=20000, data={"usage_complete": True})  # type: ignore[assignment]
    trial.needs = [{"id": name, "verification_mode": "design"} for name in ("first", "second")]
    trial.pool = {"large": {"repository": "owned/large"}, "small": {"repository": "owned/small"}}
    trial.state = {
        "phase": "research",
        "started": time.time(),
        "admissions": [],
        "outcomes": [],
        "plan": {
            "first": {"leads": [{"donor": "large"}]},
            "second": {"leads": [{"donor": "small"}]},
        },
    }
    actions = iter(
        [
            {
                "action": "acquire",
                "attempt": "first-owned",
                "need": "first",
                "lead": {"repository": "owned/large"},
            },
            {
                "action": "acquire",
                "attempt": "second-owned",
                "need": "second",
                "lead": {"repository": "owned/small"},
            },
            {"finished": True, "stop_reason": "scoped-goals-met"},
        ]
    )
    prepared, assessed, receipts, stops = [], [], [], []

    def next_action() -> dict[str, Any]:
        trial.action = next(actions)
        return trial.action

    def prepare(lead: dict[str, Any], *_: Any) -> dict[str, Any]:
        prepared.append(lead["donor"])
        return {"windows": []}

    def assess(lead: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("admission_only"):
            return {"admissible": lead["donor"] != "large", "remaining": 20000, "estimate": 22000}
        assessed.append(lead["donor"])
        return {"disposition": "verified-gap", "operations": []}

    monkeypatch.setattr(trial, "next", next_action)
    monkeypatch.setattr(trial, "prepare_source", prepare)
    monkeypatch.setattr(trial, "assess", assess)
    monkeypatch.setattr(trial, "record", receipts.append)
    monkeypatch.setattr(trial, "acquire", lambda *_: {"exploration": "owned"})
    monkeypatch.setattr(trial, "initial_source", lambda *_: [])
    monkeypatch.setattr(trial, "finish", lambda *_: None)
    monkeypatch.setattr(trial, "save", lambda: None)
    monkeypatch.setattr(trial, "stop", stops.append)
    trial.run()
    assert prepared == ["large", "small"] and assessed == ["small"]
    assert receipts[0]["status"] == "budget-deferred"
    assert trial.state["preparation_deferrals"][0]["attempt"] == "first-owned"
    assert stops == ["scoped-goals-met"]
