"""Bounded immutable source/test packets for supervised behavior discovery."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .attribution import GitSourceReader
from .compare.candidates import _evidence_path
from .errors import CrabError
from .licensing.path_grants import GrantResolver
from .miners.ai_config import INSTRUCTION_FILES

PACKET_SCHEMA = "hungry-crab.source-packet/1"


def _commit_pin(reader: GitSourceReader, sha: str) -> bool:
    resolved = reader._bounded_read(
        "--no-replace-objects", "rev-parse", "--verify", f"{sha}^{{commit}}", stdout_limit=128
    )
    return resolved is not None and resolved.strip() == sha


def body_excluded(path: str) -> bool:
    lowered = path.casefold()
    name = Path(path).name.casefold()
    return (
        name.startswith("readme")
        or name == "skill.md"
        or name in {Path(item).name.casefold() for item in INSTRUCTION_FILES}
        or lowered.startswith((".cursor/rules/", ".github/instructions/", ".github/prompts/"))
        or (
            Path(path).suffix.casefold() in {".md", ".mdc"}
            and lowered.startswith(
                (
                    "agents/",
                    "commands/",
                    "skills/",
                    ".claude/agents/",
                    ".claude/commands/",
                    ".codex/agents/",
                    ".agents/skills/",
                )
            )
        )
    )


def syntax_query(
    source: str, queries: list[dict[str, str]], *, timeout: float = 5
) -> dict[str, Any]:
    """Use a fresh isolated interpreter, never a donor cwd, module or import path."""
    if len(source.encode("utf-8")) > 65_536 or len(queries) > 8:
        return {"status": "budget-deferred", "matches": [], "reason": "input-limit"}
    payload = json.dumps({"source": source, "queries": queries}, ensure_ascii=False).encode()
    if len(payload) > 100_000:
        return {"status": "budget-deferred", "matches": [], "reason": "transport-limit"}
    worker = Path(__file__).with_name("syntax_worker.py")
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(worker)],
            input=payload,
            capture_output=True,
            timeout=min(5, max(0.001, timeout)),
            cwd=worker.parent,
            check=False,
        )
        if result.returncode or len(result.stdout) > 65_536:
            raise ValueError("worker-limit")
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
            raise ValueError("worker-protocol")
        return data
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {
            "status": "budget-deferred",
            "matches": [],
            "reason": "worker-unavailable-or-timeout",
        }


def source_packet(
    repo: Path,
    sha: str,
    paths: list[str],
    queries: list[dict[str, str]],
    *,
    need: str,
    source_url: str | None = None,
    byte_budget: int = 128 * 1024,
    related: bool = True,
    ranges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return all selected/omitted paths and actual ranges, not a semantic judgment."""
    if re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", sha) is None:
        raise CrabError("source packet requires an immutable commit SHA")
    if (
        not isinstance(need, str)
        or not need
        or len(need) > 200
        or not isinstance(paths, list)
        or len(paths) > 32
        or not isinstance(queries, list)
        or len(queries) > 8
    ):
        raise CrabError("source packet plan exceeds bounded need/path/query limits")
    if type(byte_budget) is not int or not 1024 <= byte_budget <= 512 * 1024:
        raise CrabError("source packet byte budget must be in 1024..524288")
    if any(not isinstance(path, str) or not _evidence_path(path) for path in paths):
        raise CrabError("source packet contains an unsafe path")
    explicit: dict[str, list[dict[str, Any]]] = {}
    if ranges is not None:
        if not isinstance(ranges, list) or len(ranges) > 32 or (ranges and queries):
            raise CrabError("choose at most 32 explicit ranges or structural queries")
        for row in ranges:
            if not isinstance(row, dict):
                raise CrabError("source ranges must be objects")
            path, start, end = row.get("path"), row.get("start"), row.get("end")
            if (
                path not in paths
                or type(start) is not int
                or type(end) is not int
                or not 1 <= start <= end <= start + 59
            ):
                raise CrabError("source ranges require a selected path and 1..60 actual lines")
            explicit.setdefault(path, []).append(
                {"start": start, "end": end, "relationship": "requested-source-range"}
            )
    deadline = time.monotonic() + 30
    reader = GitSourceReader(repo, byte_limit=65_536, deadline=deadline)
    resolver = GrantResolver(reader, sha, source_url)
    records: list[dict[str, Any]] = []
    omissions: list[dict[str, str]] = []
    associations: list[dict[str, str]] = []
    pin_verified = _commit_pin(reader, sha)
    if not pin_verified:
        paths = []
        omissions.append({"path": "", "reason": "unavailable-or-noncanonical-commit-pin"})
    elif related:
        paths, associations, listing_omissions = _related(reader, sha, paths)
        omissions.extend(listing_omissions)
    used = 0
    for path in dict.fromkeys(paths):
        if reader.expired():
            omissions.append({"path": path, "reason": "whole-packet-time-limit"})
            continue
        if body_excluded(path):
            omissions.append({"path": path, "reason": "instruction/readme-body-excluded"})
            continue
        parent, _, name = path.rpartition("/")
        if (reader.entries(sha, parent) or {}).get(name) not in {"100644", "100755"}:
            omissions.append({"path": path, "reason": "not-a-regular-pinned-file"})
            continue
        text = reader.read(sha, path)
        if text is None or len(text.encode("utf-8")) > 65_536:
            omissions.append({"path": path, "reason": "unavailable-or-file-byte-limit"})
            continue
        lines = text.splitlines()
        manual = explicit.get(path)
        result: dict[str, Any] = (
            {"status": "not-run-explicit-source-ranges", "matches": []}
            if manual is not None
            else (
                syntax_query(text, queries, timeout=deadline - time.monotonic())
                if path.endswith((".py", ".pyi"))
                else {"status": "unsupported-structural-language", "matches": []}
            )
        )
        unique: dict[tuple[int, int], dict[str, Any]] = {}
        for match in sorted(
            result["matches"],
            key=lambda match: (match["query_index"], match["start"], match["end"]),
        ):
            unique.setdefault((match["start"], match["end"]), match)
        matches = manual if manual is not None else list(unique.values())[:8]
        if manual is None and not matches:
            matches = [{"start": 1, "end": min(60, len(lines)), "relationship": "selected-context"}]
        # Related test/source paths are a heuristic. A static association never
        # claims runtime coverage or marks a finding reviewed.
        windows: list[dict[str, Any]] = []
        for match in matches:
            start, end = match["start"], match["end"]
            if manual is not None and end > len(lines):
                omissions.append({"path": path, "reason": "requested-range-outside-file"})
                continue
            content = "\n".join(lines[start - 1 : end])
            size = len(content.encode("utf-8"))
            if used + size > byte_budget:
                omissions.append({"path": path, "reason": "whole-packet-byte-limit"})
                break
            used += size
            windows.append(
                {
                    **match,
                    "sha": sha,
                    "path": path,
                    "content": content,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )
        records.append(
            {
                "path": path,
                "sha": sha,
                "decoded_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "encoding": "utf-8-with-replacement",
                "line_count": len(lines),
                "analysis": result["status"],
                "structural_result": result,
                "duplicate_structural_ranges": len(result["matches"]) - len(unique),
                "structural_ranges_deferred": max(0, len(unique) - 8),
                "ranges": windows,
                "path_grant": resolver.resolve(path).to_dict(),
                "test_association": "selected-test-path"
                if any(p in {"test", "tests", "spec"} for p in path.split("/"))
                else "unconfirmed",
            }
        )
    return {
        "schema": PACKET_SCHEMA,
        "untrusted_data": True,
        "need": need,
        "sha": sha,
        "commit_pin_verified": pin_verified,
        "source_url": source_url,
        "files": records,
        "omissions": omissions,
        "bytes": used,
        "byte_budget": byte_budget,
        "time_limit_seconds": 30,
        "reviewed": False,
        "semantic_coverage": "not-established",
        "donor_executed": False,
        "associations": associations,
    }


_INDEX_SUFFIXES = frozenset(
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
        ".yml",
        ".yaml",
        ".toml",
        ".json",
        ".sh",
    }
)


def _test_path_heuristic(path: str) -> bool:
    """Test-like path naming is a navigation hint, never a coverage certificate."""
    parts = path.split("/")
    if any(part in {"test", "tests", "spec"} for part in parts[:-1]):
        return True
    basename = parts[-1]
    if not basename.endswith((".py", ".pyi")):
        return False
    return basename.startswith(("test_", "spec_")) or basename.endswith(
        ("_test.py", "_test.pyi", "_spec.py", "_spec.pyi")
    )


def source_index(
    repo: Path,
    sha: str,
    *,
    need: str,
    terms: list[str],
    prefix: str = "",
    limit: int = 24,
    symbol_files: int = 8,
) -> dict[str, Any]:
    """Navigate actual pinned files/declarations before requesting source windows.

    Ranking uses literal path matches only. It is neither semantic relevance nor
    test coverage, and the index never supplies transfer permission or code bodies.
    """
    if re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", sha) is None:
        raise CrabError("source index requires an immutable commit SHA")
    if not isinstance(need, str) or not 1 <= len(need) <= 200:
        raise CrabError("source index requires a bounded receiving need")
    if (
        not isinstance(terms, list)
        or len(terms) > 8
        or any(not isinstance(term, str) or not 1 <= len(term) <= 100 for term in terms)
    ):
        raise CrabError("source index accepts at most eight bounded literal path terms")
    if sum(len(term.encode("utf-8")) for term in terms) > 512:
        raise CrabError("source index literal terms exceed the total byte budget")
    if type(limit) is not int or not 1 <= limit <= 64:
        raise CrabError("source index file limit must be in 1..64")
    if type(symbol_files) is not int or not 0 <= symbol_files <= 16:
        raise CrabError("source index symbol-file limit must be in 0..16")
    if not isinstance(prefix, str) or (prefix and not _evidence_path(prefix.rstrip("/"))):
        raise CrabError("source index prefix must be a safe relative directory")
    if len(prefix.encode("utf-8")) > 2048:
        raise CrabError("source index directory prefix exceeds its byte budget")
    prefix = prefix.rstrip("/")
    deadline = time.monotonic() + 30
    reader = GitSourceReader(repo, byte_limit=65_536, deadline=deadline)
    pin_verified = _commit_pin(reader, sha)
    if pin_verified:
        paths, omissions, available = _source_paths(reader, sha, prefix)
    else:
        paths, omissions, available = (
            [],
            [{"path": prefix, "reason": "unavailable-or-noncanonical-commit-pin"}],
            False,
        )
    candidates = [
        path
        for path, mode in paths
        if not body_excluded(path)
        and (Path(path).suffix.casefold() in _INDEX_SUFFIXES or mode == "100755")
    ]
    lowered = [term.casefold() for term in terms]
    candidates.sort(key=lambda path: (-sum(term in path.casefold() for term in lowered), path))
    records: list[dict[str, Any]] = []
    inspected = 0
    for path in candidates[:limit]:
        record: dict[str, Any] = {
            "path": path,
            "sha": sha,
            "path_term_matches": [term for term in terms if term.casefold() in path.casefold()],
            "test_path_heuristic": _test_path_heuristic(path),
            "symbols": [],
            "symbol_status": "unsupported-structural-language",
        }
        if path.endswith((".py", ".pyi")):
            if reader.expired():
                record["symbol_status"] = "time-budget-deferred"
            elif inspected >= symbol_files:
                record["symbol_status"] = "symbol-file-budget-deferred"
            else:
                inspected += 1
                text = reader.read(sha, path)
                if text is None:
                    record["symbol_status"] = "unavailable-or-file-byte-limit"
                elif reader.expired():
                    record["symbol_status"] = "time-budget-deferred"
                else:
                    symbol_queries = [
                        {"kind": kind, "contains": term}
                        for kind in ("function", "class")
                        for term in terms[:4]
                    ] or [{"kind": "function"}, {"kind": "class"}]
                    result = syntax_query(
                        text,
                        symbol_queries,
                        timeout=deadline - time.monotonic(),
                    )
                    if (
                        not result["matches"]
                        and result["status"] == "examined-syntax"
                        and not reader.expired()
                    ):
                        result = syntax_query(
                            text,
                            [{"kind": "function"}, {"kind": "class"}],
                            timeout=deadline - time.monotonic(),
                        )
                        record["symbol_query_fallback"] = "unfiltered-navigation-only"
                    unique = {
                        (row["kind"], row["start"], row["end"], row["name"]): row
                        for row in result["matches"]
                    }
                    matches = sorted(
                        unique.values(),
                        key=lambda row: (
                            -sum(
                                term in row.get("qualified_name", row["name"]).casefold()
                                for term in lowered
                            ),
                            row["start"],
                            row["end"],
                        ),
                    )
                    record.update(
                        symbols=matches[:12],
                        symbol_status=result["status"],
                        returned_matches_omitted=max(0, len(matches) - 12),
                        line_count=len(text.splitlines()),
                        decoded_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    )
        records.append(record)
    return {
        "schema": "hungry-crab.source-index/1",
        "untrusted_data": True,
        "need": need,
        "sha": sha,
        "commit_pin_verified": pin_verified,
        "declaration_terms": terms[:4],
        "declaration_terms_not_used": terms[4:],
        "prefix": prefix,
        "files": records,
        "available": available,
        "regular_paths_observed": len(paths),
        "eligible_paths_observed": len(candidates),
        "eligible_paths_not_returned": max(0, len(candidates) - limit),
        "tree_omissions": omissions[:80],
        "tree_omission_records_not_returned": max(0, len(omissions) - 80),
        "tree_complete": available and not omissions,
        "instruction_readme_paths_excluded": sum(body_excluded(path) for path, _ in paths),
        "navigation_only": True,
        "semantic_coverage": "not-established",
        "test_coverage": "not-established",
        "license_permission": "not-established",
        "donor_executed": False,
        "file_byte_limit": 65_536,
        "time_budget_seconds": 30,
        "tree_entry_limit": 10000,
        "tree_directory_limit": 128,
        "tree_path_limit": 5000,
    }


def _source_paths(
    reader: GitSourceReader, sha: str, prefix: str = ""
) -> tuple[list[tuple[str, str]], list[dict[str, str]], bool]:
    queue = [prefix]
    files: list[tuple[str, str]] = []
    omissions: list[dict[str, str]] = []
    available = False
    observed_entries = 0
    for _ in range(128):
        if not queue or len(files) >= 5000 or observed_entries >= 10000 or reader.expired():
            break
        directory = queue.pop(0)
        entries = reader.tree_entries(sha, directory)
        if entries is None or len(entries) > 5000:
            omissions.append({"path": directory, "reason": "tree-unavailable-or-entry-limit"})
            continue
        if directory == prefix:
            available = True
        for name, mode in sorted(entries.items()):
            observed_entries += 1
            path = directory + "/" + name if directory else name
            if len(path.encode("utf-8")) > 2048 or not _evidence_path(path):
                omissions.append({"path": path[:200], "reason": "unsupported-path"})
            elif mode == "040000":
                if len(queue) < 128:
                    queue.append(path)
                else:
                    omissions.append({"path": path[:200], "reason": "directory-queue-limit"})
            elif mode in {"100644", "100755"}:
                files.append((path, mode))
                if len(files) >= 5000:
                    break
            else:
                omissions.append(
                    {"path": path[:200], "reason": "non-regular-material-not-followed"}
                )
            if observed_entries >= 10000:
                break
    if queue or len(files) >= 5000 or observed_entries >= 10000:
        omissions.append({"path": prefix, "reason": "tree-path-directory-or-time-budget"})
    return files, omissions, available


def _related(
    reader: GitSourceReader, sha: str, selected: list[str]
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """Bounded tree plus filename/import associations, never a coverage claim."""
    paths, omissions, _ = _source_paths(reader, sha)
    candidates = [path for path, _mode in paths]
    result = list(dict.fromkeys(selected))
    associations: list[dict[str, str]] = []
    for original in selected[:8]:
        stem = Path(original).stem.removeprefix("test_")
        modules: list[str] = []
        if original.endswith(".py"):
            text = reader.read(sha, original)
            if text is not None and len(text.encode()) <= 65_536:
                parsed = syntax_query(text, [{"kind": "import"}])
                modules = [str(match["name"]).replace(".", "/") for match in parsed["matches"]]
        for path in candidates:
            if path in result:
                continue
            relationship = None
            if Path(path).stem in {stem, "test_" + stem, stem + "_test"}:
                relationship = "filename-heuristic"
            elif any(
                path
                in {
                    module + ".py",
                    "src/" + module + ".py",
                    module + "/__init__.py",
                    "src/" + module + "/__init__.py",
                }
                for module in modules
            ):
                relationship = "static-import"
            if relationship:
                if len(result) >= 32:
                    omissions.append({"path": path, "reason": "related-path-budget"})
                    break
                result.append(path)
                associations.append({"from": original, "to": path, "confidence": relationship})
    return result, associations, omissions
