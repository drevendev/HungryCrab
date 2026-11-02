"""Lossless references to repeated original proposal text in review evidence."""

from __future__ import annotations

import copy
import json
from typing import Any


def expand_review_text(encoded: dict[str, Any]) -> Any:
    """Restore every original value, including literal source and acceptance text."""
    proposals = encoded["proposal_text"]

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) == {"$proposal_text"}:
                return proposals[value["$proposal_text"]]
            if set(value) == {"$concat_text"}:
                return "".join(expand(part) for part in value["$concat_text"])
            return {key: expand(child) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child) for child in value]
        return value

    return expand(encoded["evidence"])


def factor_review_text(payload: dict[str, Any]) -> dict[str, Any]:
    """Factor only exact original proposal strings, without semantic summarisation."""
    table: dict[str, str] = {}
    for run in payload["runs"]:
        for observation in run["observations"]:
            for field in ("adaptation", "acceptance", "maintenance"):
                text = observation["native_decision"].get(field)
                if isinstance(text, str) and len(text) >= 128 and text not in table.values():
                    table["T" + str(len(table) + 1)] = text
    ordered = sorted(table, key=lambda key: (-len(table[key]), key))

    def encode(value: Any) -> Any:
        if isinstance(value, str):
            parts: list[Any] = [value]
            for key in ordered:
                next_parts: list[Any] = []
                for part in parts:
                    if not isinstance(part, str) or table[key] not in part:
                        next_parts.append(part)
                        continue
                    for index, chunk in enumerate(part.split(table[key])):
                        if index:
                            next_parts.append({"$proposal_text": key})
                        if chunk:
                            next_parts.append(chunk)
                parts = next_parts
            if parts == [value]:
                return value
            return parts[0] if len(parts) == 1 else {"$concat_text": parts}
        if isinstance(value, dict):
            if {"$proposal_text", "$concat_text"} & value.keys():
                raise ValueError("reserved review transport reference in original evidence")
            return {key: encode(child) for key, child in value.items()}
        if isinstance(value, list):
            return [encode(child) for child in value]
        return value

    result = {
        "encoding": (
            "Resolve $proposal_text from proposal_text; $concat_text joins its exact parts. "
            "All original proposals, literal source, roles, grants and acceptance text survive."
        ),
        "evidence": encode(copy.deepcopy(payload)),
        "proposal_text": table,
    }
    if expand_review_text(result) != payload:
        raise RuntimeError("review transport failed exact original-evidence round trip")
    # This is an exact transport, not permission to inflate a small original.
    if len(json.dumps(result)) >= len(json.dumps(payload)):
        return {"encoding": "Unchanged original evidence", "evidence": payload, "proposal_text": {}}
    return result
