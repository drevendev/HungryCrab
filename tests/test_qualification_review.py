"""Review preparation preserves proof and does not reveal the producing arm."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from qualification_review import compact_evidence, packet, project_case, share_exact_evidence
from research_host import write
from review_transport import expand_review_text, factor_review_text


def test_review_transport_restores_original_proposal_source_and_acceptance() -> None:
    adaptation = "Add an opt-in receiving mechanism. " * 12
    acceptance = "Preserve exact existing outputs and test original cases. " * 12
    payload = {
        "runs": [
            {
                "observations": [
                    {
                        "native_decision": {"adaptation": adaptation, "acceptance": acceptance},
                        "verification": {"assessment": adaptation, "criteria": acceptance + "\n"},
                        "actual_acceptance_artifact": adaptation + "\nAcceptance:\n" + acceptance,
                    }
                ]
            }
        ],
        "content": "# untrusted literal\n" + acceptance,
        "roles": {"held": True, "mode": "HUMAN", "original_receiving_owned": True},
    }
    original = json.dumps(payload)
    encoded = factor_review_text(payload)
    assert expand_review_text(encoded) == payload
    assert json.dumps(payload) == original
    assert len(json.dumps(encoded)) < len(original)
    payload["reserved"] = {"$concat_text": ["not a reference"]}
    with pytest.raises(ValueError, match="reserved review transport"):
        factor_review_text(payload)


@pytest.mark.parametrize("copies", [2, 3])
def test_shared_evidence_roundtrips_exact_values_and_refuses_ambiguous_input(copies: int) -> None:
    repeated = {"code": "def f():\n    return 1\n" * 60, "sha": "a" * 64}
    original = {"runs": [repeated] * copies, "plain": None}
    encoded = share_exact_evidence(original)

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) == {"$shared_evidence"}:
                return expand(encoded["shared_evidence"][value["$shared_evidence"]])
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    assert expand(encoded["evidence"]) == original
    assert len(json.dumps(encoded)) < len(json.dumps(original))
    with pytest.raises(ValueError, match="reserved"):
        share_exact_evidence({"nested": {"$shared_evidence": "E1"}})


def test_semantic_context_compression_preserves_repeated_pending_source_and_roles() -> None:
    declaration = {"path": "app.py", "source": "def consume(stream):\n    yield stream.read()\n"}
    payload = {
        "pending": [{"sha": str(i), "declaration": declaration} for i in range(14)],
        "witness": {"id": "D123", "side": "donor", "content": "not instructions"},
    }
    packed = json.loads(compact_evidence(payload))
    assert packed["encoding"].startswith("Exact JSON")
    assert packed["evidence"]["witness"] == payload["witness"]
    assert len(compact_evidence(payload)) < len(json.dumps(payload, separators=(",", ":")))
    # The function verifies every expanded value before returning a packet.
    assert json.loads(compact_evidence({"unique": 7})) == {"unique": 7}


@pytest.mark.parametrize("typed", [False, True])
def test_case_projection_retains_original_data_actual_calls_and_hashes(
    tmp_path: Path, typed: bool
) -> None:
    execution = tmp_path / "receiving-original"
    execution.mkdir()
    receipt = execution / "receipt.json"
    calls = [{"path": "app.py", "qualified_name": "consume", "count": 2}]
    write(receipt, {"context": {"sha": "a" * 40}, "observed_receiving_calls": calls})
    (execution / "stdout.txt").write_bytes(b'{"cases":[]}\n')
    data = {"cases": [{"label": "original"}], "target": {"path": "app.py"}, "author": "host"}
    if typed:
        data = {"python_probe": "from app import consume\n", "target": data["target"]}
    write(tmp_path / "original.original-cases.json", data)
    checks = [{"pointer": "/value/count", "observed": 2, "passed": True}]
    original = {
        "label": "actual",
        "role": "positive-control",
        "passed": True,
        "checks": checks,
        "actual": {"value": {"count": 2, "other": "large return object"}},
    }
    result = project_case({"status": "executed", "receipt": str(receipt), "cases": [original]})
    assert result["receipt_content"]["observed_receiving_calls"] == calls
    assert result["receipt_sha256"] == hashlib.sha256(receipt.read_bytes()).hexdigest()
    assert result["stdout_sha256"] == hashlib.sha256(b'{"cases":[]}\n').hexdigest()
    assert result["receiving_sha"] == "a" * 40
    assert result["original_case_data"] == {k: v for k, v in data.items() if k != "author"}
    assert result["cases"][0]["checks"] == checks
    assert result["cases"][0]["actual_result_sha256"]
    assert "large return object" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_packet_masks_metadata_and_requires_every_trial_complete(tmp_path: Path) -> None:
    names = [f"crab-{arm}-{n}" for arm in ("baseline", "candidate") for n in (1, 2)]
    state: dict[str, Any] = {
        "phase": "complete",
        "outcomes": [],
        "case_results": {},
        "model_budget_valid": True,
        "usage_complete": True,
        "stop_reason": "supported-frontier-exhausted",
        "actual_model_tokens": 1,
        "seconds": 1,
    }
    for name in names:
        (tmp_path / name).mkdir()
        write(tmp_path / name / "host-state.json", state)
    need: dict[str, Any] = {
        k: k for k in ("id", "flow", "expected", "observed", "constraints", "target")
    }
    need["novelty"] = {"bindings": [], "backlog_summary": [], "backlog_coverage": []}
    plan = {
        "trial_root": str(tmp_path),
        "schedule": names,
        "sealed_at": "original",
        "maws": {"crab": {"context": {"sha": "a" * 40}, "needs": [need]}},
        "tool_cwd": {"candidate": str(tmp_path)},
    }
    payload, mapping = packet(plan, "crab", 1)
    assert set(mapping.values()) == set(names)
    assert payload["shared_actual_implementation_scope"] == {"bindings": []}
    serialized = json.dumps(payload)
    assert all(arm not in serialized for arm in ("baseline", "candidate"))
    source = 'VERSION = "0.6.8"\nclass Candidate: pass\n'
    other = {
        **need,
        "id": "other",
        "novelty": {
            "bindings": [{"sha": "b" * 40}],
            "source": {"content": source},
            "backlog_summary": [],
            "backlog_coverage": [],
        },
    }
    plan["maws"]["crab"]["needs"] = [need, other]
    payload, _ = packet(plan, "crab", 1)
    assert payload["shared_actual_implementation_scope"] is None
    assert len(payload["actual_implementation_scopes"]) == 2
    first, second = payload["needs_and_actual_novelty_context"]
    assert payload["actual_implementation_scopes"][first["implementation_scope_id"]] == {
        "bindings": []
    }
    assert (
        payload["actual_implementation_scopes"][second["implementation_scope_id"]]["source"][
            "content"
        ]
        == source
    )
    state["phase"] = "research"
    write(tmp_path / names[-1] / "host-state.json", state)
    with pytest.raises(RuntimeError, match="all measured trials finish"):
        packet(plan, "crab", 1)
