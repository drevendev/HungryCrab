"""Bounded selected/default/pending implementation facts, not semantic novelty votes."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .attribution import GitSourceReader
from .compare.candidates import _evidence_path
from .discovery_session import live_context
from .errors import CrabError
from .source_inspection import _commit_pin, _source_paths, body_excluded


def _scope_paths(
    reader: GitSourceReader, sha: str, prefix: str
) -> tuple[list[tuple[str, str]], list[dict[str, str]], bool]:
    directory = ""
    parts = PurePosixPath(prefix).parts
    for index, part in enumerate(parts):
        entries = reader.tree_entries(sha, directory)
        if entries is None:
            return [], [{"path": directory, "reason": "scope-tree-unavailable"}], False
        mode = entries.get(part)
        if mode is None:
            return [], [], True  # Established absence at this immutable head.
        path = directory + "/" + part if directory else part
        if mode == "040000":
            directory = path
        elif mode in {"100644", "100755"} and index == len(parts) - 1:
            return [(path, mode)], [], True
        else:
            return [], [{"path": path, "reason": "scope-not-regular"}], False
    return _source_paths(reader, sha, directory)


def implementation_scope(maw: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate actual code bytes while preserving every immutable role binding.

    The caller explicitly acquires the owner's refs before invocation. There is
    no implicit fetch, branch switch, provider call or assertion of global absence.
    """
    context = live_context(maw)
    prefixes = plan.get("prefixes")
    pending = plan.get("pending", [])
    if (
        not isinstance(prefixes, list)
        or not 1 <= len(prefixes) <= 8
        or any(not isinstance(p, str) or not _evidence_path(p) for p in prefixes)
    ):
        raise CrabError("novelty scope requires 1..8 explicit receiving path prefixes")
    if not isinstance(pending, list) or len(pending) > 50:
        raise CrabError("novelty scope requires at most fifty confirmed pending heads")
    targets = [
        {"role": "receiving-selected", "sha": context["sha"], "ref": None},
        {
            "role": "receiving-default",
            "sha": plan.get("default_sha"),
            "ref": plan.get("default_ref"),
        },
    ]
    for row in pending:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("ref"), str)
            or re.fullmatch(r"refs/crab/qualification/pr-\d+", row["ref"]) is None
        ):
            raise CrabError(
                "pending implementation needs an explicitly acquired canonical pull ref"
            )
        targets.append({"role": "receiving-pending", "sha": row.get("sha"), "ref": row["ref"]})
    reader = GitSourceReader(maw, byte_limit=65_536, deadline=time.monotonic() + 60)
    sources: dict[str, dict[str, Any]] = {}
    bindings = []
    omissions: list[dict[str, Any]] = []
    used = 0
    for target in targets:
        sha, ref = target["sha"], target["ref"]
        if (
            not isinstance(sha, str)
            or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", sha) is None
            or not _commit_pin(reader, sha)
        ):
            omissions.append({**target, "reason": "immutable-head-unavailable"})
            continue
        if ref is not None:
            resolved = reader._bounded_read(
                "--no-replace-objects", "rev-parse", "--verify", ref, stdout_limit=128
            )
            if resolved is None or resolved.strip() != sha:
                omissions.append({**target, "reason": "confirmed-ref-does-not-match-pin"})
                continue
        eligible: list[str] = []
        for prefix in prefixes:
            paths, missing, available = _scope_paths(reader, sha, prefix)
            if not available or missing:
                omissions.extend(
                    {**target, **row} for row in missing or [{"reason": "tree-unavailable"}]
                )
            eligible.extend(p for p, _ in paths if not body_excluded(p))
        eligible = list(dict.fromkeys(eligible))
        if len(eligible) > 128:
            omissions.append({**target, "reason": "scope-file-count-limit"})
        selected = []
        for path in eligible[:128]:
            text = reader.read(sha, path)
            if text is None:
                omissions.append(
                    {**target, "path": path, "reason": "file-unavailable-or-byte-limit"}
                )
                continue
            identity = hashlib.sha256(text.encode()).hexdigest()
            if identity not in sources:
                amount = len(text.encode())
                if used + amount > 512 * 1024:
                    omissions.append({**target, "path": path, "reason": "scope-byte-limit"})
                    continue
                used += amount
                sources[identity] = {"content": text, "line_count": len(text.splitlines())}
            selected.append({"path": path, "content_sha256": identity})
        bindings.append({**target, "files": selected, "scope_files_observed": len(eligible)})
    collection = plan.get("pending_collection", {})
    collection_complete = (
        isinstance(collection, dict)
        and collection.get("complete") is True
        and isinstance(collection.get("as_of"), str)
        and 0 < len(collection["as_of"]) <= 60
    )
    return {
        "schema": "hungry-crab.implementation-scope/1",
        "receiving_context": context,
        "prefixes": prefixes,
        "targets": bindings,
        "sources": sources,
        "omissions": omissions,
        "pending_collection": collection,
        "implementation_scope_complete": collection_complete and not omissions,
        "global_historical_absence_proven": False,
        "semantic_novelty": "requires-agent-comparison",
        "interpretation": (
            "Compare selected/default/confirmed pending implementations in this explicit scope. "
            "An open request is not implemented behavior. A new mechanism for an unimplemented "
            "request may add value; link known work. Missing outside-scope evidence is unknown, "
            "not proof of duplication or novelty."
        ),
        "source_roles": "All bindings describe the receiving project, never donor transfer grants.",
        "provider_requests": 0,
        "donor_executed": False,
        "bytes_retained": used,
    }
