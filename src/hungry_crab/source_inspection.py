"""Bounded immutable source/test packets for supervised behavior discovery."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .attribution import GitSourceReader
from .compare.candidates import _evidence_path
from .errors import CrabError
from .licensing.path_grants import GrantResolver

PACKET_SCHEMA = "hungry-crab.source-packet/1"


def syntax_query(source: str, queries: list[dict[str, str]]) -> dict[str, Any]:
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
            timeout=5,
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
) -> dict[str, Any]:
    """Return all selected/omitted paths and actual ranges, not a semantic judgment."""
    if re.fullmatch(r"[a-f0-9]{40,64}", sha) is None:
        raise CrabError("source packet requires an immutable commit SHA")
    if not need or len(need) > 200 or len(paths) > 32 or len(queries) > 8:
        raise CrabError("source packet plan exceeds bounded need/path/query limits")
    if not 1024 <= byte_budget <= 512 * 1024:
        raise CrabError("source packet byte budget must be in 1024..524288")
    if any(not _evidence_path(path) for path in paths):
        raise CrabError("source packet contains an unsafe path")
    reader = GitSourceReader(repo)
    resolver = GrantResolver(reader, sha, source_url)
    records: list[dict[str, Any]] = []
    omissions: list[dict[str, str]] = []
    associations: list[dict[str, str]] = []
    if related:
        paths, associations, listing_omissions = _related(reader, sha, paths)
        omissions.extend(listing_omissions)
    used = 0
    for path in dict.fromkeys(paths):
        if Path(path).name.casefold() in {
            "readme",
            "readme.md",
            "agents.md",
            "claude.md",
            "skill.md",
        }:
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
        result: dict[str, Any] = (
            syntax_query(text, queries)
            if path.endswith((".py", ".pyi"))
            else {"status": "unsupported-structural-language", "matches": []}
        )
        matches = result["matches"]
        # Related test/source paths are a heuristic. A static association never
        # claims runtime coverage or marks a finding reviewed.
        windows: list[dict[str, Any]] = []
        for match in matches[:8] or [
            {"start": 1, "end": min(60, len(lines)), "relationship": "selected-context"}
        ]:
            start, end = match["start"], match["end"]
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
                "file_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "line_count": len(lines),
                "analysis": result["status"],
                "ranges": windows,
                "path_grant": resolver.resolve(path).to_dict(),
                "test_association": "selected-test-path"
                if any(p in {"test", "tests", "spec"} for p in path.split("/"))
                else "unconfirmed",
            }
        )
    return {
        "schema": PACKET_SCHEMA,
        "need": need,
        "sha": sha,
        "source_url": source_url,
        "files": records,
        "omissions": omissions,
        "bytes": used,
        "byte_budget": byte_budget,
        "reviewed": False,
        "semantic_coverage": "not-established",
        "donor_executed": False,
        "associations": associations,
    }


def _related(
    reader: GitSourceReader, sha: str, selected: list[str]
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """Bounded tree plus filename/import associations, never a coverage claim."""
    queue: list[str] = [""]
    candidates: list[str] = []
    omissions: list[dict[str, str]] = []
    for _ in range(128):
        if not queue or len(candidates) >= 5000:
            break
        directory = queue.pop(0)
        entries = reader.tree_entries(sha, directory)
        if entries is None or len(entries) > 5000:
            omissions.append({"path": directory, "reason": "related-tree-unavailable-or-limit"})
            continue
        for name, mode in sorted(entries.items()):
            path = directory + "/" + name if directory else name
            if mode == "040000" and len(queue) < 128:
                queue.append(path)
            elif mode in {"100644", "100755"}:
                candidates.append(path)
                if len(candidates) >= 5000:
                    break
    if queue or len(candidates) >= 5000:
        omissions.append({"path": "", "reason": "related-tree-budget"})
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
