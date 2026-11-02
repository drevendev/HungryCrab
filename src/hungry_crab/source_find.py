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
        ".jsx",
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
        ".fs",
        ".fsx",
        ".hs",
        ".sh",
        ".php",
        ".kt",
        ".kts",
        ".swift",
        ".dart",
        ".ex",
        ".exs",
        ".cr",
        ".jl",
        ".ml",
        ".mli",
        ".scala",
        ".clj",
        ".cljs",
        ".cljc",
        ".zig",
        ".rkt",
        ".scm",
        ".ss",
        ".lisp",
        ".cl",
        ".lua",
        ".nix",
        ".r",
        ".pl",
        ".pm",
        ".p6",
        ".pm6",
        ".raku",
        ".rakumod",
        ".targets",
        ".props",
        ".csproj",
    }
)


def path_role(path: str) -> str:
    """Disclosed filename convention, never proof of runtime use or non-use."""
    parts = {part.casefold() for part in Path(path).parts}
    if parts & {".idea", ".vscode", ".vs", ".settings", ".phpstorm.meta.php"}:
        return "editor-metadata"
    return "source" if Path(path).suffix.casefold() in _CODE_SUFFIXES else "other"


def filename_terms(terms: list[str]) -> list[str]:
    """Expand identifier spellings for filename ordering only, never body matches."""
    result: dict[str, None] = {}
    for term in terms:
        result[term.casefold()] = None
        separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", term)
        separated = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", separated)
        for part in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", separated):
            result[part.casefold()] = None
    return list(result)


def find_source(
    repo: Path,
    sha: str,
    terms: list[str],
    *,
    prefix: str = "",
    max_files: int = 64,
    max_bytes: int = 512 * 1024,
    limit: int = 12,
    focus: str = "implementation",
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
        or focus not in {"implementation", "metadata", "any"}
    ):
        raise CrabError("literal source search requires bounded terms, prefix and limits")
    reader = GitSourceReader(repo, byte_limit=65_536, deadline=time.monotonic() + 30)
    if not _commit_pin(reader, sha):
        raise CrabError("literal source search requires an available immutable commit pin")
    needles = list(dict.fromkeys(t.casefold().strip() for t in terms))
    path_terms = filename_terms(terms)
    paths, omissions, available = _source_paths(
        reader, sha, prefix, priority_terms=path_terms if focus == "implementation" else None
    )
    eligible = [p for p, _ in paths if not body_excluded(p)]

    def priority(path: str) -> int:
        role = path_role(path)
        if focus == "metadata":
            return int(role != "editor-metadata")
        return (
            {"source": 0, "other": 1, "editor-metadata": 2}[role]
            if focus == "implementation"
            else 0
        )

    # A source search's finite read allowance should reach implementation/test
    # files before ancillary metadata. Keep every other path eligible and report
    # uninspected counts; this ordering is not a semantic or licence decision.
    eligible.sort(
        key=lambda p: (
            priority(p),
            Path(p).suffix.casefold() not in _CODE_SUFFIXES,
            -sum(len(t) for t in path_terms if t in p.casefold()),
            p,
        )
    )
    matches: list[dict[str, Any]] = []

    def rank(row: dict[str, Any]) -> tuple[int, int, int, str, int]:
        return (
            priority(row["path"]),
            -row["score"],
            -int(row["declaration_like"]),
            row["path"],
            row["line"],
        )

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
                    "path_role": path_role(path),
                    "sha": sha,
                    "line": number,
                    "line_count": len(lines),
                    "terms": hit,
                    "score": sum(len(t) for t in hit),
                    "declaration_like": bool(
                        re.match(
                            r"\s*(?:(?:pub|public|private|protected|static|async|export|final)\s+)*"
                            r"(?:def|class|fn|function|struct|enum|interface|type|trait)\s+",
                            line,
                        )
                    ),
                }
            )
            match = matches[-1]
            first = number if match["declaration_like"] else max(1, number - 8)
            last = min(len(lines), number + (119 if match["declaration_like"] else 45))
            match["context_ranges"] = [
                {"start": start, "end": min(last, start + 59)}
                for start in range(first, last + 1, 60)
            ]
            match["context_kind"] = (
                "bounded-declaration-text; end is not a parsed declaration boundary"
                if match["declaration_like"]
                else "bounded-literal-context"
            )
            if len(matches) >= 256:
                matches.sort(key=rank)
                del matches[128:]
    matches.sort(key=rank)
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
        "focus": focus,
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
        "file_order": (
            "focus role, implementation tree frontier, identifier filename association; "
            "body matches still require original literals; omissions disclosed"
        ),
        "match_order": (
            "focus path-role convention, literal-specificity, declaration-like text, path/line; "
            "declaration-like is a text heuristic, not syntax proof"
        ),
        "donor_executed": False,
        "license_permission": "not-established",
    }
