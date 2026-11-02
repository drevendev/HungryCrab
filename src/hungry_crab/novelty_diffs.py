"""Bounded unique-line change projections with explicit omissions."""

from __future__ import annotations

from collections import Counter
from itertools import pairwise
from typing import Any

from .source_lines import physical_lines


def difference_projection(
    targets: list[dict[str, Any]], sources: dict[str, dict[str, Any]], *, content_bytes: int = 16000
) -> dict[str, Any]:
    """Linear unique-line anchors expose actual changes, never equivalence claims."""
    selected_target = next((t for t in targets if t["role"] == "receiving-selected"), None)
    if selected_target is None or not selected_target.get("scope_complete"):
        return {
            "requested": True,
            "complete": False,
            "reason": "selected-implementation-unavailable-or-incomplete",
            "comparisons": [],
        }
    selected = {f["path"]: f["content_sha256"] for f in selected_target["files"]}
    groups: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
    byte_left, line_left = content_bytes, 320
    omitted_comparisons = 0
    for target in targets:
        if target["role"] == "receiving-selected":
            continue
        if not target.get("scope_complete"):
            omitted_comparisons += 1
            continue
        other = {f["path"]: f["content_sha256"] for f in target["files"]}
        for path in sorted(set(selected) | set(other)):
            before, after = selected.get(path), other.get(path)
            if before == after:
                continue
            key = path, before, after
            binding = {k: target[k] for k in ("role", "sha", "ref")}
            if key in groups:
                groups[key]["bindings"].append(binding)
                continue
            if len(groups) >= 16:
                omitted_comparisons += 1
                continue
            left = physical_lines(sources[before]["content"]) if before else []
            right = physical_lines(sources[after]["content"]) if after else []
            lc, rc = Counter(left), Counter(right)
            positions = {text: i for i, text in enumerate(right) if rc[text] == 1}
            anchors = [(-1, -1)]
            for i, text in enumerate(left):
                j = positions.get(text)
                if lc[text] == 1 and j is not None and j > anchors[-1][1]:
                    anchors.append((i, j))
            anchors.append((len(left), len(right)))
            row: dict[str, Any] = {
                "path": path,
                "selected_content_sha256": before,
                "other_content_sha256": after,
                "bindings": [binding],
                "hunks": [],
                "omitted_ranges": [],
                "omitted_hunks": 0,
                "complete": True,
            }
            groups[key] = row
            if before is None or after is None:
                # Whole-file additions/removals otherwise repeat a large selected
                # body before useful changed implementations can fit. Presence is
                # established, while unprojected behavior explicitly stays unknown.
                row.update(
                    change="selected-path-absent" if before is None else "other-path-absent",
                    body_projection=(
                        "Present body retained by hash in parent sources; read separately if needed"
                    ),
                    complete=False,
                    omitted_hunks=1,
                    omitted_ranges=[
                        {
                            "selected": {"start": 1, "end": len(left)},
                            "other": {"start": 1, "end": len(right)},
                        }
                    ],
                )
                continue
            for (a, c), (b, d) in pairwise(anchors):
                if left[a + 1 : b] == right[c + 1 : d]:
                    continue
                ranges = (
                    (left, max(0, a), min(len(left), b + 1)),
                    (right, max(0, c), min(len(right), d + 1)),
                )
                views: list[dict[str, Any]] = [
                    {"start": x + 1, "end": y, "content": "\n".join(lines[x:y])}
                    for lines, x, y in ranges
                ]
                size = sum(len(v["content"].encode()) for v in views)
                lines = sum(y - x for _, x, y in ranges)
                if size > byte_left or lines > line_left or len(row["hunks"]) >= 16:
                    row["complete"] = False
                    row["omitted_hunks"] += 1
                    if len(row["omitted_ranges"]) < 16:
                        row["omitted_ranges"].append(
                            {
                                "selected": {"start": a + 2, "end": b},
                                "other": {"start": c + 2, "end": d},
                            }
                        )
                    continue
                row["hunks"].append({"selected": views[0], "other": views[1]})
                byte_left -= size
                line_left -= lines
    return {
        "requested": True,
        "comparisons": list(groups.values()),
        "omitted_comparisons": omitted_comparisons,
        "complete": not omitted_comparisons and all(r["complete"] for r in groups.values()),
        "interpretation": (
            "Exact changed text between ordered unique-line anchors, with one context line. "
            "Not syntax or runtime equivalence. Empty ranges have end below start. Full source, "
            "line endings and role bindings remain in the parent artifact; "
            "omitted ranges are unknown."
        ),
        "limits": {
            "comparisons": 16,
            "content_bytes": content_bytes,
            "lines": 320,
            "hunks_each": 16,
        },
    }
