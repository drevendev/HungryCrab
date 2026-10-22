"""A deeply nested prey manifest is an unparseable file, not a failed miner (#330).

`json`, `tomllib` and `yaml` recurse once per nesting level, so a few kilobytes of `[` raise
`RecursionError`, which is not a syntax error. Each parse site must treat it like one.
"""

from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW
from fixture_builder import git
from helpers import copy_repo, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, incomplete_miners, run_digest
from hungry_crab.licensing.detect import manifest_license
from hungry_crab.miners.ai_config import AiConfigMiner
from hungry_crab.miners.base import MineContext
from hungry_crab.miners.ci import CiMiner
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

    for path in ("web/package.json", "py/pyproject.toml", "Cargo.toml", "composer.json"):
        assert f"could not parse {path}: RecursionError" in result.warnings
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
    assert "deps: could not parse composer.json: RecursionError" in warnings
    assert "ci: .github/workflows/deep.yml: YAML error (RecursionError)" in warnings
