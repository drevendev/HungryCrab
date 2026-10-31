"""Isolated stdlib AST inspection. Input is parsed, never imported or executed."""

from __future__ import annotations

import ast
import json
import sys
from typing import Any


def qualified(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
        if len(parts) > 20:
            return ""
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def inspect(source: str, queries: list[dict[str, str]]) -> dict[str, Any]:
    if len(source.encode("utf-8")) > 65_536 or len(queries) > 8:
        return {"status": "budget-deferred", "matches": [], "reason": "input-limit"}
    supported = {"call", "function", "class", "import", "raise", "except", "with", "compare"}
    if any(q.get("kind") not in supported or len(q.get("name", "")) > 100 for q in queries):
        return {"status": "unsupported", "matches": []}
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError, MemoryError, ValueError):
        return {"status": "unsupported-syntax", "matches": []}
    matches: list[dict[str, Any]] = []
    for count, node in enumerate(ast.walk(tree)):
        if count >= 30_000:
            return {"status": "budget-deferred", "matches": matches, "reason": "node-limit"}
        kind, name = "", ""
        if isinstance(node, ast.Call):
            kind, name = "call", qualified(node.func)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            kind, name = "function", node.name
        elif isinstance(node, ast.ClassDef):
            kind, name = "class", node.name
        elif isinstance(node, ast.Import):
            kind, name = "import", ",".join(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            kind, name = "import", node.module or ""
        elif isinstance(node, ast.Raise):
            kind, name = "raise", qualified(node.exc.func) if isinstance(node.exc, ast.Call) else ""
        elif isinstance(node, ast.ExceptHandler):
            kind, name = "except", qualified(node.type) if node.type else ""
        elif isinstance(node, ast.With | ast.AsyncWith):
            kind = "with"
        elif isinstance(node, ast.Compare):
            kind = "compare"
        for query in queries:
            requested = query.get("name", "")
            if kind == query["kind"] and (
                not requested or requested == name or requested in name.split(",")
            ):
                start = int(getattr(node, "lineno", 1))
                matches.append(
                    {
                        "kind": kind,
                        "name": name[:100],
                        "start": start,
                        "end": min(
                            getattr(node, "end_lineno", start) or start,
                            start + 59,
                        ),
                        "relationship": "syntax-only",
                    }
                )
                if len(matches) >= 100:
                    return {
                        "status": "budget-deferred",
                        "matches": matches,
                        "reason": "match-limit",
                    }
    return {"status": "examined-syntax", "matches": matches}


def main() -> None:
    try:
        # Bound before JSON decoding. The process gets only JSON/source as data.
        raw = sys.stdin.buffer.read(100_001)
        if len(raw) > 100_000:
            raise ValueError("input-limit")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("source"), str)
            or not isinstance(value.get("queries"), list)
        ):
            raise ValueError("invalid-input")
        if not all(
            isinstance(q, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in q.items())
            for q in value["queries"]
        ):
            raise ValueError("invalid-query")
        print(json.dumps(inspect(value["source"], value["queries"])))
    except (ValueError, KeyError, TypeError, RecursionError, MemoryError):
        print(json.dumps({"status": "unsupported", "matches": []}))


if __name__ == "__main__":
    main()
