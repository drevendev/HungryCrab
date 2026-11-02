"""Same-investigation checkpoints, never shared judgments or fresh evidence."""

from __future__ import annotations

import copy
from typing import Any

from research_host import identity


def requested_window_ids(windows: list[dict[str, Any]], answer: dict[str, Any]) -> set[str]:
    """Also resupply prior bodies when the investigator explicitly requests them."""
    result = set()
    for operation in answer.get("operations", []):
        side = "donor" if operation["side"] == "donor-search" else operation["side"]
        for window in windows:
            if window.get("side") != side or window.get("path") != operation["path"]:
                continue
            if operation["side"] == "donor-search" or (
                window["start"] <= operation["end"] and window["end"] >= operation["start"]
            ):
                result.add(window["id"])
    return result


def continuation_packet(
    facts: dict[str, Any], prior_facts: dict[str, Any], prior_answer: dict[str, Any]
) -> dict[str, Any]:
    """Send new bodies with exact prior observations and immutable read identities.

    Full facts remain the retained review artifact. A checkpoint is the same
    investigator's original answer, not a supervisor-written semantic summary.
    """
    for field in ("need", "donor", "novelty", "original_receiving_cases"):
        if facts[field] != prior_facts[field]:
            raise RuntimeError("continuation context changed; cannot reuse prior judgment")
    old = {w["id"]: w for w in prior_facts["actual_windows"]}
    current = {w["id"]: w for w in facts["actual_windows"]}
    if any(current.get(key) != window for key, window in old.items()):
        raise RuntimeError("continuation lost or changed a prior source witness")
    for field, side in (("source_evidence", "donor"), ("maw_evidence", "receiving")):
        if any(key not in old or old[key]["side"] != side for key in prior_answer[field]):
            raise RuntimeError("checkpoint cites an unread or wrong-role prior witness")
    result = copy.deepcopy(facts)
    requested = requested_window_ids(list(current.values()), prior_answer)
    result["actual_windows"] = [
        w for key, w in current.items() if key not in old or key in requested
    ]
    result["resent_prior_window_ids"] = sorted(requested & old.keys())
    cited = set(prior_answer["source_evidence"] + prior_answer["maw_evidence"])
    result["previous_read_window_index"] = [
        {key: value for key, value in w.items() if key != "content"}
        for key, w in old.items()
        if key in cited
    ]
    result["prior_uncited_windows_retained"] = len(old.keys() - cited)
    result["own_prior_checkpoint"] = {
        "original_answer": prior_answer,
        "original_answer_sha256": identity(prior_answer),
        "full_prior_facts_sha256": identity(prior_facts),
        "scope": "Same investigator/trial only; no other arm, repeat or reviewer judgment",
        "limits": (
            "Prior bodies were actually supplied at the earlier stage and remain retained. "
            "They are not resent. This is not new evidence or stronger verification. Request "
            "original ranges again if the checkpoint is insufficient for a changed claim."
        ),
    }
    if prior_answer.get("novelty_resolved") is True:
        result["novelty"] = {
            "prior_comparison_sha256": identity(prior_facts["novelty"]),
            "own_original_judgment": prior_answer["novelty"],
            "resolved_by_same_investigator": True,
            "limitation": (
                "Full comparison was actually supplied earlier and remains in full facts. "
                "This carries the investigator's scoped judgment, not a new absence proof. "
                "New conflicting evidence requires reassessment. Additional reads remain below."
            ),
        }
    return result
