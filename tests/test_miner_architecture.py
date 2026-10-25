from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from helpers import read_json, read_md, write_tree

from hungry_crab.compare.candidates import Side, architecture_candidates
from hungry_crab.digest import DigestResult
from hungry_crab.miners.architecture import ArchitectureMiner, _resolve_py, _resolve_ts
from hungry_crab.miners.base import FileInfo, MineContext, MinerResult


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


_SEEDED_DIGEST = """
import json, sys
from hungry_crab.cli import main
main(["-q", "digest", sys.argv[1], "--out", sys.argv[2], "--miners", "architecture"])
data = json.load(open(sys.argv[2] + "/architecture.json", encoding="utf-8"))
print(json.dumps(data["graph"], sort_keys=True))
"""


def test_the_import_graph_does_not_depend_on_the_hash_seed(tmp_path: Path) -> None:
    """Tied in-degrees used to follow set order, which follows PYTHONHASHSEED."""
    prey = tmp_path / "prey"
    files = {f"pkg/hub{i}.py": "X = 1\n" for i in range(12)}
    for j in range(6):
        imports = "".join(f"from pkg.hub{i} import X\n" for i in range(12))
        files[f"app/user{j}.py"] = imports + "import os\nimport sys\n"
    files["pkg/__init__.py"] = ""
    files["app/__init__.py"] = ""
    write_tree(prey, files)
    graphs = set()
    for seed in ("0", "1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", _SEEDED_DIGEST, str(prey), str(tmp_path / f"out-{seed}")],
            capture_output=True, text=True, encoding="utf-8", env=env, check=True,
        )  # fmt: skip
        graphs.add(result.stdout.strip().splitlines()[-1])
    assert len(graphs) == 1
    hubs = json.loads(graphs.pop())["hubs"]
    assert [hub["path"] for hub in hubs] == sorted(hub["path"] for hub in hubs)


def test_architecture_hubs_disclose_sampling_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late hub beats the sampled hub, but cannot appear in the truncated graph."""

    def file_info(name: str) -> FileInfo:
        return FileInfo(
            path=f"pkg/{name}.py",
            name=f"{name}.py",
            ext=".py",
            size=40,
            language="Python",
            is_code=True,
            vendored=False,
            generated=False,
            binary=False,
            loc=1,
            depth=2,
            lockfile=False,
            manifest_kind=None,
        )

    names = [f"n{i:04d}" for i in range(3000)] + ["z_late"]
    files = [file_info(name) for name in names]
    ctx = MineContext(root=tmp_path, sha="deadbeef", ref="HEAD", label="synthetic")
    ctx.results["inventory"] = MinerResult("inventory", {}, extra={"files": files})

    def synthetic_read(rel: str, *, limit: int = 512_000) -> str:
        if rel in {f"pkg/n{i:04d}.py" for i in range(1, 4)}:
            return "from pkg.n0000 import X\n"
        if rel in {f"pkg/n{i:04d}.py" for i in range(4, 24)}:
            return "from pkg.z_late import X\n"
        return "X = 1\n"

    monkeypatch.setattr(ctx, "read", synthetic_read)
    miner = ArchitectureMiner()
    normal = miner.run(ctx)
    assert normal.data["files_scanned"] == 3000
    assert normal.data["files_eligible"] == 3001
    assert normal.data["truncated"] is True
    assert normal.data["graph"]["hubs"][0]["path"] == "pkg/n0000.py"
    assert all(h["path"] != "pkg/z_late.py" for h in normal.data["graph"]["hubs"])

    maw = Side(label="maw", sha="deadbeef", url=None, root=None)
    sampled = Side(label="prey", sha="deadbeef", url=None, root=None, architecture=normal.data)
    card = architecture_candidates(sampled, maw)[0]
    assert "sampled import-graph hubs" in card.what
    assert "3000/3001 eligible code files" in card.what
    assert "incomplete coverage" in card.what

    ctx.depth = "deep"
    complete = miner.run(ctx)
    assert complete.data["truncated"] is False
    assert complete.data["files_eligible"] == complete.data["files_scanned"] == 3001
    assert complete.data["graph"]["hubs"][0]["path"] == "pkg/z_late.py"
    full = Side(label="prey", sha="deadbeef", url=None, root=None, architecture=complete.data)
    assert architecture_candidates(full, maw)[0].what.startswith("import-graph hubs: ")

    legacy = dict(normal.data)
    del legacy["files_eligible"]
    old = Side(label="prey", sha="deadbeef", url=None, root=None, architecture=legacy)
    assert "eligible file count unknown" in architecture_candidates(old, maw)[0].what


def _synthetic_architecture_json(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    """Exercise the actual digest output, not only isolated regexes."""
    prey, output = tmp_path / "prey", tmp_path / "digest"
    write_tree(prey, files)
    subprocess.run(
        [sys.executable, "-c", _SEEDED_DIGEST, str(prey), str(output)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads((output / "architecture.json").read_text(encoding="utf-8"))


def test_ts_architecture_excludes_inert_symbols_and_imports(tmp_path: Path) -> None:
    source = (
        "/*\nexport function phantom() {}\nimport { ghost } from './ghost';\n*/\n"
        "// require('./ghost');\n"
        "const quoted = \"require('./ghost')\";\n"
        "const template = \x60export function hidden() {}\nimport './ghost';\x60;\n"
        "export function actual() {}\n"
        "import { value } from './real';\n"
        "const module = require('./real');\n"
    )
    data = _synthetic_architecture_json(
        tmp_path,
        {
            "src/index.ts": source,
            "src/ghost.ts": "export const ghost = 1;\n",
            "src/real.ts": "export const value = 1;\n",
        },
    )
    assert data["totals"]["functions"] == 1
    assert data["edges"] == [["src/index.ts", "src/real.ts"]]
    assert data["graph"]["hubs"] == [{"path": "src/real.ts", "imported_by": 1, "imports": 0}]
    assert data["public_surface"] == [{"path": "src/index.ts", "names": ["actual"]}]
    assert data["lexical_uncertain_files"] == 0


def test_python_architecture_excludes_docstring_symbols_and_imports(tmp_path: Path) -> None:
    source = (
        '"""\ndef phantom():\n    pass\nclass Ghost:\n    pass\n'
        "from pkg.ghost import VALUE\n__all__ = ['ghost']\n"
        '"""\n'
        "# import pkg.ghost\n"
        "text = 'from pkg.ghost import VALUE'\n"
        "formatted = f'''\ndef formatted_phantom():\n    pass\n"
        "import pkg.ghost\n'''\n"
        '__all__ = ["actual"]\n'
        "class ActualClass:\n    pass\n"
        "def actual():\n    pass\n"
        "from pkg.real import VALUE\n"
    )
    data = _synthetic_architecture_json(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/ghost.py": "VALUE = 1\n",
            "pkg/real.py": "VALUE = 1\n",
            "pkg/main.py": source,
        },
    )
    assert data["totals"]["functions"] == 1
    assert data["totals"]["classes"] == 1
    assert data["edges"] == [["pkg/main.py", "pkg/real.py"]]
    assert data["graph"]["hubs"] == [{"path": "pkg/real.py", "imported_by": 1, "imports": 0}]
    main = next(row for row in data["top_files_by_symbols"] if row["path"] == "pkg/main.py")
    assert main["public"] == 1
    assert data["lexical_uncertain_files"] == 0


def test_architecture_marks_unsupported_template_expressions(tmp_path: Path) -> None:
    data = _synthetic_architecture_json(
        tmp_path,
        {
            "src/index.ts": ("const line = \x60hello ${name}\x60;\nexport function real() {}\n"),
        },
    )
    assert data["lexical_uncertain_files"] == 1
    assert data["totals"]["functions"] == 1


def test_nested_template_text_cannot_escape_the_lexical_mask(tmp_path: Path) -> None:
    data = _synthetic_architecture_json(
        tmp_path,
        {
            "src/index.ts": (
                "const line = `outer ${`inner\nexport function phantom() {}\n"
                "import './ghost';\n`}`;\nexport function actual() {}\nimport './real';\n"
            ),
            "src/ghost.ts": "export const ghost = 1;\n",
            "src/real.ts": "export const real = 1;\n",
        },
    )
    assert data["totals"]["functions"] == 1
    assert data["edges"] == [["src/index.ts", "src/real.ts"]]
    assert data["lexical_uncertain_files"] == 1


def test_python_template_strings_are_inert_on_supported_interpreters(tmp_path: Path) -> None:
    data = _synthetic_architecture_json(
        tmp_path,
        {
            "app.py": "text = t'''\ndef phantom():\n    pass\n'''\ndef actual():\n    pass\n",
        },
    )
    assert data["totals"]["functions"] == 1
