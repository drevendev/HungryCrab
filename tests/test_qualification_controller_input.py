"""Large retained evidence must not overfill the deterministic controller plan."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from qualification_cycle import Trial
from test_discovery_inputs import repository
from test_release_069_discovery import plan

from hungry_crab.analysis_context import installed_source_identity


def test_controller_starts_with_large_evidence_retained_outside_its_plan(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    maw = repository(tmp_path / "receiving", {"app.py": "def consume(stream):\n    return 1\n"})
    definition = plan(maw)
    need = definition["needs"][0]
    need["novelty"] = {"original_evidence": "retained " * 40000}
    trial = Trial(
        "owned-candidate-1",
        {
            "host": {
                "model": "gpt-6.1-sol",
                "effort": "medium",
                "minimum_assessment_reserve": 6500,
            },
            "limits": {"model_tokens": 30000, "seconds": 2700},
            "candidate": {"source_identity": installed_source_identity()["source_sha256"]},
            "maws": {
                "owned": {
                    "context": definition["receiving_context"],
                    "needs": [need],
                    "plan": definition,
                }
            },
            "pool": [
                {"id": "available", "batch": "owned"},
                {
                    "id": "held",
                    "batch": "owned",
                    "repository": "owned/held",
                    "sha": "a" * 40,
                    "acquisition_status": "held",
                    "acquisition_error": "Owned fixture export refusal; no source view exists",
                },
            ],
            "trial_root": str(tmp_path / "trials"),
            "arm_python": {"candidate": sys.executable},
            "tool_cwd": {"candidate": str(root)},
            "native_cwd": str(root),
        },
    )
    actual = json.loads((trial.root / "session-plan.json").read_text(encoding="utf-8"))
    assert (trial.session / "session.json").exists()
    assert "novelty" not in actual["needs"][0]
    assert actual["needs"][0]["maw_evidence"] == need["maw_evidence"]
    assert trial.needs[0]["novelty"] == need["novelty"]
    assert set(trial.pool) == {"available"}
    assert trial.state["nomination_pool_size"] == 2
    assert trial.state["unavailable_nominees"][0]["id"] == "held"
