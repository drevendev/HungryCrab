"""Inventory miner: the shape of the tree.

Files, languages, lines of code, manifests, lock files, entry points, vendored and generated
content, largest files. Every other miner reads the file list this one produces, so it walks the
tree exactly once and never follows symlinks.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..fs import BINARY_EXTENSIONS, count_lines, is_ignored, looks_binary
from ..mdutil import MdDoc
from .base import FileInfo, MineContext, MinerResult

LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "Python", ".pyi": "Python", ".pyx": "Cython",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".cs": "C#", ".fs": "F#", ".fsx": "F#", ".vb": "Visual Basic",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala", ".groovy": "Groovy", ".rb": "Ruby", ".php": "PHP", ".pl": "Perl",
    ".pm": "Perl", ".c": "C", ".h": "C/C++ Header", ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".hpp": "C++", ".hh": "C++", ".m": "Objective-C", ".mm": "Objective-C++", ".swift": "Swift",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Shell", ".ps1": "PowerShell",
    ".psm1": "PowerShell", ".psd1": "PowerShell", ".bat": "Batch", ".cmd": "Batch",
    ".sql": "SQL", ".lua": "Lua", ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hs": "Haskell", ".clj": "Clojure", ".cljs": "Clojure", ".r": "R",
    ".jl": "Julia", ".zig": "Zig", ".nim": "Nim", ".ml": "OCaml", ".elm": "Elm",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "Sass",
    ".less": "Less", ".vue": "Vue", ".svelte": "Svelte", ".astro": "Astro",
    ".md": "Markdown", ".mdx": "Markdown", ".markdown": "Markdown", ".rst": "reStructuredText",
    ".adoc": "AsciiDoc", ".txt": "Text",
    ".yml": "YAML", ".yaml": "YAML", ".json": "JSON", ".jsonc": "JSON", ".json5": "JSON",
    ".toml": "TOML", ".xml": "XML", ".ini": "INI", ".cfg": "INI", ".conf": "Config",
    ".properties": "Properties", ".env": "Dotenv",
    ".csproj": "MSBuild", ".fsproj": "MSBuild", ".vbproj": "MSBuild", ".props": "MSBuild",
    ".targets": "MSBuild", ".sln": "Solution", ".slnx": "Solution",
    ".tf": "HCL", ".hcl": "HCL", ".proto": "Protocol Buffers", ".graphql": "GraphQL",
    ".gql": "GraphQL", ".prisma": "Prisma", ".ipynb": "Jupyter", ".cmake": "CMake",
    ".gradle": "Gradle", ".mk": "Makefile", ".nix": "Nix", ".dockerfile": "Dockerfile",
    ".svg": "SVG", ".tex": "TeX", ".bib": "BibTeX", ".csv": "CSV", ".tsv": "CSV",
}  # fmt: skip

LANGUAGE_BY_NAME: dict[str, str] = {
    "Dockerfile": "Dockerfile", "Containerfile": "Dockerfile", "Makefile": "Makefile",
    "GNUmakefile": "Makefile", "Justfile": "Just", "justfile": "Just",
    "CMakeLists.txt": "CMake", "Jenkinsfile": "Groovy", "Gemfile": "Ruby", "Rakefile": "Ruby",
    "Vagrantfile": "Ruby", "Podfile": "Ruby", "Brewfile": "Ruby", "Procfile": "Text",
    ".editorconfig": "INI", ".gitignore": "Git", ".gitattributes": "Git", ".gitmodules": "Git",
    ".npmrc": "INI", ".nvmrc": "Text", ".python-version": "Text", ".tool-versions": "Text",
    ".prettierrc": "JSON", ".eslintrc": "JSON", ".babelrc": "JSON", ".cursorrules": "Text",
    ".windsurfrules": "Text", ".clinerules": "Text", "LICENSE": "Text", "LICENCE": "Text",
    "COPYING": "Text", "NOTICE": "Text", "AUTHORS": "Text", "CODEOWNERS": "Text",
}  # fmt: skip

CODE_LANGUAGES = frozenset(
    {
        "Python", "Cython", "TypeScript", "JavaScript", "C#", "F#", "Visual Basic", "Go", "Rust",
        "Java", "Kotlin", "Scala", "Groovy", "Ruby", "PHP", "Perl", "C", "C/C++ Header", "C++",
        "Objective-C", "Objective-C++", "Swift", "Shell", "PowerShell", "Batch", "SQL", "Lua",
        "Dart", "Elixir", "Erlang", "Haskell", "Clojure", "R", "Julia", "Zig", "Nim", "OCaml",
        "Elm", "Vue", "Svelte", "Astro",
    }
)  # fmt: skip

MANIFEST_BY_NAME: dict[str, str] = {
    "package.json": "npm", "pyproject.toml": "python", "setup.py": "python",
    "setup.cfg": "python", "Pipfile": "python", "go.mod": "go", "Cargo.toml": "rust",
    "pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle",
    "Gemfile": "ruby", "composer.json": "php", "pubspec.yaml": "dart", "mix.exs": "elixir",
    "Package.swift": "swift", "deno.json": "deno", "deno.jsonc": "deno",
    "Directory.Packages.props": "dotnet", "Directory.Build.props": "dotnet",
    "global.json": "dotnet", "CMakeLists.txt": "cmake", "Makefile": "make",
    "Dockerfile": "docker", "docker-compose.yml": "compose", "docker-compose.yaml": "compose",
    "compose.yml": "compose", "compose.yaml": "compose", "flake.nix": "nix",
}  # fmt: skip
MANIFEST_BY_EXT: dict[str, str] = {
    ".csproj": "dotnet", ".fsproj": "dotnet", ".vbproj": "dotnet", ".sln": "dotnet",
    ".slnx": "dotnet",
}  # fmt: skip
_REQUIREMENTS_RE = re.compile(r"^requirements[\w.-]*\.txt$", re.IGNORECASE)

LOCKFILES: dict[str, str] = {
    "package-lock.json": "npm", "npm-shrinkwrap.json": "npm", "yarn.lock": "npm",
    "pnpm-lock.yaml": "npm", "bun.lock": "npm", "bun.lockb": "npm", "uv.lock": "python",
    "poetry.lock": "python", "Pipfile.lock": "python", "pdm.lock": "python",
    "packages.lock.json": "dotnet", "go.sum": "go", "Cargo.lock": "rust",
    "Gemfile.lock": "ruby", "composer.lock": "php", "pubspec.lock": "dart",
    "flake.lock": "nix", "deno.lock": "deno",
}  # fmt: skip

IGNORED_DIRS = frozenset({".git", ".hg", ".svn", ".jj"})
VENDORED_DIRS = frozenset(
    {
        "node_modules", "bower_components", "jspm_packages", "vendor", "vendors", "third_party",
        "third-party", "thirdparty", "external", "externals", "extern", ".venv", "venv",
        "virtualenv", "__pycache__", ".tox", ".nox", ".mypy_cache", ".ruff_cache",
        ".pytest_cache", ".yarn", ".pnp", "Pods", "Carthage", ".gradle", ".terraform",
        "site-packages",
    }
)  # fmt: skip
BUILD_OUTPUT_DIRS = frozenset(
    {
        "dist", "build", "out", ".next", ".nuxt", ".output", ".svelte-kit", ".turbo",
        "coverage", "htmlcov", "_site", ".docusaurus", ".parcel-cache", "storybook-static",
        "TestResults", ".angular",
    }
)  # fmt: skip
GENERATED_FILE_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\.min\.(?:js|css|mjs)$", r"\.bundle\.js$", r"\.js\.map$", r"\.css\.map$",
        r"\.g\.cs$", r"\.designer\.cs$", r"\.generated\.\w+$", r"_pb2(?:_grpc)?\.py$",
        r"\.pb\.go$", r"\.pb\.cc$", r"\.pb\.h$", r"\.g\.dart$", r"\.freezed\.dart$",
    )
)  # fmt: skip

MAX_FILES = {"normal": 40_000, "deep": 120_000}
MAX_TEXT_SIZE = 1024 * 1024
MAX_VENDORED_PER_DIR = 3000

ROLE_BY_NAME: dict[str, str] = {
    "src": "source", "lib": "source", "app": "source", "modules": "source", "cmd": "source",
    "internal": "source", "pkg": "source", "core": "source", "server": "source",
    "client": "source", "api": "source", "web": "source", "apps": "workspace",
    "packages": "workspace", "libs": "workspace", "crates": "workspace",
    "test": "tests", "tests": "tests", "__tests__": "tests", "spec": "tests", "specs": "tests",
    "e2e": "tests", "integration": "tests", "testing": "tests", "fixtures": "tests",
    "mocks": "tests", "docs": "docs", "doc": "docs", "documentation": "docs",
    "website": "docs", "site": "docs", "wiki": "docs", ".github": "github", ".gitlab": "ci",
    ".circleci": "ci", "ci": "ci", "scripts": "scripts", "script": "scripts",
    "tools": "scripts", "tool": "scripts", "bin": "scripts", "hack": "scripts",
    "build": "build", "dist": "build", "out": "build", "target": "build", "obj": "build",
    "examples": "examples", "example": "examples", "samples": "examples", "sample": "examples",
    "demo": "examples", "demos": "examples", "playground": "examples",
    "benchmarks": "benchmarks", "benchmark": "benchmarks", "bench": "benchmarks",
    "perf": "benchmarks", "public": "assets", "static": "assets", "assets": "assets",
    "images": "assets", "img": "assets", "media": "assets", "fonts": "assets",
    "config": "config", "configs": "config", ".config": "config", ".vscode": "config",
    ".idea": "config", ".devcontainer": "config", ".husky": "config", ".claude": "ai-config",
    ".cursor": "ai-config", "skills": "ai-config", "agents": "ai-config",
    ".claude-plugin": "ai-config", "vendor": "vendored", "third_party": "vendored",
    "node_modules": "vendored", "migrations": "database", "db": "database",
    "database": "database", "prisma": "database", "schema": "schema", "schemas": "schema",
    "proto": "schema", "templates": "templates", "locales": "i18n", "i18n": "i18n",
    "translations": "i18n", "types": "types", "typings": "types", "infra": "infrastructure",
    "deploy": "infrastructure", "deployment": "infrastructure", "k8s": "infrastructure",
    "terraform": "infrastructure", "docker": "infrastructure", "charts": "infrastructure",
    "data": "data", "datasets": "data", "notebooks": "notebooks",
}  # fmt: skip


def describe_file(
    full: str, rel: str, name: str, depth: int, size: int, *, vendored: bool, build_output: bool
) -> FileInfo:
    ext = os.path.splitext(name)[1].lower()
    language = LANGUAGE_BY_NAME.get(name) or LANGUAGE_BY_EXT.get(ext)
    if language is None and name.lower().startswith("dockerfile"):
        language = "Dockerfile"
    generated = build_output or any(r.search(rel) for r in GENERATED_FILE_RES)
    lockfile = name in LOCKFILES
    manifest_kind = MANIFEST_BY_NAME.get(name) or MANIFEST_BY_EXT.get(ext)
    if manifest_kind is None and _REQUIREMENTS_RE.match(name):
        manifest_kind = "python"
    binary = False
    loc = 0
    if ext in BINARY_EXTENSIONS:
        binary = True
    elif size and size <= MAX_TEXT_SIZE and not vendored:
        try:
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError:
            data = b""
        if looks_binary(data):
            binary = True
        else:
            loc = count_lines(data.decode("utf-8", errors="replace"))
    return FileInfo(
        path=rel,
        name=name,
        ext=ext,
        size=size,
        language=language,
        is_code=language in CODE_LANGUAGES,
        vendored=vendored,
        generated=generated,
        binary=binary,
        loc=loc,
        depth=depth,
        lockfile=lockfile,
        manifest_kind=manifest_kind,
    )


def walk_tree(root: Path, *, max_files: int) -> tuple[list[FileInfo], dict[str, Any]]:
    """Walk ``root`` without following symlinks; cap vendored directories and total files."""
    files: list[FileInfo] = []
    stats: dict[str, Any] = {
        "dirs": 0,
        "symlinks": 0,
        "truncated": False,
        "vendored_dirs_capped": [],
    }
    vendored_counts: dict[str, int] = defaultdict(int)
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str, topdown=True, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root_str)
        parts: tuple[str, ...] = ()
        if rel_dir != ".":
            parts = tuple(rel_dir.replace(os.sep, "/").split("/"))
        kept: list[str] = []
        for directory in sorted(dirnames):
            if directory in IGNORED_DIRS:
                continue
            if os.path.islink(os.path.join(dirpath, directory)):
                stats["symlinks"] += 1
                continue
            kept.append(directory)
        dirnames[:] = kept
        stats["dirs"] += len(kept)

        vendored_index = next((i for i, p in enumerate(parts) if p in VENDORED_DIRS), None)
        vendored = vendored_index is not None
        vendored_key = "/".join(parts[: (vendored_index or 0) + 1]) if vendored else ""
        if vendored and vendored_counts[vendored_key] >= MAX_VENDORED_PER_DIR:
            dirnames[:] = []
            if vendored_key not in stats["vendored_dirs_capped"]:
                stats["vendored_dirs_capped"].append(vendored_key)
            continue
        build_output = any(p in BUILD_OUTPUT_DIRS for p in parts)

        for name in sorted(filenames):
            if len(files) >= max_files:
                stats["truncated"] = True
                dirnames[:] = []
                break
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                stats["symlinks"] += 1
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = "/".join((*parts, name))
            if vendored:
                vendored_counts[vendored_key] += 1
            files.append(
                describe_file(
                    full,
                    rel,
                    name,
                    len(parts),
                    st.st_size,
                    vendored=vendored,
                    build_output=build_output,
                )
            )
    return files, stats


def mark_build_outputs(files: list[FileInfo]) -> None:
    """``bin``/``obj`` are build output in .NET repositories, ``target`` in Rust/JVM ones."""
    kinds = {f.manifest_kind for f in files if f.manifest_kind}
    outputs: set[str] = set()
    if "dotnet" in kinds:
        outputs |= {"bin", "obj"}
    if kinds & {"rust", "maven", "gradle"}:
        outputs.add("target")
    if not outputs:
        return
    for info in files:
        parts = info.path.split("/")[:-1]
        if any(part in outputs for part in parts):
            info.generated = True


def _first_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for item in value.values():
            found = _first_str(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_str(item)
            if found:
                return found
    return None


def entry_points(root: Path, files: list[FileInfo]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    by_path = {f.path: f for f in files}

    def add(kind: str, value: str, source: str) -> None:
        if len(found) < 25 and not any(e["value"] == value for e in found):
            found.append({"kind": kind, "value": value, "source": source})

    if "package.json" in by_path:
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            pkg = None
        if isinstance(pkg, dict):
            for key in ("main", "module", "browser"):
                value = pkg.get(key)
                if isinstance(value, str):
                    add(f"npm:{key}", value, "package.json")
            bins = pkg.get("bin")
            if isinstance(bins, str):
                add("npm:bin", bins, "package.json")
            elif isinstance(bins, dict):
                for name, target in list(bins.items())[:10]:
                    if isinstance(target, str):
                        add("npm:bin", f"{name} -> {target}", "package.json")
            exports = pkg.get("exports")
            main_export = _first_str(exports.get(".") if isinstance(exports, dict) else exports)
            if main_export:
                add("npm:exports", main_export, "package.json")
    if "pyproject.toml" in by_path:
        try:
            project = tomllib.loads(
                (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
            ).get("project", {})
        except (OSError, tomllib.TOMLDecodeError):
            project = {}
        for section in ("scripts", "gui-scripts"):
            scripts = project.get(section) if isinstance(project, dict) else None
            if isinstance(scripts, dict):
                for name, target in list(scripts.items())[:10]:
                    add(f"python:{section}", f"{name} -> {target}", "pyproject.toml")
    for info in files:
        if info.vendored or info.generated:
            continue
        name = info.name
        path = info.path
        if name == "__main__.py":
            add("python:__main__", path, "tree")
        elif name == "Program.cs":
            add("dotnet:Program", path, "tree")
        elif name == "main.go" and (path == "main.go" or path.startswith("cmd/")):
            add("go:main", path, "tree")
        elif path in ("src/main.rs",) or path.startswith("src/bin/"):
            add("rust:bin", path, "tree")
        elif path in (
            "src/index.ts", "src/index.js", "index.ts", "index.js", "src/main.ts",
            "src/main.tsx", "src/main.js", "src/app.ts", "server.js", "app.js", "main.py",
            "app.py", "manage.py", "cli.py", "src/cli.py",
        ):  # fmt: skip
            add("well-known", path, "tree")
    return found


def top_level_layout(files: list[FileInfo]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for info in files:
        head, _, rest = info.path.partition("/")
        is_dir = bool(rest)
        entry = groups.setdefault(
            head,
            {"path": head, "kind": "dir" if is_dir else "file", "files": 0, "loc": 0, "langs": {}},
        )
        entry["files"] += 1
        if info.counted:
            entry["loc"] += info.loc
            if info.language:
                entry["langs"][info.language] = entry["langs"].get(info.language, 0) + info.loc
    rows: list[dict[str, Any]] = []
    for entry in groups.values():
        langs: dict[str, int] = entry.pop("langs")
        main_lang = max(langs, key=lambda k: langs[k]) if langs else None
        role = ROLE_BY_NAME.get(entry["path"])
        if role is None:
            if entry["kind"] == "file":
                role = "file"
            elif main_lang in CODE_LANGUAGES:
                role = "source (guess)"
            else:
                role = "?"
        entry["language"] = main_lang
        entry["role"] = role
        rows.append(entry)
    rows.sort(key=lambda r: (r["kind"] != "dir", -r["files"], r["path"]))
    return rows


def summarize(
    root: Path, files: list[FileInfo], stats: dict[str, Any], root_entries: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    counted = [f for f in files if f.counted]
    loc_total = sum(f.loc for f in counted)
    bytes_total = sum(f.size for f in files)
    languages: dict[str, dict[str, int]] = {}
    for info in counted:
        if not info.language:
            continue
        bucket = languages.setdefault(info.language, {"files": 0, "loc": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["loc"] += info.loc
        bucket["bytes"] += info.size
    ordered = dict(sorted(languages.items(), key=lambda kv: (-kv[1]["loc"], kv[0])))
    code_langs = [name for name in ordered if name in CODE_LANGUAGES]
    primary = code_langs[0] if code_langs else (next(iter(ordered), None))

    manifests = [
        {"path": f.path, "kind": f.manifest_kind}
        for f in sorted(files, key=lambda f: (f.depth, f.path))
        if f.manifest_kind and not f.vendored and not f.generated
    ][:60]
    lockfiles = [
        {"path": f.path, "ecosystem": LOCKFILES[f.name]}
        for f in files
        if f.lockfile and not f.vendored and f.depth <= 2
    ][:20]
    largest = [
        {"path": f.path, "bytes": f.size, "loc": f.loc, "generated": f.generated}
        for f in sorted((f for f in files if not f.vendored), key=lambda f: -f.size)[:10]
    ]
    noise: dict[str, int] = defaultdict(int)
    for info in files:
        if info.vendored or info.generated:
            head = info.path.split("/")[0]
            key = head if "/" in info.path else info.name
            noise[key] += 1
    noise_rows = sorted(noise.items(), key=lambda kv: -kv[1])[:20]

    gitattributes = ""
    if ".gitattributes" in root_entries:
        try:
            gitattributes = (root / ".gitattributes").read_text(encoding="utf-8", errors="replace")
        except OSError:
            gitattributes = ""
    entries_lower = {e.lower() for e in root_entries}
    flags = {
        "has_gitignore": ".gitignore" in entries_lower,
        "has_gitattributes": ".gitattributes" in entries_lower,
        "has_gitmodules": ".gitmodules" in entries_lower,
        "has_lfs": "filter=lfs" in gitattributes,
        "has_editorconfig": ".editorconfig" in entries_lower,
    }
    data: dict[str, Any] = {
        "files": len(files),
        # Files that are actually this repository's content. `files` also counts vendored and
        # generated trees, which a fresh clone of a prey does not have, so comparing the raw
        # count against a prey's makes a host with a .venv look enormous.
        "files_counted": len(counted),
        "dirs": stats["dirs"],
        "bytes": bytes_total,
        "loc": loc_total,
        "primary_language": primary,
        "languages": dict(list(ordered.items())[:30]),
        "top_level": top_level_layout(files)[:60],
        "manifests": manifests,
        "lockfiles": lockfiles,
        "entry_points": entry_points(root, files),
        "largest_files": largest,
        "vendored_or_generated": [{"path": k, "files": v} for k, v in noise_rows],
        "binary_files": sum(1 for f in files if f.binary),
        "symlinks": stats["symlinks"],
        "truncated": stats["truncated"],
        "vendored_dirs_capped": stats["vendored_dirs_capped"],
        "flags": flags,
    }
    extra = {"files": files, "root_entries": root_entries, "languages": ordered}
    return data, extra


class InventoryMiner:
    name = "inventory"
    requires: tuple[str, ...] = ()
    json_file = "inventory.json"
    md_file = "inventory.md"

    def run(self, ctx: MineContext) -> MinerResult:
        files, stats = walk_tree(ctx.root, max_files=MAX_FILES["deep" if ctx.deep else "normal"])
        mark_build_outputs(files)
        ignored = 0
        if ctx.ignore:
            kept = [f for f in files if not is_ignored(f.path, ctx.ignore)]
            ignored = len(files) - len(kept)
            files = kept
        try:
            root_entries = sorted(
                entry.name for entry in ctx.root.iterdir() if not is_ignored(entry.name, ctx.ignore)
            )
        except OSError:
            root_entries = []
        data, extra = summarize(ctx.root, files, stats, root_entries)
        data["ignored"] = {"patterns": list(ctx.ignore), "files": ignored}
        warnings: list[str] = []
        if stats["truncated"]:
            warnings.append("file list truncated; use --depth deep for more")
        if stats["vendored_dirs_capped"]:
            warnings.append(
                "vendored directories capped: " + ", ".join(stats["vendored_dirs_capped"])
            )
        return MinerResult(
            self.name, data, doc=self._doc(ctx, data), extra=extra, warnings=warnings
        )

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"Inventory: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        summary.kv(
            [
                ("Files", f"{data['files_counted']} ({data['files']} including vendored)"),
                ("Directories", data["dirs"]),
                ("Size", f"{data['bytes'] / 1024:.0f} KB"),
                ("Lines of code (excluding vendored, generated, binary)", data["loc"]),
                ("Primary language", data["primary_language"] or "unknown"),
                ("Binary files", data["binary_files"]),
                ("Manifests", len(data["manifests"])),
                ("Lock files", ", ".join(lock["path"] for lock in data["lockfiles"]) or "none"),
                ("Git flags", ", ".join(k for k, v in data["flags"].items() if v) or "none"),
            ]
        )
        languages = doc.section("Languages", priority=1)
        total = data["loc"] or 1
        languages.table(
            ["Language", "Files", "LOC", "Share"],
            (
                [name, info["files"], info["loc"], f"{info['loc'] * 100 / total:.0f}%"]
                for name, info in data["languages"].items()
            ),
            max_rows=15,
        )
        layout = doc.section("Top-level layout", priority=2)
        layout.table(
            ["Entry", "Role", "Files", "LOC", "Main language"],
            (
                [
                    row["path"] + ("/" if row["kind"] == "dir" else ""),
                    row["role"],
                    row["files"],
                    row["loc"],
                    row["language"] or "",
                ]
                for row in data["top_level"]
            ),
            max_rows=40,
        )
        manifests = doc.section("Manifests and entry points", priority=2)
        manifests.bullets((f"{m['path']} ({m['kind']})" for m in data["manifests"]), max_items=25)
        if data["entry_points"]:
            manifests.line("Entry points:")
            manifests.bullets(
                (f"{e['kind']}: {e['value']}" for e in data["entry_points"]), max_items=15
            )
        largest = doc.section("Largest files", priority=3)
        largest.table(
            ["Path", "KB", "LOC", "Generated"],
            (
                [f["path"], f"{f['bytes'] / 1024:.0f}", f["loc"], f["generated"]]
                for f in data["largest_files"]
            ),
        )
        if data["vendored_or_generated"]:
            noise = doc.section("Vendored, generated or build output", priority=3)
            noise.bullets(
                (f"{n['path']}: {n['files']} files" for n in data["vendored_or_generated"]),
                max_items=15,
            )
        notes = doc.section("Notes", priority=4)
        note_lines = []
        if data["truncated"]:
            note_lines.append("The file list was truncated at the miner's limit.")
        if data["symlinks"]:
            note_lines.append(f"{data['symlinks']} symlinks were skipped.")
        if data["vendored_dirs_capped"]:
            note_lines.append(
                "Vendored directories capped: " + ", ".join(data["vendored_dirs_capped"])
            )
        if not note_lines:
            note_lines.append("Nothing unusual.")
        notes.bullets(note_lines)
        return doc
