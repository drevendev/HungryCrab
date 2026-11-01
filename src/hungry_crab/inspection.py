"""Cumulative inspected witnesses in the existing exploration event chain.

Only this reader creates inspection events. Model citations select typed witness
identities; they cannot silently change a donor path into receiving evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .analysis_context import installed_source_identity
from .artifact_read import read_object
from .errors import CrabError
from .exploration import _append, _snapshot, _write_new, events, fingerprint
from .file_lock import state_lock
from .source_inspection import body_excluded, source_packet


def _current(directory: Path) -> dict[str, Any]:
    from .discovery_session import _same_context, live_context

    snapshot = _snapshot(directory)
    if snapshot["identities"]["tool"] != installed_source_identity():
        raise CrabError("inspection tool changed; start a fresh exploration")
    if not _same_context(live_context(Path(snapshot["maw_root"])), snapshot["receiving_context"]):
        raise CrabError("inspection receiving context changed")
    return snapshot


def _witnesses(packet: dict[str, Any], side: str) -> list[dict[str, Any]]:
    rows = []
    for file in packet["files"]:
        for window in file["ranges"]:
            ref = {key: window[key] for key in ("sha", "path", "start", "end")}
            identity = {
                **ref,
                "side": side,
                "content_sha256": hashlib.sha256(window["content"].encode()).hexdigest(),
            }
            rows.append(
                {"id": ("D" if side == "donor" else "R") + fingerprint(identity)[:24], **identity}
            )
    if len(rows) > 128:
        raise CrabError("inspection exceeds the cumulative witness operation limit")
    return rows


def inspect(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Read exact source, persist its full packet, and append typed witnesses."""
    operation = plan.get("operation")
    side = plan.get("side")
    if not isinstance(operation, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", operation) is None:
        raise CrabError("inspection needs a bounded stable operation identity")
    if side not in {"donor", "receiving"}:
        raise CrabError("inspection side must be donor or receiving")
    if (
        set(plan)
        - {"operation", "side", "paths", "queries", "need", "byte_budget", "related", "ranges"}
        or len(json.dumps(plan).encode()) > 8192
    ):
        raise CrabError("inspection plan contains unknown fields or exceeds its byte limit")
    with state_lock(directory / "events.json"):
        snapshot = _current(directory)
        history = [x["record"] for x in events(directory) if x["record"]["kind"] == "inspection"]
        for row in history:
            if row["operation"] == operation:
                if row["plan"] != plan:
                    raise CrabError("inspection operation already has a different plan")
                packet = read_object(directory / row["artifact"])
                if fingerprint(packet) != row["packet_sha256"]:
                    raise CrabError("inspection packet changed")
                return {"inspection": row, "packet": packet, "idempotent": True}
        if len(history) >= 64:
            raise CrabError("exploration inspection operation budget exhausted")
        prey = snapshot["menu"]["prey"]
        repo = (
            (prey.get("objects") or prey.get("root")) if side == "donor" else snapshot["maw_root"]
        )
        sha = prey["sha"] if side == "donor" else snapshot["receiving_context"]["sha"]
        if not isinstance(repo, str):
            raise CrabError("inspection source objects are unavailable; acquire them explicitly")
        packet = source_packet(
            Path(repo),
            sha,
            plan.get("paths", []),
            plan.get("queries", []),
            need=plan.get("need", snapshot["plan"]["scope"]),
            source_url=prey.get("url") if side == "donor" else None,
            byte_budget=plan.get("byte_budget", 16384),
            related=plan.get("related", False),
            ranges=plan.get("ranges"),
        )
        witnesses = _witnesses(packet, side)
        if len(json.dumps(packet).encode()) > 256_000:
            raise CrabError("inspection packet exceeds its artifact byte limit")
        artifact = "inspection-" + fingerprint(plan) + ".json"
        path = directory / artifact
        if path.exists():
            if read_object(path) != packet:
                raise CrabError("interrupted inspection artifact differs; use a new operation")
        else:
            _write_new(path, packet)
        row = {
            "kind": "inspection",
            "operation": operation,
            "plan": plan,
            "side": side,
            "artifact": artifact,
            "packet_sha256": fingerprint(packet),
            "witnesses": witnesses,
            "semantic_review": False,
            "donor_executed": False,
        }
        _append(directory, row)
        return {"inspection": row, "packet": packet, "idempotent": False}


def witnesses(directory: Path) -> dict[str, Any]:
    """Load all prior reads, including implementation windows before continuation."""
    snapshot = _current(directory)
    result: dict[str, dict[str, Any]] = {}
    for event in events(directory):
        row = event["record"]
        if row["kind"] != "inspection":
            continue
        packet = read_object(directory / row["artifact"])
        if (
            fingerprint(packet) != row["packet_sha256"]
            or _witnesses(packet, row["side"]) != row["witnesses"]
        ):
            raise CrabError("cumulative inspection artifact/witness identity changed")
        contents = [window["content"] for file in packet["files"] for window in file["ranges"]]
        for ref, content in zip(row["witnesses"], contents, strict=True):
            result[ref["id"]] = {**ref, "content": content}
    return {
        "schema": "hungry-crab.inspection-witnesses/1",
        "receiving_context": snapshot["receiving_context"],
        "witnesses": list(result.values()),
        "semantic_review": False,
        "license_permission": "consult-the-original-packets-and-publication-guards",
    }


def resolve(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Expand selected actual witness ids, refusing role swaps and unknown reads."""
    available = {row["id"]: row for row in witnesses(directory)["witnesses"]}
    output: dict[str, Any] = {}
    for key, side in (("source_evidence", "donor"), ("maw_evidence", "receiving")):
        ids = plan.get(key, [])
        if (
            not isinstance(ids, list)
            or len(ids) > 20
            or any(not isinstance(x, str) for x in ids)
            or len(ids) != len(set(ids))
        ):
            raise CrabError("citations need at most twenty distinct witness ids per side")
        refs = []
        for identity in ids:
            if (
                not isinstance(identity, str)
                or identity not in available
                or available[identity]["side"] != side
            ):
                raise CrabError("citation is uninspected or belongs to the other evidence role")
            refs.append({k: available[identity][k] for k in ("sha", "path", "start", "end")})
        output[key] = refs
    return output


def require_inspected(directory: Path, payload: dict[str, Any]) -> None:
    """A supervised review cannot substitute a declaration for an actual read."""
    available = witnesses(directory)["witnesses"]
    for key, side in (("source_evidence", "donor"), ("maw_evidence", "receiving")):
        refs = payload.get(key)
        if not isinstance(refs, list) or not refs:
            raise CrabError("review requires actual cumulative source and receiving inspections")
        for ref in refs:
            if not isinstance(ref, dict) or not any(
                row["side"] == side
                and row["sha"] == ref.get("sha")
                and row["path"] == ref.get("path")
                and type(ref.get("start")) is int
                and type(ref.get("end")) is int
                and row["start"] <= ref["start"] <= ref["end"] <= row["end"]
                for row in available
            ):
                raise CrabError("review cites an uninspected range or incorrect evidence role")


def contract(directory: Path, *, terms: list[str] | None = None) -> dict[str, Any]:
    """Generate capability/path/citation enums from real frozen receiving/source facts."""
    snapshot = _current(directory)
    if terms is not None and (
        not isinstance(terms, list)
        or len(terms) > 8
        or any(not isinstance(t, str) or not 0 < len(t) <= 100 for t in terms)
    ):
        raise CrabError("contract needs at most eight bounded literal terms")
    data = witnesses(directory)
    needles = [t.casefold() for t in (terms or []) if isinstance(t, str) and 0 < len(t) <= 100][:8]
    paths = {}
    for side, label in (("prey", "donor"), ("maw", "receiving")):
        known = [p for p in snapshot["file_evidence"][side] if not body_excluded(p)]
        known.sort(key=lambda p: (-sum(t in p.casefold() for t in needles), p))
        paths[label] = {"paths": known[:128], "paths_not_returned": max(0, len(known) - 128)}
    ids = {
        side: [r["id"] for r in data["witnesses"] if r["side"] == side]
        for side in ("donor", "receiving")
    }

    def selection(side: str) -> dict[str, Any]:
        return (
            {"type": "array", "maxItems": 20, "items": {"type": "string", "enum": ids[side]}}
            if ids[side]
            else {"type": "array", "maxItems": 0, "items": {"type": "string"}}
        )

    return {
        "schema": "hungry-crab.inspection-contract/1",
        "receiving_context": data["receiving_context"],
        "capabilities": [
            "read-donor",
            "read-receiving",
            "find-donor",
            "find-receiving",
            "read-pending",
            "search-backlog",
        ],
        "paths": paths,
        "witnesses": [
            {k: v for k, v in row.items() if k != "content"} for row in data["witnesses"]
        ],
        "citation_schema": {
            "type": "object",
            "properties": {
                "source_evidence": selection("donor"),
                "maw_evidence": selection("receiving"),
            },
            "required": ["source_evidence", "maw_evidence"],
            "additionalProperties": False,
        },
        "unknown_path": "Use bounded source find/index first; descriptive labels are not paths.",
        "semantic_review": False,
    }
