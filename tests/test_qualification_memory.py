"""Continuation memory preserves prior provenance without inventing a reread."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from qualification_memory import continuation_packet


def test_checkpoint_carries_only_own_exact_prior_judgment_and_new_bodies() -> None:
    donor = {
        "id": "D1",
        "side": "donor",
        "content": "old donor body",
        "sha": "a" * 40,
        "path": "example.py",
        "start": 1,
        "end": 10,
    }
    receiving = {"id": "R1", "side": "receiving", "content": "old receiving body"}
    prior: dict[str, Any] = {
        "need": {"id": "bounded-iteration"},
        "donor": {"sha": "a" * 40},
        "actual_windows": [donor, receiving],
        "novelty": {"actual_pending_changes": "retained code\n" * 2000},
        "original_receiving_cases": {"status": "design-proposal; runtime unrun"},
    }
    answer = {
        "source_evidence": ["D1"],
        "maw_evidence": ["R1"],
        "novelty_resolved": True,
        "novelty": "Only this scoped implementation was compared",
        "disposition": "request-more",
        "adaptation": "",
        "acceptance": "",
    }
    current = copy.deepcopy(prior)
    current["actual_windows"].append({"id": "D2", "side": "donor", "content": "new body"})
    original = copy.deepcopy(current)
    result = continuation_packet(current, prior, answer)
    assert current == original
    assert result["actual_windows"] == [{"id": "D2", "side": "donor", "content": "new body"}]
    assert all("content" not in w for w in result["previous_read_window_index"])
    assert result["own_prior_checkpoint"]["original_answer"] == answer
    assert result["novelty"]["own_original_judgment"] == answer["novelty"]
    assert result["original_receiving_cases"] == prior["original_receiving_cases"]
    reread = {
        **answer,
        "operations": [
            {"side": "donor", "path": "example.py", "start": 1, "end": 10},
        ],
    }
    replayed = continuation_packet(current, prior, reread)
    assert replayed["actual_windows"][0] == donor
    assert replayed["resent_prior_window_ids"] == ["D1"]
    answer["novelty_resolved"] = False
    assert continuation_packet(current, prior, answer)["novelty"] == prior["novelty"]
    for field in ("need", "donor", "novelty", "original_receiving_cases"):
        changed = copy.deepcopy(current)
        changed[field] = {"changed": True}
        with pytest.raises(RuntimeError, match="context changed"):
            continuation_packet(changed, prior, answer)
    changed = copy.deepcopy(current)
    changed["actual_windows"][0]["content"] = "different immutable body"
    with pytest.raises(RuntimeError, match="prior source witness"):
        continuation_packet(changed, prior, answer)
    answer["source_evidence"] = ["R1"]
    with pytest.raises(RuntimeError, match="wrong-role"):
        continuation_packet(current, prior, answer)
