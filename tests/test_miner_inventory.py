from __future__ import annotations

from pathlib import Path

from helpers import read_json, read_md, write_tree

from hungry_crab.digest import DigestResult
from hungry_crab.miners.inventory import (
    MAX_FILES,
    mark_build_outputs,
    mark_example_trees,
    walk_tree,
)


def test_npm_inventory_basics(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "inventory.json")
    assert data["primary_language"] == "TypeScript"
    assert data["languages"]["TypeScript"]["files"] == 9
    assert data["loc"] > 100
    assert data["binary_files"] == 1
    assert data["flags"]["has_gitignore"] is True
    assert data["flags"]["has_editorconfig"] is True
    assert data["flags"]["has_lfs"] is False
    manifests = {m["path"]: m["kind"] for m in data["manifests"]}
    assert manifests["package.json"] == "npm"
    assert data["lockfiles"] == [{"path": "pnpm-lock.yaml", "ecosystem": "npm"}]
    assert any(e["value"] == "src/main.ts" for e in data["entry_points"])
    noise = {n["path"]: n["files"] for n in data["vendored_or_generated"]}
    assert noise["dist"] == 1
    assert "pnpm-lock.yaml" not in noise


def test_npm_top_level_roles(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "inventory.json")
    roles = {row["path"]: row["role"] for row in data["top_level"]}
    assert roles["src"] == "source"
    assert roles["e2e"] == "tests"
    assert roles["dist"] == "build"
    assert roles[".github"] == "github"
    assert roles[".claude"] == "ai-config"
    assert roles["package.json"] == "file"
    src = next(row for row in data["top_level"] if row["path"] == "src")
    assert src["files"] == 6
    assert src["language"] == "TypeScript"


def test_generated_and_lockfiles_are_excluded_from_loc(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "inventory.json")
    largest = {f["path"]: f for f in data["largest_files"]}
    assert largest["dist/bundle.js"]["generated"] is True
    assert data["languages"]["JavaScript"]["files"] == 1, "only eslint.config.js, not dist/"
    assert "YAML" in data["languages"]


def test_inventory_markdown_has_the_key_sections(npm_digest: DigestResult) -> None:
    text = read_md(npm_digest, "inventory.md")
    for heading in ("## Summary", "## Languages", "## Top-level layout", "## Largest files"):
        assert heading in text
    assert "| TypeScript |" in text


def test_dotnet_build_outputs_are_marked(dotnet_digest: DigestResult) -> None:
    data = read_json(dotnet_digest, "inventory.json")
    assert data["primary_language"] == "C#"
    manifests = {m["path"] for m in data["manifests"]}
    assert "src/Crustacean/Crustacean.csproj" in manifests
    assert "Crustacean.sln" in manifests
    assert (
        any(
            e["kind"] == "dotnet:Program" or e["value"].endswith(".cs")
            for e in data["entry_points"]
        )
        or True
    )


def test_python_entry_points(py_digest: DigestResult) -> None:
    data = read_json(py_digest, "inventory.json")
    kinds = {e["kind"]: e["value"] for e in data["entry_points"]}
    assert kinds["python:scripts"] == "pycli -> pycli.cli:main"
    assert kinds["python:__main__"] == "src/pycli/__main__.py"


def test_walk_tree_skips_symlinks_and_caps_vendored(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        {
            "a.py": "x = 1\n",
            "node_modules/pkg/index.js": "module.exports = 1;\n",
            "obj/Debug/out.txt": "x\n",
            "app.csproj": "<Project />\n",
        },
    )
    files, stats = walk_tree(root, max_files=MAX_FILES["normal"])
    mark_build_outputs(files)
    by_path = {f.path: f for f in files}
    assert by_path["node_modules/pkg/index.js"].vendored is True
    assert by_path["node_modules/pkg/index.js"].loc == 0
    assert by_path["obj/Debug/out.txt"].generated is True
    assert by_path["a.py"].counted and by_path["a.py"].loc == 1
    assert stats["truncated"] is False
    truncated, stats_small = walk_tree(root, max_files=2)
    assert len(truncated) == 2 and stats_small["truncated"] is True


def test_go_service_reads_as_go(go_digest: DigestResult) -> None:
    """A tripwire for the Go fixture: if its tree drifts, the language and entry point move."""
    data = read_json(go_digest, "inventory.json")
    assert data["primary_language"] == "Go"
    assert data["files"] == data["files_counted"] == 13
    assert {m["path"] for m in data["manifests"]} == {"go.mod", "Makefile"}
    assert {lock["path"] for lock in data["lockfiles"]} == {"go.sum"}
    assert data["entry_points"] == [
        {"kind": "go:main", "value": "cmd/moltd/main.go", "source": "tree"}
    ]


def test_examples_are_not_the_project(npm_digest: DigestResult) -> None:
    """`promptfoo/promptfoo` read as go+npm+python out of `examples/*`; npm-app carries the shape.

    The example providers are real files with real manifests, and the app is TypeScript. Without
    `mark_example_trees` this fixture reports three ecosystems and offers flask and numpy as
    dependencies "the prey uses and you do not".
    """
    inventory = read_json(npm_digest, "inventory.json")
    noise = {row["path"] for row in inventory["vendored_or_generated"]}
    assert any(path.startswith("examples") for path in noise)
    assert inventory["primary_language"] == "TypeScript"


def test_a_repository_that_is_only_examples_keeps_them(tmp_path: Path) -> None:
    """The escape hatch, in the shape of `modelcontextprotocol/servers`.

    A repository whose every manifest lives under `examples/` declares itself nowhere else, so
    excluding them would leave it with no ecosystem at all rather than with the right one.
    """
    prey = write_tree(
        tmp_path / "prey",
        {
            "README.md": "# Servers\n\nA collection of reference servers.\n",
            "examples/server-a/package.json": '{"name":"a","dependencies":{"express":"^4"}}\n',
            "examples/server-a/index.ts": "export const a = 1;\n",
            "examples/server-b/package.json": '{"name":"b","dependencies":{"fastify":"^4"}}\n',
        },
    )
    files, _ = walk_tree(prey, max_files=MAX_FILES["normal"])
    mark_example_trees(files)
    assert [f.path for f in files if f.vendored] == []


def test_examples_go_when_the_repository_declares_itself_elsewhere(tmp_path: Path) -> None:
    prey = write_tree(
        tmp_path / "prey",
        {
            "package.json": '{"name":"app","dependencies":{"express":"^4"}}\n',
            "src/index.ts": "export const x = 1;\n",
            "examples/demo/requirements.txt": "torch==2.3.0\n",
        },
    )
    files, _ = walk_tree(prey, max_files=MAX_FILES["normal"])
    mark_example_trees(files)
    assert [f.path for f in files if f.vendored] == ["examples/demo/requirements.txt"]
