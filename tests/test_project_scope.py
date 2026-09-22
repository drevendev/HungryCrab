from __future__ import annotations

from pathlib import Path

from hungry_crab.miners.base import MineContext, MinerResult
from hungry_crab.miners.inventory import InventoryMiner
from hungry_crab.miners.scope import ProjectDepsMiner, ProjectTestingMiner


def _context(root: Path) -> MineContext:
    return MineContext(root=root, sha="a" * 40, ref="fixture", label="fixture")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_auxiliary_tooling_stays_visible_without_becoming_project_stack(tmp_path: Path) -> None:
    _write(tmp_path, "Gemfile", 'source "https://rubygems.org"\n')
    _write(
        tmp_path,
        "tools/grammars/go.mod",
        "module example.com/grammar\n\ngo 1.22\n\nrequire github.com/stretchr/testify v1.9.0\n",
    )
    _write(tmp_path, "tools/grammars/go.sum", "")
    _write(
        tmp_path,
        "tools/grammars/helper_test.go",
        'package grammar\n\nimport "testing"\n\nfunc TestHelper(t *testing.T) {}\n',
    )
    _write(
        tmp_path,
        "script/regex-compatibility/rust/Cargo.toml",
        '[package]\nname = "regex-helper"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[dev-dependencies]\nproptest = "1"\n',
    )
    _write(tmp_path, "script/regex-compatibility/rust/Cargo.lock", "")
    _write(tmp_path, "test/linguist_test.rb", "puts :ok\n")

    ctx = _context(tmp_path)
    inventory = InventoryMiner().run(ctx)
    ctx.results["inventory"] = inventory
    deps = ProjectDepsMiner().run(ctx)
    ctx.results["deps"] = deps
    ctx.results["ci"] = MinerResult("ci", {"workflows": []})
    testing = ProjectTestingMiner().run(ctx)

    assert deps.data["ecosystems"] == ["ruby"]
    assert deps.data["packages"] == []
    assert deps.extra["names"] == {"ruby": []}
    assert {row["ecosystem"] for row in deps.data["auxiliary_manifests"]} == {"go", "rust"}
    assert {row["ecosystem"] for row in deps.data["auxiliary_packages"]} == {"go", "rust"}
    assert {row["ecosystem"] for row in deps.data["auxiliary_lockfiles"]} == {"go", "rust"}
    assert all(row["role"] == "auxiliary" for row in deps.data["auxiliary_manifests"])
    assert "testing" not in testing.data["frameworks"]
    assert testing.data["has_tests"] is True


def test_tools_only_repository_keeps_its_manifest_as_project_evidence(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tools/worker/go.mod",
        "module example.com/worker\n\ngo 1.22\n\nrequire github.com/stretchr/testify v1.9.0\n",
    )
    _write(tmp_path, "tools/worker/worker_test.go", "package worker\n")

    ctx = _context(tmp_path)
    inventory = InventoryMiner().run(ctx)
    ctx.results["inventory"] = inventory
    deps = ProjectDepsMiner().run(ctx)
    ctx.results["deps"] = deps
    ctx.results["ci"] = MinerResult("ci", {"workflows": []})
    testing = ProjectTestingMiner().run(ctx)

    assert deps.data["ecosystems"] == ["go"]
    assert deps.data["auxiliary_manifests"] == []
    assert all(row["role"] == "project" for row in deps.data["manifests"])
    assert testing.data["frameworks"]["testing"] == "unit"
