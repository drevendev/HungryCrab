"""A repeated donor must preserve the first receiving need's guarded registration."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_discovery_inputs import repository
from test_release_069_discovery import plan

from hungry_crab.analysis_context import installed_source_identity
from hungry_crab.discovery_session import live_context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from qualification_cycle import Trial
from research_host import Commands


@pytest.mark.parametrize("candidate", [True, False])
def test_completed_goal_is_reported_even_when_no_next_model_call_fits(candidate: bool) -> None:
    trial = object.__new__(Trial)
    trial.state = {
        "phase": "research",
        "started": time.time(),
        "outcomes": [
            {"need": "done", "decision": {"disposition": "verified-gap"}, "adoption_held": False}
        ],
    }
    trial.needs = [{"id": "done"}]
    trial.meter = SimpleNamespace(remaining=0)
    trial.frozen = {"host": {"minimum_assessment_reserve": 6500}}
    trial.session = Path("owned-session") if candidate else None
    observed = []
    trial.stop = observed.append
    trial.next = lambda: {"finished": True, "stop_reason": "scoped-goals-met"}
    trial.run()
    assert observed == [
        "scoped-goals-met" if candidate else "supported-frontier-exhausted-or-goals-met"
    ]


def test_candidate_design_planning_uses_actual_cli_seeds_without_a_model(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    donor = repository(tmp_path / "donor", {"stream.py": "VALUE = 7\n"})
    trial = object.__new__(Trial)
    trial.arm = "candidate"
    trial.frozen = {"host": {"candidate_metadata_seeds": True}}
    trial.needs = [{"id": "intake", "queries": ["stream limit"], "verification_mode": "design"}]
    trial.pool = {
        "fixture": {
            "id": "fixture",
            "repository": "owned/example",
            "sha": live_context(donor)["sha"],
            "view": str(donor),
        }
    }
    trial.root = tmp_path / "trial"
    trial.root.mkdir()
    trial.state = {}
    trial.state_path = trial.root / "state.json"
    trial.commands = Commands(trial.root / "cli", Path(sys.executable), root)
    assert trial.planner()
    assert trial.state["phase"] == "cases"
    assert trial.state["plan"]["intake"]["cases_json"] == ""
    assert trial.state["plan"]["intake"]["leads"][0]["donor"] == "fixture"
    assert json.loads((trial.root / "seed-preparation.json").read_text())["model_calls"] == 0


def test_baseline_has_ordinary_literal_lookup_without_candidate_ast_facts(tmp_path: Path) -> None:
    donor = repository(tmp_path / "donor", {"api.py": "# heading\n" * 90 + "VALUE = 7\n"})
    trial = object.__new__(Trial)
    trial.arm = "baseline"
    trial.state = {"read_ranges": []}
    trial.state_path = tmp_path / "state.json"
    trial.pool = {
        "fixture": {"view": str(donor), "sha": live_context(donor)["sha"], "catalog": ["api.py"]}
    }
    rows = trial.lookup_source(
        {"donor": "fixture"},
        {"id": "lookup"},
        {"exploration": "child"},
        {"path": "api.py", "query": "VALUE ="},
        "ordinary-read",
    )
    assert any("VALUE = 7" in r["content"] for r in rows)
    assert all(r["side"] == "donor" and r["end"] - r["start"] < 60 for r in rows)
    assert trial.state["source_lookups"]["lookup"][0]["matching_lines"] == [91]
    assert "no AST facts" in trial.state["source_lookups"]["lookup"][0]["method"]


def test_initial_navigation_reads_implementation_beyond_a_long_docstring(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        '__all__ = ["choose", "alias"]\ndef choose():\n    """documentation\n'
        + "    text\n" * 120
        + '    """\n    result = 7\n    return result\n'
    )
    donor = repository(tmp_path / "donor", {"api.py": source})
    maw = repository(tmp_path / "maw", {"app.py": "VALUE = 1\n"})
    trial = object.__new__(Trial)
    trial.arm = "candidate"
    trial.commands = Commands(tmp_path / "cli", Path(sys.executable), root)
    trial.maw = maw
    trial.context = live_context(maw)
    trial.pool = {"fixture": {"view": str(donor), "sha": live_context(donor)["sha"]}}
    result = trial.prepare_source(
        {"donor": "fixture", "terms": ["choose", "alias"]},
        {"id": "algorithm", "maw_evidence": []},
    )
    assert all(r["end"] - r["start"] < 60 for r in result["donor"])
    assert any("result = 7" in w.get("content", "") for w in result["windows"])


def test_second_need_preserves_first_registered_menu(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    maw = repository(
        tmp_path / "maw", {"app.py": "def consume(stream):\n    return stream.read()\n"}
    )
    donor = repository(
        tmp_path / "donor", {"reader.py": "def consume(stream):\n    return stream.read(4)\n"}
    )
    definition = plan(maw)
    first = definition["needs"][0]
    first.update(target={"path": "app.py", "qualified_name": "consume"}, novelty={})
    second = {**first, "id": "second"}
    definition["needs"] = [first, second]
    frozen: dict[str, Any] = {
        "calibration_only": True,
        "trial_root": str(tmp_path / "trials"),
        "candidate": {"source_identity": installed_source_identity()["source_sha256"]},
        "arm_python": {"candidate": sys.executable},
        "tool_cwd": {"candidate": str(root)},
        "native_cwd": str(tmp_path),
        "host": {"model": "not-invoked", "effort": "medium"},
        "pool": [
            {
                "id": "source",
                "batch": "control",
                "repository": "donor",
                "view": str(donor),
                "arm_digests": {"candidate": "not-used-for-original-fixture"},
            }
        ],
        "maws": {
            "control": {
                "context": live_context(maw),
                "plan": definition,
                "needs": [first, second],
                "receiving_python": sys.executable,
            }
        },
    }
    trial = Trial("control-candidate-1", frozen)
    trial.next()
    trial.record({"requests": 0, "candidates": [{"repository": "donor"}]})
    trial.next()
    lead = {"donor": "source"}
    original = trial.acquire(lead, first)
    child = original["exploration"]
    for side, path in (("donor", "reader.py"), ("receiving", "app.py")):
        trial.inspect(child, side, [{"path": path, "start": 1, "end": 2}], side, frozen["pool"][0])
    witnesses = trial.commands.json(["discover", "witnesses", child])["witnesses"]
    refs = {
        "source_evidence": [r["id"] for r in witnesses if r["side"] == "donor"],
        "maw_evidence": [r["id"] for r in witnesses if r["side"] == "receiving"],
    }
    expanded = trial.commands.json(["discover", "citations", child], refs)
    fields = {
        "hypothesis": "fixture bounded input",
        "disposition": "verified-gap",
        "reviewed": True,
        "reason": "Known original fixture control",
        "adaptation": "Use bounded reads",
        "maintenance": "Standard library only",
        "novelty": "Known fixture scope",
        "acceptance": "Original bounded stream control",
        "author": "controlled test",
        "tool": "fixture host",
        "attempt": "first",
    }
    trial.commands.json(["explore", "record", child], {"kind": "decision", **fields, **expanded})
    trial.commands.json(
        ["explore", "register", child, "--source-repo", donor],
        {
            **fields,
            **expanded,
            "key": "code.fixture-bounded",
            "title": "Bound fixture input",
            "behavior": "Finite reads",
            "gap": "Known original unbounded reader",
        },
    )
    before = json.loads((Path(original["meal"]) / "menu.json").read_text())
    assert any(c["key"] == "code.fixture-bounded" for c in before["candidates"])
    trial.record({"evidence_level": "source-supported"})
    trial.next()
    trial.record(
        {
            k: fields[k]
            for k in ("hypothesis", "disposition", "reason", "adaptation", "maintenance", "novelty")
        }
    )
    trial.next()
    trial.record({"requests": 0, "candidates": [{"repository": "donor"}]})
    trial.next()
    following = trial.acquire(lead, second)
    assert Path(following["meal"]) != Path(original["meal"])
    assert json.loads((Path(original["meal"]) / "menu.json").read_text()) == before
    assert trial.meter.data["tokens"] == 0  # Controlled deterministic fixture, never a model trial.
