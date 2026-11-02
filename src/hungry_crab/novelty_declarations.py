"""Bounded comparison of requested declarations, retaining external uncertainty."""

from __future__ import annotations

from typing import Any

from .novelty_diffs import difference_projection
from .source_lines import physical_lines


def declaration_comparisons(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe exact requested bytes; never infer unchanged runtime behavior."""
    selected = next((t for t in targets if t["role"] == "receiving-selected"), None)
    if selected is None or not selected.get("scope_complete"):
        return {"complete": False, "reason": "selected-scope-unavailable", "comparisons": []}
    rows: dict[tuple[str, str, str, str | None, str, str | None], dict[str, Any]] = {}
    omitted = 0
    for target in targets:
        if target["role"] == "receiving-selected":
            continue
        if not target.get("scope_complete"):
            omitted += 1
            continue
        other = {(d["path"], d["qualified_name"]): d for d in target["declarations"]}
        for declaration in selected["declarations"]:
            key = declaration["path"], declaration["qualified_name"]
            counterpart = other.get(key, {"status": "declaration-unavailable"})
            fingerprint = (
                *key,
                declaration["status"],
                declaration.get("content_sha256"),
                counterpart["status"],
                counterpart.get("content_sha256"),
            )
            if fingerprint not in rows:
                if len(rows) >= 64:
                    omitted += 1
                    continue
                rows[fingerprint] = {
                    "path": key[0],
                    "qualified_name": key[1],
                    "selected_status": declaration["status"],
                    "selected_content_sha256": declaration.get("content_sha256"),
                    "other_status": counterpart["status"],
                    "other_content_sha256": counterpart.get("content_sha256"),
                    "exact_declaration_bytes_equal": (
                        declaration["status"] == counterpart["status"] == "declaration-available"
                        and declaration["content_sha256"] == counterpart["content_sha256"]
                    ),
                    "bindings": [],
                }
            location = {
                "selected_file_sha256": declaration.get("source_content_sha256"),
                "other_file_sha256": counterpart.get("source_content_sha256"),
                "selected_range": {k: declaration.get(k) for k in ("start", "end")},
                "other_range": {k: counterpart.get(k) for k in ("start", "end")},
            }
            locations = rows[fingerprint]["bindings"]
            group = next((g for g in locations if g["location"] == location), None)
            if group is None:
                group = {"location": location, "revisions": []}
                locations.append(group)
            group["revisions"].append({k: target[k] for k in ("role", "sha", "ref")})
    return {
        "comparisons": list(rows.values()),
        "complete": not omitted,
        "omitted_comparisons": omitted,
        "interpretation": (
            "Exact requested declaration bytes only, including decorators. Equal bytes do not "
            "establish runtime equivalence: imports, globals, helpers and other code may differ. "
            "Absent/ambiguous/unavailable declarations are not equal implementations. All file "
            "bodies, declarations, changes outside these ranges and role bindings remain in the "
            "parent scope artifact; read differing or relevant external code before judging it."
        ),
        "semantic_novelty": "requires-agent-comparison",
    }


def declaration_change_view(scope: dict[str, Any]) -> dict[str, Any]:
    """Project actual changed declaration hunks; disclose external behavior."""
    comparisons = scope["declaration_comparisons"]
    declarations = {
        (d["path"], d["qualified_name"], d["content_sha256"]): (d, target)
        for target in reversed(scope["targets"])
        for d in target["declarations"]
        if d["status"] == "declaration-available"
    }
    changes: list[dict[str, Any]] = []
    used = 0
    for row in comparisons.get("comparisons", []):
        if row["exact_declaration_bytes_equal"] or not all(
            row[k] == "declaration-available" for k in ("selected_status", "other_status")
        ):
            continue
        targets, sources, origins = [], {}, []
        for side, role in (("selected", "receiving-selected"), ("other", "receiving-pending")):
            digest = row[side + "_content_sha256"]
            declaration, target = declarations[row["path"], row["qualified_name"], digest]
            source = scope["sources"][declaration["source_content_sha256"]]["content"]
            body = "".join(
                physical_lines(source, keepends=True)[declaration["start"] - 1 : declaration["end"]]
            )
            sources[digest] = {"content": body}
            targets.append(
                {
                    "role": role,
                    "sha": target["sha"],
                    "ref": target["ref"],
                    "scope_complete": True,
                    "files": [{"path": row["path"], "content_sha256": digest}],
                }
            )
            origins.append(
                {
                    "side": side,
                    "sha": target["sha"],
                    "start": declaration["start"],
                    "end": declaration["end"],
                }
            )
        diff = difference_projection(targets, sources, content_bytes=max(0, 8192 - used))
        used += sum(
            len(view["content"].encode())
            for comparison in diff["comparisons"]
            for hunk in comparison["hunks"]
            for view in hunk.values()
        )
        changes.append(
            {
                "path": row["path"],
                "qualified_name": row["qualified_name"],
                "origins": origins,
                "differences": diff,
            }
        )
    return {
        "declaration_comparisons": comparisons,
        "changed_declarations": changes,
        "coordinates": (
            "Hunk lines are declaration-relative; physical line = origin.start + line - 1"
        ),
        "presence_or_unknown": "Absent/ambiguous/unavailable declarations need parent source reads",
        "other_file_changes": {
            "projected": False,
            "reason": "explicit-declaration-view; external behavior remains unknown",
            "parent_field": "implementation_differences",
        },
        "raw_scope_required": True,
    }
