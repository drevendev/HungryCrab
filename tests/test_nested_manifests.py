"""A deeply nested prey manifest is an unparseable file, not a failed miner (#330).

`json`, `tomllib` and `yaml` recurse once per nesting level, so a few kilobytes of `[` raise
`RecursionError`, which is not a syntax error. Each parse site must treat it like one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from fixture_builder import git
from helpers import copy_repo, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, incomplete_miners, run_digest
from hungry_crab.licensing.detect import manifest_license
from hungry_crab.miners.ai_config import AiConfigMiner
from hungry_crab.miners.base import MineContext
from hungry_crab.miners.ci import CiMiner, YamlExpansionError, load_prey_yaml, parse_workflow
from hungry_crab.miners.inventory import InventoryMiner
from hungry_crab.miners.scope import ProjectDepsMiner

DEEP_JSON = "[" * 50_000
DEEP_JSON_OBJECT = '{"a":' * 50_000
DEEP_TOML = "a = " + "[" * 50_000
DEEP_YAML = "a: " + "[" * 5_000


def _context(root: Path) -> MineContext:
    ctx = MineContext(root=root, sha="a" * 40, ref="fixture", label="fixture")
    ctx.results["inventory"] = InventoryMiner().run(ctx)
    return ctx


def test_manifest_license_treats_deep_nesting_as_unparseable() -> None:
    for name, text in (
        ("package.json", DEEP_JSON),
        ("composer.json", DEEP_JSON_OBJECT),
        ("pyproject.toml", DEEP_TOML),
        ("Cargo.toml", DEEP_TOML),
    ):
        assert manifest_license(name, text) == (None, "unparseable manifest"), name


def test_deps_miner_warns_about_deep_manifests_and_reads_the_rest(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            # Root package.json and pyproject.toml are also read by the inventory; keep them
            # nested so this test pins the deps miner alone.
            "web/package.json": DEEP_JSON,
            "py/pyproject.toml": DEEP_TOML,
            "Cargo.toml": DEEP_TOML,
            "composer.json": DEEP_JSON,
            "go.mod": "module example.com/a\n\nrequire example.com/b v1.0.0\n",
        },
    )
    result = ProjectDepsMiner().run(_context(tmp_path))

    # How deep nesting fails depends on the interpreter: RecursionError on most, but the C JSON
    # scanner of some builds reports a JSONDecodeError first. Either is an unparseable file.
    for path in ("web/package.json", "py/pyproject.toml", "Cargo.toml", "composer.json"):
        assert any(w.startswith(f"could not parse {path}: ") for w in result.warnings), path
    assert [package["name"] for package in result.data["packages"]] == ["example.com/b"]


def test_ai_config_marks_deep_json_as_a_parse_error(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            ".claude/settings.json": DEEP_JSON_OBJECT,
            ".mcp.json": DEEP_JSON,
            ".codex-plugin/plugin.json": DEEP_JSON_OBJECT,
        },
    )
    result = AiConfigMiner().run(_context(tmp_path))

    assert result.data["settings"] == [{"path": ".claude/settings.json", "parse_error": True}]
    assert result.data["mcp"] == [{"path": ".mcp.json", "servers": [], "transports": []}]
    assert result.data["plugin"][0]["parse_error"] is True


def test_ci_miner_warns_about_a_deep_workflow_and_keeps_the_others(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            ".github/workflows/deep.yml": "on: push\njobs: " + "[" * 5_000,
            ".github/workflows/ok.yml": "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n",
            ".github/dependabot.yml": DEEP_YAML,
            "renovate.json": DEEP_JSON,
        },
    )
    result = CiMiner().run(_context(tmp_path))

    assert result.warnings == [".github/workflows/deep.yml: YAML error (RecursionError)"]
    assert [workflow["path"] for workflow in result.data["workflows"]] == [
        ".github/workflows/ok.yml"
    ]
    assert result.data["dependabot"] is None
    assert result.data["renovate"] == {"path": "renovate.json", "extends": []}


def _anchor_bomb(levels: int) -> str:
    lines = ["x-a0: &a0 [" + ", ".join(['"xxxxxxxxxx"'] * 9) + "]"]
    for level in range(1, levels):
        lines.append(f"x-a{level}: &a{level} [" + ", ".join([f"*a{level - 1}"] * 9) + "]")
    return "\n".join(lines) + f"\non:\n  push:\n    branches: *a{levels - 1}\n"


def test_workflow_anchors_that_reuse_steps_still_parse() -> None:
    parsed = parse_workflow(
        "on: push\njobs:\n"
        "  a:\n    runs-on: ubuntu-latest\n    steps: &steps\n"
        "      - uses: actions/checkout@v4\n      - run: pytest\n"
        "  b:\n    runs-on: windows-latest\n    steps: *steps\n",
        ".github/workflows/ci.yml",
    )
    assert parsed is not None
    assert [job["steps"] for job in parsed["jobs"]] == [2, 2]
    assert parsed["tools"] == ["pytest"]


def test_workflow_anchors_that_expand_or_cycle_are_refused(tmp_path: Path) -> None:
    """529 bytes of anchors made a 210 MB ci.json; `&a [*a]` crashed the JSON writer."""
    write_tree(
        tmp_path,
        {
            ".github/workflows/bomb.yml": _anchor_bomb(9),
            ".github/workflows/cycle.yml": "on:\n  push:\n    branches: &a [*a]\n",
            ".github/workflows/ok.yml": "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n",
            ".github/dependabot.yml": "version: 2\nupdates:\n  - directory: &d [*d]\n",
        },
    )
    started = time.perf_counter()
    result = CiMiner().run(_context(tmp_path))
    assert time.perf_counter() - started < 10

    assert result.warnings == [
        ".github/workflows/bomb.yml: YAML error (YamlExpansionError)",
        ".github/workflows/cycle.yml: YAML error (YamlExpansionError)",
    ]
    assert [workflow["path"] for workflow in result.data["workflows"]] == [
        ".github/workflows/ok.yml"
    ]
    assert result.data["dependabot"] is None
    assert len(json.dumps(result.data)) < 10_000


def test_load_prey_yaml_charges_long_strings_behind_aliases() -> None:
    many = "s: &s " + "x" * 2_000 + "\nl: [" + ", ".join(["*s"] * 600) + "]\n"
    with pytest.raises(YamlExpansionError):
        load_prey_yaml(many)
    assert load_prey_yaml("s: &s abc\nl: [*s, *s]\n") == {"s": "abc", "l": ["abc", "abc"]}


def _merge_bomb(levels: int, fan_out: int = 10) -> str:
    lines = ["a0: &a0 {k0: 0, k1: 1, k2: 2, k3: 3, k4: 4, k5: 5, k6: 6, k7: 7, k8: 8, k9: 9}"]
    for level in range(1, levels + 1):
        refs = ", ".join([f"*a{level - 1}"] * fan_out)
        lines.append(f"a{level}: &a{level} {{<<: [{refs}]}}")
    return "\n".join(lines) + "\non: push\njobs: {}\n"


@pytest.mark.parametrize(
    "text",
    [
        # 552 bytes: PyYAML copied 10^8 pairs while building it, 78 s, and the result was small.
        _merge_bomb(7),
        _merge_bomb(12),
        # A single merge key whose source is already a fan-out of aliases.
        _merge_bomb(4) + "x: &x {<<: *a4, y: 1}\nz: [" + ", ".join(["*x"] * 200) + "]\n",
        # Nested inline merges: each level copies every pair of the level below.
        "x: "
        + "{<<: " * 200
        + "{"
        + ", ".join(f"k{i}: {i}" for i in range(5_000))
        + "}"
        + "}" * 200
        + "\n",
        # A merge that refers to the mapping it sits in.
        "a: &a {<<: *a, k: 1}\n",
    ],
    ids=["fan-7", "fan-12", "fan-then-alias", "inline-chain", "self-merge"],
)
def test_merge_keys_are_charged_before_the_document_is_built(text: str) -> None:
    started = time.perf_counter()
    with pytest.raises(YamlExpansionError):
        load_prey_yaml(text)
    assert time.perf_counter() - started < 1


def test_a_merge_key_bomb_is_a_workflow_warning(tmp_path: Path) -> None:
    write_tree(tmp_path, {".github/workflows/merge.yml": _merge_bomb(7)})
    started = time.perf_counter()
    result = CiMiner().run(_context(tmp_path))
    assert time.perf_counter() - started < 5
    assert result.warnings == [".github/workflows/merge.yml: YAML error (YamlExpansionError)"]


def test_workflow_merge_keys_that_share_job_defaults_still_parse() -> None:
    parsed = parse_workflow(
        "x-defaults: &defaults\n  runs-on: ubuntu-latest\n  timeout-minutes: 10\n"
        "on: push\njobs:\n"
        "  a:\n    <<: *defaults\n    steps: [{run: pytest}]\n"
        "  b:\n    <<: [*defaults]\n    runs-on: windows-latest\n    steps: [{run: pytest}]\n",
        ".github/workflows/ci.yml",
    )
    assert parsed is not None
    assert [(job["id"], job["runs_on"], job["timeout_minutes"]) for job in parsed["jobs"]] == [
        ("a", "ubuntu-latest", 10),
        ("b", "windows-latest", 10),
    ]
    assert load_prey_yaml(_merge_bomb(3)) is not None


def test_a_deep_manifest_fails_no_miner_in_a_digest(npm_app: Path, tmp_path: Path) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    write_tree(
        work,
        {
            "composer.json": DEEP_JSON,
            "Cargo.toml": DEEP_TOML,
            ".claude/settings.json": DEEP_JSON_OBJECT,
            ".github/workflows/deep.yml": "on: push\njobs: " + "[" * 5_000,
        },
    )
    git(work, "add", "-A")
    git(work, "commit", "-m", "test: add deeply nested manifests")

    result = run_digest(
        Target(path=work),
        DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache"),
    )

    assert incomplete_miners(result.manifest) == []
    warnings = result.manifest["warnings"]
    assert any(w.startswith("deps: could not parse composer.json: ") for w in warnings)
    assert "ci: .github/workflows/deep.yml: YAML error (RecursionError)" in warnings


def test_a_deep_root_manifest_fails_neither_inventory_nor_testing(
    npm_app: Path, tmp_path: Path
) -> None:
    """Inventory is the root miner: its failure would block every other miner."""
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    write_tree(work, {"package.json": DEEP_JSON, "pyproject.toml": DEEP_TOML})
    git(work, "add", "-A")
    git(work, "commit", "-m", "test: deep root manifests")

    result = run_digest(
        Target(path=work),
        DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache"),
    )

    assert incomplete_miners(result.manifest) == []


def test_a_recursion_error_while_parsing_json_is_an_unparseable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the catch itself, whatever the interpreter raises for real deep nesting."""
    from hungry_crab.miners import deps

    def deep(_: str) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    write_tree(tmp_path, {"web/package.json": "{}", "go.mod": "module example.com/a\n"})
    monkeypatch.setattr(deps.json, "loads", deep)
    result = ProjectDepsMiner().run(_context(tmp_path))
    assert "could not parse web/package.json: RecursionError" in result.warnings
