"""Architecture miner: directory roles, a regex symbol index and the internal import graph.

Regex parsers for TypeScript/JavaScript, Python and C# are deliberately shallow: enough to name
the hubs (files everything imports), the orchestrators (files that import everything) and the
public surface, which is what an architect subagent needs to start from.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from ..mdutil import MdDoc
from .base import FileInfo, MineContext, MinerResult

MAX_FILES = {"normal": 3000, "deep": 8000}
READ_LIMIT = 400_000

_TS_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
    r"(?:function\*?|class|const|let|var|interface|type|enum|abstract\s+class)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_TS_EXPORT_LIST_RE = re.compile(r"^\s*export\s*\{([^}]*)\}", re.MULTILINE)
_TS_FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_TS_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE
)
_TS_IMPORT_RE = re.compile(
    r"""(?:^|\s)(?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]""", re.MULTILINE
)
_TS_REQUIRE_RE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", re.MULTILINE)
_PY_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.MULTILINE)
_PY_FROM_RE = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+", re.MULTILINE)
_PY_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)
_PY_ALL_RE = re.compile(r"__all__\s*=\s*[\[(]([^\])]*)[\])]", re.DOTALL)
_CS_TYPE_RE = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*(?:(public|internal|private|protected)\s+)?"
    r"(?:static\s+|abstract\s+|sealed\s+|partial\s+|readonly\s+|unsafe\s+)*"
    r"(class|interface|record|struct|enum)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)
_CS_METHOD_RE = re.compile(
    r"^\s*(?:public|internal|protected|private)\s+(?:static\s+|virtual\s+|override\s+|async\s+)*"
    r"[\w<>\[\],\s]+?\s+([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)
_CS_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.MULTILINE)
_CS_USING_RE = re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)
_TS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_GROUP_DIRS = ("src", "lib", "packages", "apps", "tests", "test")


def _resolve_ts(source: str, spec: str, files: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    base = source.rsplit("/", 1)[0] if "/" in source else ""
    parts = (base.split("/") if base else []) + spec.split("/")
    stack: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if stack:
                stack.pop()
            continue
        stack.append(part)
    target = "/".join(stack)
    for candidate in (
        target,
        *(f"{target}{ext}" for ext in _TS_EXTS),
        *(f"{target}/index{ext}" for ext in _TS_EXTS),
    ):
        if candidate in files:
            return candidate
    stripped = re.sub(r"\.(?:js|mjs|cjs)$", "", target)
    for ext in (".ts", ".tsx", ".mts"):
        if f"{stripped}{ext}" in files:
            return f"{stripped}{ext}"
    return None


def _python_modules(files: list[FileInfo]) -> dict[str, str]:
    """module name -> file path, for every layout root (src/, repo root, package dirs)."""
    modules: dict[str, str] = {}
    for info in files:
        if info.ext != ".py":
            continue
        parts = info.path[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):
            name = ".".join(parts[start:])
            if name and name not in modules:
                modules[name] = info.path
    return modules


def _suffixes(name: str) -> list[str]:
    """``a.b.c`` -> [``a.b.c``, ``b.c``, ``c``]: layouts differ in how many leading dirs matter."""
    parts = name.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def _parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def _lookup(source: str, candidates: list[str], modules: dict[str, str]) -> str | None:
    if candidates and modules.get(candidates[0]) == source:
        return None  # a module naming itself is not an edge to its package
    for name in candidates:
        path = modules.get(name)
        if path and path != source:
            return path
    return None


def _resolve_py(source: str, spec: str, modules: dict[str, str]) -> str | None:
    if spec.startswith("."):
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec.lstrip(".")
        # the containing package of a module, or the package itself for __init__.py
        base_parts = source[:-3].split("/")[:-1]
        if dots > 1:
            base_parts = base_parts[: len(base_parts) - (dots - 1)]
        candidate = ".".join([*base_parts, *([rest] if rest else [])])
        names = [n for suffix in _suffixes(candidate) for n in (suffix, *_parents(suffix))]
        return _lookup(source, names, modules)
    return _lookup(source, [spec, *_parents(spec)], modules)


def _top_dir(path: str) -> str:
    parts = path.split("/")
    if len(parts) <= 1:
        return "."
    if parts[0] in _GROUP_DIRS and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def _namespace_parents(namespace: str) -> list[str]:
    parts = namespace.split(".")
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


class ArchitectureMiner:
    name = "architecture"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "architecture.json"
    md_file = "architecture.md"

    def run(self, ctx: MineContext) -> MinerResult:
        code = [f for f in ctx.files() if f.counted and f.is_code and f.size <= READ_LIMIT]
        limit = MAX_FILES["deep" if ctx.deep else "normal"]
        truncated = len(code) > limit
        code = code[:limit]
        paths = {f.path for f in code}
        modules = _python_modules(code)
        namespaces: dict[str, list[str]] = defaultdict(list)
        file_namespaces: dict[str, list[str]] = defaultdict(list)
        texts: dict[str, str] = {}
        symbols: dict[str, dict[str, Any]] = {}
        for info in code:
            text = ctx.read(info.path, limit=READ_LIMIT)
            texts[info.path] = text
            entry: dict[str, Any] = {
                "functions": 0,
                "classes": 0,
                "exports": [] if info.ext in _TS_EXTS else None,
                "public": [],
            }
            if info.ext in _TS_EXTS:
                names = _TS_EXPORT_RE.findall(text)
                for group in _TS_EXPORT_LIST_RE.findall(text):
                    names += [n.strip().split(" as ")[-1] for n in group.split(",") if n.strip()]
                entry["exports"] = sorted(set(names))[:60]
                entry["functions"] = len(_TS_FUNCTION_RE.findall(text))
                entry["classes"] = len(_TS_CLASS_RE.findall(text))
                entry["public"] = entry["exports"]
            elif info.ext == ".py":
                entry["functions"] = len(_PY_DEF_RE.findall(text))
                entry["classes"] = len(_PY_CLASS_RE.findall(text))
                all_match = _PY_ALL_RE.search(text)
                if all_match:
                    entry["public"] = sorted(
                        {n.strip().strip("'\"") for n in all_match.group(1).split(",") if n.strip()}
                    )[:60]
                else:
                    found = _PY_DEF_RE.findall(text) + _PY_CLASS_RE.findall(text)
                    entry["public"] = sorted({n for n in found if not n.startswith("_")})[:60]
            elif info.ext == ".cs":
                types = _CS_TYPE_RE.findall(text)
                entry["classes"] = len(types)
                entry["functions"] = len(_CS_METHOD_RE.findall(text))
                entry["public"] = sorted(
                    {name for visibility, _, name in types if visibility == "public"}
                )[:60]
                for namespace in _CS_NAMESPACE_RE.findall(text):
                    namespaces[namespace].append(info.path)
                    file_namespaces[info.path].append(namespace)
            symbols[info.path] = entry

        edges: set[tuple[str, str]] = set()
        external: Counter[str] = Counter()
        for info in code:
            text = texts[info.path]
            if info.ext in _TS_EXTS:
                specs = _TS_IMPORT_RE.findall(text) + _TS_REQUIRE_RE.findall(text)
                for spec in specs:
                    target = _resolve_ts(info.path, spec, paths)
                    if target and target != info.path:
                        edges.add((info.path, target))
                    elif not spec.startswith("."):
                        head = spec.split("/")
                        external["/".join(head[:2]) if spec.startswith("@") else head[0]] += 1
            elif info.ext == ".py":
                for spec in _PY_FROM_RE.findall(text) + _PY_IMPORT_RE.findall(text):
                    target = _resolve_py(info.path, spec, modules)
                    if target:
                        edges.add((info.path, target))
                    elif not spec.startswith("."):
                        external[spec.split(".")[0]] += 1
            elif info.ext == ".cs":
                own = set(file_namespaces.get(info.path, []))
                # parent namespaces are visible without a using directive
                implicit = {p for ns in own for p in _namespace_parents(ns)}
                for used in set(_CS_USING_RE.findall(text)) | implicit:
                    if used in namespaces and used not in own:
                        for target in namespaces[used][:5]:
                            if target != info.path:
                                edges.add((info.path, target))
                    elif used not in namespaces and used not in implicit:
                        external[used.split(".")[0]] += 1

        in_degree: Counter[str] = Counter()
        out_degree: Counter[str] = Counter()
        dir_edges: Counter[tuple[str, str]] = Counter()
        for source, target in edges:
            in_degree[target] += 1
            out_degree[source] += 1
            source_dir, target_dir = _top_dir(source), _top_dir(target)
            if source_dir != target_dir:
                dir_edges[(source_dir, target_dir)] += 1
        dir_cycles = sorted(
            {tuple(sorted(pair)) for pair in dir_edges if (pair[1], pair[0]) in dir_edges}
        )
        dir_in: Counter[str] = Counter()
        dir_out: Counter[str] = Counter()
        for (source_dir, target_dir), count in dir_edges.items():
            dir_out[source_dir] += count
            dir_in[target_dir] += count
        layer_rows: list[dict[str, Any]] = [
            {"dir": d, "imported_by": dir_in[d], "imports": dir_out[d]}
            for d in set(dir_in) | set(dir_out)
        ]
        layer_rows.sort(
            key=lambda row: (-(int(row["imported_by"]) - int(row["imports"])), str(row["dir"]))
        )
        hubs = [
            {"path": path, "imported_by": in_degree[path], "imports": out_degree[path]}
            for path, _ in in_degree.most_common(10)
        ]
        orchestrators = [
            {"path": path, "imports": out_degree[path], "imported_by": in_degree[path]}
            for path, _ in out_degree.most_common(10)
        ]
        by_symbols = sorted(
            symbols.items(), key=lambda kv: (-(kv[1]["functions"] + kv[1]["classes"]), kv[0])
        )[:15]
        entry_names = ("__init__.py", "index.ts", "index.js", "mod.ts")
        public_surface = [
            {"path": path, "names": entry["public"][:30]}
            for path, entry in symbols.items()
            if entry["public"] and (path.endswith(entry_names) or in_degree[path] >= 2)
        ][:12]
        data: dict[str, Any] = {
            "available": bool(code),
            "files_scanned": len(code),
            "truncated": truncated,
            "languages": sorted({f.language or "" for f in code if f.language}),
            "totals": {
                "functions": sum(e["functions"] for e in symbols.values()),
                "classes": sum(e["classes"] for e in symbols.values()),
                "exports": sum(len(e["exports"]) for e in symbols.values() if e["exports"]),
            },
            "top_files_by_symbols": [
                {
                    "path": path,
                    "functions": e["functions"],
                    "classes": e["classes"],
                    "public": len(e["public"]),
                }
                for path, e in by_symbols
            ],
            "public_surface": public_surface,
            "graph": {
                "nodes": len({p for edge in edges for p in edge}),
                "edges": len(edges),
                "hubs": hubs,
                "orchestrators": orchestrators,
                "dir_edges": [
                    {"from": s, "to": t, "count": c} for (s, t), c in dir_edges.most_common(30)
                ],
                "dir_cycles": [list(pair) for pair in dir_cycles[:10]],
                "layers": layer_rows[:15],
                "external_top": [
                    {"name": name, "imports": count} for name, count in external.most_common(15)
                ],
            },
            "edges": [list(edge) for edge in sorted(edges)[:2000]],
        }
        warnings = ["symbol index truncated; use --depth deep"] if truncated else []
        return MinerResult(self.name, data, doc=self._doc(ctx, data), warnings=warnings)

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"Architecture: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        if not data["available"]:
            summary.para("No code files to index.")
            return doc
        graph = data["graph"]
        totals = data["totals"]
        cycles = ", ".join(" <-> ".join(pair) for pair in graph["dir_cycles"]) or "none"
        externals = (
            ", ".join(f"{e['name']} ({e['imports']})" for e in graph["external_top"][:8]) or "none"
        )
        indexed = f"{data['files_scanned']}" + (" (truncated)" if data["truncated"] else "")
        summary.kv(
            [
                ("Code files indexed", indexed),
                ("Languages", ", ".join(data["languages"])),
                (
                    "Functions / classes / exports",
                    f"{totals['functions']} / {totals['classes']} / {totals['exports']}",
                ),
                ("Internal import graph", f"{graph['nodes']} files, {graph['edges']} edges"),
                ("Directory cycles", cycles),
                ("Top external imports", externals),
            ]
        )
        hubs = doc.section("Hubs (most imported files)", priority=1)
        hubs.table(
            ["File", "Imported by", "Imports"],
            ([h["path"], h["imported_by"], h["imports"]] for h in graph["hubs"]),
        )
        orchestrators = doc.section("Orchestrators (files that import the most)", priority=3)
        orchestrators.table(
            ["File", "Imports", "Imported by"],
            ([o["path"], o["imports"], o["imported_by"]] for o in graph["orchestrators"]),
        )
        layers = doc.section("Directory layering (imported-by minus imports)", priority=2)
        layers.table(
            ["Directory", "Imported by", "Imports"],
            ([row["dir"], row["imported_by"], row["imports"]] for row in graph["layers"]),
        )
        if data["public_surface"]:
            surface = doc.section("Public surface", priority=2)
            for entry in data["public_surface"]:
                more = " ..." if len(entry["names"]) > 15 else ""
                surface.line(f"- `{entry['path']}`: {', '.join(entry['names'][:15])}{more}")
            surface.line("")
        biggest = doc.section("Files with the most symbols", priority=4)
        biggest.table(
            ["File", "Functions", "Classes", "Public"],
            (
                [f["path"], f["functions"], f["classes"], f["public"]]
                for f in data["top_files_by_symbols"]
            ),
        )
        return doc
