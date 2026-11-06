"""Transport aliases cannot alter bodies or turn receiving reads into donors."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from qualification_cycle import semantic_packet
from research_host import Meter


def test_receipt_aliases_preserve_exact_source_and_expand_both_roles() -> None:
    donor = "D" + "a" * 24
    receiving = "R" + "b" * 24
    windows: list[dict[str, Any]] = [
        {
            "id": donor,
            "side": "donor",
            "sha": "c" * 40,
            "path": "value.py",
            "start": 5,
            "end": 5,
            "content": donor,
            "content_sha256": "d" * 64,
        },
        {
            "id": receiving,
            "side": "receiving",
            "sha": "e" * 40,
            "path": "own.py",
            "start": 9,
            "end": 9,
            "content": "return original",
            "content_sha256": "f" * 64,
        },
    ]
    facts = {
        "actual_windows": windows,
        "source_preparation": {
            "budget": 4096,
            "bytes_selected": 25,
            "original_ranges": [{"start": 5}],
            "selected_ranges": [{"start": 5}],
            "omissions": [{"start": 6, "end": 10}],
            "interpretation": "omissions unknown",
        },
    }
    original = copy.deepcopy(facts)
    packet, aliases = semantic_packet(facts, windows)
    assert facts == original
    assert aliases == {"D1": donor, "R1": receiving}
    for raw, encoded in zip(windows, packet["actual_windows"], strict=True):
        assert aliases[encoded["id"]] == raw["id"]
        for key in ("side", "sha", "path", "start", "end", "content"):
            assert encoded[key] == raw[key]
    assert packet["source_preparation"]["omissions"] == facts["source_preparation"]["omissions"]


def test_continuation_citations_and_resent_ids_use_the_same_receipt_aliases() -> None:
    donor = {
        "id": "D-original",
        "side": "donor",
        "path": "a.py",
        "sha": "a" * 40,
        "start": 1,
        "end": 1,
        "content": "return 1",
        "content_sha256": "b" * 64,
    }
    receiving = {**donor, "id": "R-original", "side": "receiving", "path": "own.py"}
    facts = {
        "actual_windows": [receiving],
        "previous_read_window_index": [donor],
        "resent_prior_window_ids": [receiving["id"]],
        "own_prior_checkpoint": {
            "original_answer": {"source_evidence": [donor["id"]], "maw_evidence": [receiving["id"]]}
        },
    }
    packet, aliases = semantic_packet(facts, [donor, receiving])
    answer = packet["own_prior_checkpoint"]["original_answer"]
    assert [aliases[key] for key in answer["source_evidence"]] == [donor["id"]]
    assert [aliases[key] for key in answer["maw_evidence"]] == [receiving["id"]]
    assert packet["previous_read_window_index"][0]["id"] == "D1"
    assert packet["resent_prior_window_ids"] == ["R1"]


def test_admission_estimate_never_settles_usage_or_overrides_actual_caps(tmp_path: Path) -> None:
    meter = Meter(tmp_path / "meter", cwd=tmp_path, model="owned-model", effort="medium")
    original = copy.deepcopy(meter.data)
    ascii_request = meter.admission("a" * 1000, {"type": "object"})
    unicode_request = meter.admission("λ" * 1000, {"type": "object"})
    assert unicode_request["estimate"] > ascii_request["estimate"]
    assert meter.data == original and meter.remaining == 30000
    meter.data["tokens"] = 29999
    assert not meter.admission("", {"type": "object"})["admissible"]
    meter.data.update(tokens=0, usage_complete=False)
    assert not meter.admission("", {"type": "object"})["admissible"]
