from __future__ import annotations

from helpers import read_json, read_md

from hungry_crab.digest import DigestResult
from hungry_crab.miners.architecture import _resolve_py, _resolve_ts


def test_resolvers() -> None:
    files = {"src/main.ts", "src/app.ts", "src/lib/store.ts", "src/lib/index.ts", "src/util.js"}
    assert _resolve_ts("src/main.ts", "./app", files) == "src/app.ts"
    assert _resolve_ts("src/main.ts", "./lib/store", files) == "src/lib/store.ts"
    assert _resolve_ts("src/main.ts", "./lib", files) == "src/lib/index.ts"
    assert _resolve_ts("src/lib/store.ts", "../util.js", files) == "src/util.js"
    assert _resolve_ts("src/main.ts", "vitest", files) is None
    modules = {
        "pycli": "src/pycli/__init__.py",
        "pycli.core": "src/pycli/core.py",
        "pycli.cli": "src/pycli/cli.py",
    }
    assert _resolve_py("src/pycli/cli.py", "pycli.core", modules) == "src/pycli/core.py"
    assert _resolve_py("src/pycli/cli.py", ".core", modules) == "src/pycli/core.py"
    assert _resolve_py("src/pycli/cli.py", "click", modules) is None
    assert _resolve_py("src/pycli/cli.py", "pycli.cli", modules) is None


def test_npm_architecture(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "architecture.json")
    assert data["available"] is True
    assert data["languages"] == ["JavaScript", "TypeScript"]
    hubs = {h["path"]: h for h in data["graph"]["hubs"]}
    assert hubs["src/lib/store.ts"]["imported_by"] == 3
    assert hubs["src/app.ts"]["imported_by"] == 1
    orchestrators = {o["path"]: o["imports"] for o in data["graph"]["orchestrators"]}
    assert orchestrators["src/main.ts"] == 2
    assert ["src/lib/math.test.ts", "src/lib/math.ts"] in data["edges"]
    external = {e["name"] for e in data["graph"]["external_top"]}
    assert {"vitest", "date-fns", "@playwright/test", "fast-check"} <= external
    surface = {s["path"]: s["names"] for s in data["public_surface"]}
    assert surface["src/lib/store.ts"] == ["Listener", "Store", "createStore"]
    assert data["totals"]["classes"] == 0 and data["totals"]["functions"] >= 4
    text = read_md(npm_digest, "architecture.md")
    assert "## Hubs" in text and "src/lib/store.ts" in text


def test_python_and_dotnet_architecture(
    py_digest: DigestResult, dotnet_digest: DigestResult
) -> None:
    py = read_json(py_digest, "architecture.json")
    hubs = {h["path"]: h["imported_by"] for h in py["graph"]["hubs"]}
    assert hubs["src/pycli/core.py"] == 2
    assert hubs["src/pycli/cli.py"] == 2
    assert py["totals"]["functions"] >= 5
    surface = {s["path"]: s["names"] for s in py["public_surface"]}
    assert surface["src/pycli/core.py"] == ["count_pools"]
    external = {e["name"] for e in py["graph"]["external_top"]}
    assert {"click", "hypothesis", "pytest"} <= external
    dotnet = read_json(dotnet_digest, "architecture.json")
    assert dotnet["totals"]["classes"] >= 6
    names = {n for s in dotnet["public_surface"] for n in s["names"]}
    assert {"Claw", "Shell"} <= names
    dir_edges = {(e["from"], e["to"]) for e in dotnet["graph"]["dir_edges"]}
    assert ("tests/Crustacean.Tests", "src/Crustacean") in dir_edges
    assert dotnet["graph"]["dir_cycles"] == []
