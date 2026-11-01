"""Bounded literal search of pinned source; not semantic evidence or execution."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .attribution import GitSourceReader
from .compare.candidates import _evidence_path
from .errors import CrabError
from .source_inspection import _commit_pin, _source_paths, body_excluded
from .source_lines import physical_lines

_CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".rs",
        ".go",
        ".rb",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".hs",
        ".sh",
    }
)


def find_source(
    repo: Path,
    sha: str,
    terms: list[str],
    *,
    prefix: str = "",
    max_files: int = 64,
    max_bytes: int = 512 * 1024,
    limit: int = 12,
) -> dict[str, Any]:
    """Return ranked actual locations, retaining coverage and truncation facts."""
    if (
        not isinstance(sha, str)
        or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", sha) is None
        or not isinstance(terms, list)
        or not 1 <= len(terms) <= 8
        or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 100 for t in terms)
        or type(max_files) is not int
        or not 1 <= max_files <= 128
        or type(max_bytes) is not int
        or not 1024 <= max_bytes <= 2 * 1024 * 1024
        or type(limit) is not int
        or not 1 <= limit <= 64
        or not isinstance(prefix, str)
        or (prefix and not _evidence_path(prefix))
    ):
        raise CrabError("literal source search requires bounded terms, prefix and limits")
    reader = GitSourceReader(repo, byte_limit=65_536, deadline=time.monotonic() + 30)
    if not _commit_pin(reader, sha):
        raise CrabError("literal source search requires an available immutable commit pin")
    paths, omissions, available = _source_paths(reader, sha, prefix)
    needles = list(dict.fromkeys(t.casefold().strip() for t in terms))
    eligible = [p for p, _ in paths if not body_excluded(p)]
    # A source search's finite read allowance should reach implementation/test
    # files before ancillary metadata. Keep every other path eligible and report
    # uninspected counts; this ordering is not a semantic or licence decision.
    eligible.sort(
        key=lambda p: (
            Path(p).suffix.casefold() not in _CODE_SUFFIXES,
            -sum(t in p.casefold() for t in needles),
            p,
        )
    )
    matches: list[dict[str, Any]] = []
    inspected = used = matching_lines = 0
    for path in eligible:
        if inspected >= max_files or used >= max_bytes or reader.expired():
            break
        inspected += 1
        size_text = reader._bounded_read(
            "--no-replace-objects", "cat-file", "-s", f"{sha}:{path}", stdout_limit=128
        )
        if size_text is None or not size_text.strip().isdigit():
            omissions.append({"path": path, "reason": "file-size-unavailable"})
            continue
        size = int(size_text.strip())
        if size > max_bytes - used:
            omissions.append({"path": path, "reason": "total-read-byte-limit"})
            continue
        reader.byte_limit = min(65_536, max_bytes - used)
        text = reader.read(sha, path)
        if text is None:
            omissions.append({"path": path, "reason": "file-unavailable-or-byte-limit"})
            continue
        used += size
        lines = physical_lines(text)
        for number, line in enumerate(lines, 1):
            hit = [t for t in needles if t in line.casefold()]
            if not hit:
                continue
            matching_lines += 1
            # Retain the strongest locations across all inspected files, rather
            # than letting a generic early match consume every returned window.
            matches.append(
                {
                    "path": path,
                    "sha": sha,
                    "line": number,
                    "line_count": len(lines),
                    "terms": hit,
                    "score": sum(len(t) for t in hit),
                }
            )
            if len(matches) >= 256:
                matches.sort(key=lambda r: (-r["score"], r["path"], r["line"]))
                del matches[128:]
    matches.sort(key=lambda r: (-r["score"], r["path"], r["line"]))
    selected: list[dict[str, Any]] = []
    # Prefer distinct files before additional locations in a single file.
    for distinct in (True, False):
        for row in matches:
            if row in selected or (distinct and any(x["path"] == row["path"] for x in selected)):
                continue
            selected.append(row)
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break
    return {
        "schema": "hungry-crab.source-find/1",
        "sha": sha,
        "terms": terms,
        "matches": selected,
        "matching_lines_observed": matching_lines,
        "matching_lines_not_returned": matching_lines - len(selected),
        "eligible_files": len(eligible),
        "files_inspected": inspected,
        "bytes_read": used,
        "files_not_inspected": len(eligible) - inspected,
        "tree_available": available,
        "omissions": omissions[:80],
        "omissions_not_returned": max(0, len(omissions) - 80),
        "read_limits": {"files": max_files, "bytes": max_bytes, "seconds": 30},
        "coverage": "bounded-literal-search; strings/comments may match; not semantic coverage",
        "file_order": "source-code-first; literal-path-then-name; other-files-retained",
        "donor_executed": False,
        "license_permission": "not-established",
    }
