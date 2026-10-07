"""Optional, bounded syntax analysis. No source text or prey code is executed or exported.

The call graph is deliberately a graph of lexical references, not a claim about runtime
dispatch. Only unambiguous bare calls in the same lexical file are resolved. Member calls,
imports, overloads and dynamic dispatch remain explicit unresolved references.
"""

from __future__ import annotations

import base64
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Protocol, cast

from ..mdutil import MdDoc
from .base import MineContext, MinerResult

GRAMMARS: dict[str, tuple[str, str]] = {
    ".py": ("python", "language"),
    ".pyi": ("python", "language"),
    ".js": ("javascript", "language"),
    ".jsx": ("javascript", "language"),
    ".mjs": ("javascript", "language"),
    ".cjs": ("javascript", "language"),
    ".ts": ("typescript", "language_typescript"),
    ".mts": ("typescript", "language_typescript"),
    ".cts": ("typescript", "language_typescript"),
    ".tsx": ("typescript", "language_tsx"),
    ".cs": ("c_sharp", "language"),
    ".go": ("go", "language"),
    ".rs": ("rust", "language"),
    ".java": ("java", "language"),
    ".php": ("php", "language_php"),
    ".rb": ("ruby", "language"),
}
DEFINITIONS = {
    "function_definition": "function",
    "function_declaration": "function",
    "function_item": "function",
    "generator_function_declaration": "function",
    "method_definition": "method",
    "method_declaration": "method",
    "method": "method",
    "singleton_method": "method",
    "constructor_declaration": "constructor",
    "class_definition": "class",
    "class_declaration": "class",
    "class": "class",
    "interface_declaration": "interface",
    "struct_item": "struct",
    "enum_item": "enum",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "trait_item": "interface",
    "type_alias_declaration": "type",
}
CALLS = {
    "call",
    "call_expression",
    "invocation_expression",
    "method_invocation",
    "function_call_expression",
}
IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*[!?]?$", re.UNICODE)
READ_LIMIT = 400_000
NODE_LIMIT = 100_000


class Node(Protocol):
    type: str
    start_byte: int
    end_byte: int
    start_point: tuple[int, int]
    end_point: tuple[int, int]
    named_children: list[Node]
    has_error: bool

    def child_by_field_name(self, name: str) -> Node | None: ...


class Tree(Protocol):
    root_node: Node


def runtime_identity() -> dict[str, str]:
    """Parser/grammar versions are part of digest identity, including missing extras."""
    result: dict[str, str] = {}
    for name in sorted({"tree-sitter", *(f"tree-sitter-{g[0]}" for g in GRAMMARS.values())}):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "unavailable"
    return result


def _name(node: Node | None, source: bytes) -> str:
    if node is None or node.end_byte - node.start_byte > 200:
        return ""
    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    return text if IDENTIFIER.fullmatch(text) else ""


def _parse(source: bytes, grammar: tuple[str, str]) -> Tree | None:
    binding = importlib.import_module("tree_sitter")
    module = importlib.import_module(f"tree_sitter_{grammar[0]}")
    parser = binding.Parser(binding.Language(getattr(module, grammar[1])()))
    # Bytes avoid the binding's Windows callback crash. The parent process supplies the
    # timeout; native parsing never takes place in the CLI process.
    return cast(Tree | None, parser.parse(source))


def index_source(source: bytes, path: str, grammar: tuple[str, str]) -> dict[str, Any]:
    return index_sources([(source, path, grammar)])[0]


def index_sources(inputs: list[tuple[bytes, str, tuple[str, str]]]) -> list[dict[str, Any]]:
    failures = [
        {"path": path, "status": "parser-crash", "symbols": [], "calls": []}
        for _, path, _ in inputs
    ]
    payload = json.dumps(
        [
            {
                "source": base64.b64encode(source).decode("ascii"),
                "path": path,
                "grammar": list(grammar),
            }
            for source, path, grammar in inputs
        ]
    ).encode()
    if len(payload) > 4 * 1024 * 1024 or any(len(s) > READ_LIMIT for s, _, _ in inputs):
        return [{**failure, "status": "size-limit"} for failure in failures]
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-m", "hungry_crab.symbol_worker"],
            input=payload,
            capture_output=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [{**failure, "status": "parser-timeout"} for failure in failures]
    except OSError:
        return failures
    if proc.returncode or len(proc.stdout) > 8 * 1024 * 1024:
        return failures
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        return failures
    if (
        not isinstance(result, list)
        or len(result) != len(inputs)
        or not all(isinstance(r, dict) for r in result)
    ):
        return failures
    return cast(list[dict[str, Any]], result)


def _index_source(source: bytes, path: str, grammar: tuple[str, str]) -> dict[str, Any]:
    tree = _parse(source, grammar)
    if tree is None:
        return {"path": path, "status": "parser-limit", "symbols": [], "calls": []}
    if tree.root_node.has_error:
        return {"path": path, "status": "syntax-error", "symbols": [], "calls": []}
    symbols: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    bindings: dict[str, set[str]] = {}
    pending: list[tuple[Node, str, str]] = [(tree.root_node, "", "")]
    visited = 0
    while pending:
        node, scope, caller = pending.pop()
        visited += 1
        if visited > NODE_LIMIT:
            return {"path": path, "status": "node-limit", "symbols": [], "calls": []}
        kind = DEFINITIONS.get(node.type)
        name_node = node.child_by_field_name("name")
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in {"arrow_function", "function_expression"}:
                kind = "function"
        name = _name(name_node, source) if kind else ""
        if name:
            qualified = f"{scope}.{name}" if scope else name
            sid = f"{path}::{qualified}"
            parameters = node.child_by_field_name("parameters")
            if parameters is not None:
                bindings.setdefault(qualified, set()).update(_identifiers(parameters, source))
            symbols.append(
                {
                    "id": sid,
                    "path": path,
                    "name": name,
                    "qualified_name": qualified,
                    "scope": scope,
                    "kind": kind,
                    "language": grammar[0],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "parameter_nodes": len(parameters.named_children) if parameters else None,
                }
            )
            scope = qualified
            caller = sid if kind in {"function", "method", "constructor"} else ""
        if (
            node.type
            in {
                "assignment",
                "augmented_assignment",
                "named_expression",
                "assignment_expression",
                "variable_declarator",
                "short_var_declaration",
                "var_spec",
                "let_declaration",
                "for_statement",
                "for_in_statement",
                "enhanced_for_statement",
                "foreach_statement",
            }
            and not name
        ):
            for field in ("left", "name", "pattern", "variable"):
                bound = node.child_by_field_name(field)
                if bound is not None:
                    bindings.setdefault(scope, set()).update(_identifiers(bound, source))
        if node.type in {
            "import_statement",
            "import_from_statement",
            "import_declaration",
            "use_declaration",
        }:
            bindings.setdefault(scope, set()).update(_identifiers(node, source))
        if node.type in CALLS and caller:
            target_node = node.child_by_field_name("function")
            if target_node is None:
                target_node = node.child_by_field_name("name")
            if target_node is None and node.type == "call":
                target_node = node.child_by_field_name("method")
            receiver = node.child_by_field_name("receiver") or node.child_by_field_name("object")
            callee = _name(target_node, source) if receiver is None else ""
            calls.append(
                {
                    "caller": caller,
                    "name": callee or None,
                    "line": node.start_point[0] + 1,
                    "resolution": "unresolved",
                }
            )
        pending.extend((child, scope, caller) for child in reversed(node.named_children))
    # Duplicate ids denote overloads/redefinitions. Neither is a unique symbol target.
    counts = Counter(str(s["id"]) for s in symbols)
    by_name = {(str(s["scope"]), str(s["name"])): s for s in symbols if counts[s["id"]] == 1}
    by_id = {str(s["id"]): s for s in symbols if counts[s["id"]] == 1}
    class_scopes = {
        str(s["qualified_name"]) for s in symbols if s["kind"] in {"class", "struct", "interface"}
    }
    for call in calls:
        owner = by_id.get(str(call["caller"]))
        if owner is None or not call["name"]:
            continue
        # Bare calls resolve through enclosing function scopes and module scope. A class's
        # methods do not become bare names in Python/JS, so class scopes are excluded.
        parts = str(owner["qualified_name"]).split(".")
        scopes = [".".join(parts[:i]) for i in range(len(parts), -1, -1)]
        if any(call["name"] in bindings.get(scope, set()) for scope in scopes):
            continue
        for lexical_scope in dict.fromkeys(scopes):
            if lexical_scope in class_scopes and grammar[0] in {
                "python",
                "javascript",
                "typescript",
            }:
                continue
            target = by_name.get((lexical_scope, str(call["name"])))
            method_language = grammar[0] in {"java", "c_sharp", "ruby"}
            if target and (
                target["kind"] == "function" or (method_language and target["kind"] == "method")
            ):
                call.update(target=target["id"], resolution="lexical-same-file")
                break
    for symbol in symbols:
        symbol["ambiguous"] = counts[symbol["id"]] > 1
    return {"path": path, "status": "ok", "symbols": symbols, "calls": calls}


def _identifiers(node: Node, source: bytes) -> set[str]:
    """Conservative binding names; type names may suppress an edge rather than invent one."""
    names: set[str] = set()
    pending = [node]
    while pending:
        child = pending.pop()
        if child.type in {"identifier", "type_identifier", "variable_name"}:
            name = _name(child, source)
            if name:
                names.add(name)
        pending.extend(child.named_children)
    return names


class SymbolsMiner:
    name = "symbols"
    requires = ("inventory",)
    json_file = "symbols.json"
    md_file = "symbols.md"

    def run(self, ctx: MineContext) -> MinerResult:
        identity = runtime_identity()
        files = sorted((f for f in ctx.files() if f.counted and f.is_code), key=lambda f: f.path)
        cap = 8000 if ctx.deep else 1000
        records: list[dict[str, Any]] = []
        inputs: list[tuple[bytes, str, tuple[str, str]]] = []
        for info in files[:cap]:
            grammar = GRAMMARS.get(info.ext)
            status = "unsupported-language"
            if grammar and info.size <= READ_LIMIT:
                if (
                    identity["tree-sitter"] == "unavailable"
                    or identity[f"tree-sitter-{grammar[0]}"] == "unavailable"
                ):
                    status = "unavailable-extra"
                else:
                    # Inventory excludes symlinks, binaries and vendored/generated files.
                    source = ctx.read(info.path, limit=READ_LIMIT).encode("utf-8", errors="replace")
                    if inputs and (
                        len(inputs) >= 16
                        or sum(len(s) for s, _, _ in inputs) + len(source) > 2 * 1024 * 1024
                    ):
                        records.extend(index_sources(inputs))
                        inputs = []
                    inputs.append((source, info.path, grammar))
                    continue
            elif grammar:
                status = "size-limit"
            records.append({"path": info.path, "status": status, "symbols": [], "calls": []})
        if inputs:
            records.extend(index_sources(inputs))
        records.extend(
            {"path": f.path, "status": "file-limit", "symbols": [], "calls": []}
            for f in files[cap:]
        )
        records.sort(key=lambda r: str(r["path"]))
        symbols = [s for record in records for s in record["symbols"]]
        calls = [c for record in records for c in record["calls"]]
        edges = sorted({(str(c["caller"]), str(c["target"])) for c in calls if "target" in c})
        data: dict[str, Any] = {
            "schema": "hungry-crab.symbols/1",
            "available": any(r["status"] == "ok" for r in records),
            "runtime": identity,
            "coverage": dict(sorted(Counter(r["status"] for r in records).items())),
            "files": [
                {k: v for k, v in r.items() if k not in {"symbols", "calls"}} for r in records
            ],
            "symbols": symbols,
            "calls": calls,
            "edges": [list(e) for e in edges],
            "graph_kind": "lexical-same-file",
            "unresolved_calls": sum("target" not in c for c in calls),
        }
        doc = MdDoc(f"Symbols: {ctx.label}", source=ctx.source_line())
        section = doc.section("Coverage", priority=1)
        section.kv([(k, v) for k, v in data["coverage"].items()])
        section.para(
            "Only lexical same-file calls are resolved; imports, dispatch and overloads "
            "remain unresolved. Install hungry-crab[deep] for the optional parsers."
        )
        doc.section("Declarations", priority=2).table(
            ["Path", "Symbol", "Kind", "Lines"],
            (
                [s["path"], s["qualified_name"], s["kind"], f"{s['start_line']}-{s['end_line']}"]
                for s in symbols
            ),
        )
        warnings = [
            f"symbols: {count} files: {status}"
            for status, count in data["coverage"].items()
            if status not in {"ok", "unsupported-language", "unavailable-extra"}
        ]
        return MinerResult(self.name, data, doc=doc, warnings=warnings)
