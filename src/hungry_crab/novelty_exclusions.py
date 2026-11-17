"""Immutable exclusion facts in the existing exploration snapshot/event chain."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .analysis_context import fingerprint, installed_source_identity
from .cache import Slug
from .errors import CrabError
from .nutrients import Candidate
from .source_lines import physical_lines

BASIS_SCHEMA = "hungry-crab.novelty-exclusion-basis/1"
EXCLUSIONS = frozenset({"duplicate", "already-present"})


def capture_scope(
    maw: Path, plan: object, expected: dict[str, Any] | None = None
) -> dict[str, Any]:
    from .novelty import implementation_scope

    if not isinstance(plan, dict) or len(json.dumps(plan).encode()) > 16_000:
        raise CrabError("novelty scope needs a bounded explicit implementation plan")
    scope = implementation_scope(maw, plan)
    if expected is not None and any(
        scope["receiving_context"].get(key) != expected.get(key)
        for key in ("root", "sha", "selected_ref", "worktree", "policy", "ledger")
    ):
        raise CrabError("novelty scope differs from the frozen receiving context")
    return scope


def exclusion_basis(scope: object, requested: object) -> dict[str, Any]:
    """Bind a semantic analyst's claim to actual retained implementation reads."""
    if not isinstance(scope, dict) or not isinstance(requested, dict):
        raise CrabError("reviewed exclusion requires a frozen novelty scope and typed basis")
    fields = {"schema", "scope_sha256", "role", "ref", "sha", "evidence"}
    if (
        set(requested) - fields - {"observed_at"}
        or not fields <= set(requested)
        or requested["schema"] != BASIS_SCHEMA
        or requested["scope_sha256"] != fingerprint(scope)
        or not isinstance(scope.get("observed_at"), str)
        or requested.get("observed_at", scope["observed_at"]) != scope["observed_at"]
    ):
        raise CrabError("novelty exclusion basis has a different scope or observation identity")
    targets = [
        target
        for target in scope["targets"]
        if all(target.get(key) == requested[key] for key in ("role", "ref", "sha"))
    ]
    if len(targets) != 1 or not isinstance(requested["ref"], str):
        raise CrabError("novelty exclusion role/ref/SHA is unavailable in the frozen scope")
    target = targets[0]
    refs = requested["evidence"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
        raise CrabError("novelty exclusion needs 1..20 inspected implementation ranges")
    files = {row["path"]: row["content_sha256"] for row in target["files"]}
    evidence = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) - {
            "path",
            "start",
            "end",
            "content_sha256",
            "file_sha256",
        }:
            raise CrabError("invalid novelty implementation range")
        path, start, end = ref.get("path"), ref.get("start"), ref.get("end")
        digest = files.get(path) if isinstance(path, str) else None
        source = scope["sources"].get(digest) if digest is not None else None
        if not isinstance(source, dict):
            raise CrabError("novelty range was not acquired in the frozen implementation scope")
        content = source["content"]
        lines = physical_lines(content, keepends=True)
        if (
            hashlib.sha256(content.encode()).hexdigest() != digest
            or type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end <= min(len(lines), start + 1000)
            or ref.get("file_sha256", digest) != digest
        ):
            raise CrabError("novelty implementation bytes or range differ from the frozen read")
        window = "".join(lines[start - 1 : end])
        actual = hashlib.sha256(window.encode()).hexdigest()
        if ref.get("content_sha256") != actual:
            raise CrabError("novelty inspected range hash differs from the frozen implementation")
        evidence.append({**ref, "file_sha256": digest})
    return {**requested, "evidence": evidence, "observed_at": scope["observed_at"]}


def exclusion_status(data: dict[str, Any], record: dict[str, Any]) -> str:
    if record.get("disposition") not in EXCLUSIONS:
        return "not-an-exclusion"
    if "novelty_scope" not in data:
        return "legacy-analyst-assertion"
    try:
        exclusion_basis(data.get("novelty_scope"), record.get("novelty_basis"))
    except CrabError:
        return "unresolved-implementation-basis"
    return "frozen-implementation-supported"


def reconcile(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Record later supersession without changing an original judgement or need."""
    from .artifact_read import read_object
    from .exploration import _append, _snapshot, _text, _write_new, events
    from .file_lock import state_lock

    if (
        set(plan)
        != {
            "operation",
            "original_event",
            "scope",
            "novelty_basis",
            "author",
            "tool",
            "attempt",
            "reason",
        }
        or len(json.dumps(plan).encode()) > 32_000
    ):
        raise CrabError("novelty reconciliation needs bounded original-event and scope inputs")
    for key in ("author", "tool", "attempt", "reason"):
        _text(plan, key)
    operation = plan["operation"]
    if not isinstance(operation, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", operation) is None:
        raise CrabError("novelty reconciliation requires a bounded stable operation")
    with state_lock(directory / "events.json"):
        data = _snapshot(directory)
        if data["identities"]["tool"] != installed_source_identity():
            raise CrabError("novelty tool changed; preserve the original exploration")
        history = events(directory)
        for event in history:
            row = event["record"]
            if row["kind"] == "novelty-reconciliation" and row["operation"] == operation:
                if row["plan"] != plan:
                    raise CrabError("novelty operation already has a different plan")
                artifact = read_object(directory / row["artifact"])
                if fingerprint(artifact) != row["artifact_sha256"]:
                    raise CrabError("novelty reconciliation artifact changed")
                return event
        original = next((e for e in history if e["sha256"] == plan["original_event"]), None)
        if original is None or original["record"]["kind"] != "decision":
            raise CrabError("novelty reconciliation requires an original decision event")
        artifact_name = "novelty-" + fingerprint(plan) + ".json"
        path = directory / artifact_name
        if path.exists():
            scope = read_object(path)
            if scope.get("reconciliation_plan") != plan:
                raise CrabError("interrupted novelty scope differs from the original plan")
            scope = scope["scope"]
        else:
            scope = capture_scope(Path(data["maw_root"]), plan["scope"])
        requested = plan["novelty_basis"]
        if not isinstance(requested, dict) or "scope_sha256" in requested:
            raise CrabError("later novelty scope identity is assigned by the recorder")
        basis = exclusion_basis(scope, {**requested, "scope_sha256": fingerprint(scope)})
        artifact = {"reconciliation_plan": plan, "scope": scope}
        if not path.exists():
            _write_new(path, artifact)
        row = original["record"]
        return _append(
            directory,
            {
                "kind": "novelty-reconciliation",
                "operation": operation,
                "plan": plan,
                "original_event": original["sha256"],
                "original_disposition": row["disposition"],
                "hypothesis": row["hypothesis"],
                "candidate_id": row.get("candidate_id"),
                "novelty_basis": basis,
                "artifact": artifact_name,
                "artifact_sha256": fingerprint(artifact),
                "status": "later-supersession",
                "baseline_presence_proven": False,
                "publication_held": True,
            },
        )


def reconciliations(directory: Path, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .artifact_read import read_object

    result = []
    for event in history:
        row = event["record"]
        if row["kind"] != "novelty-reconciliation":
            continue
        artifact = read_object(directory / row["artifact"])
        if fingerprint(artifact) != row["artifact_sha256"]:
            raise CrabError("novelty reconciliation artifact changed")
        if exclusion_basis(artifact["scope"], row["novelty_basis"]) != row["novelty_basis"]:
            raise CrabError("novelty reconciliation basis changed")
        result.append({"event": event["sha256"], **row})
    return result


def require_publication_novelty(
    meal: Path, cards: Sequence[Candidate], *, provider: object | None = None, slug: Slug | None = None
) -> None:
    """A historical proposal never clears a known later implementation hold."""
    from .attribution import GitSourceReader
    from .discovery_session import _same_context, live_context
    from .exploration import _snapshot, events
    from .file_lock import require_real_state_path

    for card in cards:
        identity = card.trace.get("exploration_snapshot")
        if not identity:
            continue
        if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
            raise CrabError("publication exploration identity is unavailable")
        directory = meal / "exploration" / identity
        require_real_state_path(directory / "snapshot.json")
        data = _snapshot(directory)
        history = events(directory)
        for row in reconciliations(directory, history):
            if row["candidate_id"] in {None, card.id}:
                raise CrabError("later implementation supersedes this proposal; publication held")
        scope = data.get("novelty_scope")
        if scope is None:
            raise CrabError("novelty implementation scope is unknown; publication held")
        if scope.get("implementation_scope_complete") is not True:
            raise CrabError("novelty implementation scope is unresolved; publication held")
        if not _same_context(live_context(Path(data["maw_root"])), data["receiving_context"]):
            raise CrabError("receiving context changed; reconcile novelty before publication")
        reader = GitSourceReader(Path(data["maw_root"]))
        for target in scope["targets"]:
            ref = target.get("ref")
            resolved = (
                reader._bounded_read(
                    "--no-replace-objects",
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    ref,
                    stdout_limit=128,
                )
                if isinstance(ref, str)
                else None
            )
            if resolved is None or resolved.strip() != target["sha"]:
                raise CrabError("implementation ref changed or is unavailable; publication held")
        probe = getattr(provider, "novelty_heads", None)
        if not callable(probe) or slug is None:
            raise CrabError("current provider implementation heads unavailable; publication held")
        current = probe(slug, scope["targets"])
        if not isinstance(current, dict) or any(
            current.get(target["ref"]) != target["sha"]
            for target in scope["targets"] if target["role"] != "receiving-selected"
        ):
            raise CrabError("provider implementation changed; reconcile novelty before publication")
