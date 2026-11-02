"""End-to-end evidence/runtime controls for supervised discovery (#194, #449, #451)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fixture_builder import git
from helpers import write_tree

from hungry_crab.cache import Slug, Target
from hungry_crab.compare import CompareOptions, CompareResult, compare_digests, compare_for_maw
from hungry_crab.compare.candidates import Side, _workflow_has, tool_candidates
from hungry_crab.digest import DigestOptions, DigestResult, run_digest
from hungry_crab.errors import CrabError
from hungry_crab.miners.ci import parse_workflow

MIT = (Path(__file__).parents[1] / "LICENSE").read_text(encoding="utf-8")


def repository(root: Path, files: dict[str, str]) -> Path:
    write_tree(root, {"LICENSE": MIT, **files})
    git(root, "init", "--quiet")
    git(root, "add", ".")
    git(
        root,
        "commit",
        "--quiet",
        "-m",
        "test: synthetic source",
        author="Fixture <fixture@example.invalid>",
    )
    return root


def digest(root: Path, output: Path) -> DigestResult:
    return run_digest(Target(path=root), DigestOptions(out=output, maw_license="MIT"))


def workflow(command: str) -> str:
    return (
        "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        f"      - run: {command}\n"
    )


def test_unittest_donor_has_exact_workflow_evidence(tmp_path: Path) -> None:
    prey = repository(
        tmp_path / "prey",
        {
            "app.py": "print('inert')\n",
            ".github/workflows/test.yml": workflow("python -m unittest discover -s tests -v"),
        },
    )
    maw = repository(
        tmp_path / "maw",
        {
            "app.py": "print('inert')\n",
            "tests/test_app.py": "import unittest\n",
            ".github/workflows/build.yml": workflow("python --version"),
        },
    )
    result = compare_digests(
        digest(prey, tmp_path / "pd").out_dir, digest(maw, tmp_path / "md").out_dir
    )
    card = next(c for c in result.candidates if c.key == "ci.runs-tests")
    assert result.prey.trait("ci_runs_tests") is True
    assert [e.path for e in card.evidence] == [".github/workflows/test.yml"]


def test_root_package_scripts_share_lint_classification(npm_digest: DigestResult) -> None:
    side = Side.load(npm_digest.out_dir)
    assert side.trait("ci_runs_lint") is True
    assert any(_workflow_has(w, "lint") for w in side.ci["workflows"])


@pytest.mark.parametrize("command", ["npm run lint", "pnpm lint", "yarn lint", "bun run lint"])
def test_bounded_script_resolution_and_cycles(command: str) -> None:
    parsed = parse_workflow(
        workflow(command),
        "workflow.yml",
        scripts={"lint": "pnpm run check", "check": "eslint . && tsc --noEmit"},
    )
    assert parsed is not None and {"eslint", "tsc"} <= set(parsed["tools"])
    assert _workflow_has(parsed, "lint")
    cyclic = parse_workflow(workflow(command), "workflow.yml", scripts={"lint": "pnpm lint"})
    assert cyclic is not None and cyclic["unresolved_scripts"]
    assert not _workflow_has(cyclic, "lint")


@pytest.mark.parametrize("command", ["make test", "just test", "task test"])
def test_named_root_test_targets(command: str) -> None:
    parsed = parse_workflow(workflow(command), "workflow.yml")
    assert parsed is not None and _workflow_has(parsed, "tests")


@pytest.mark.parametrize(
    "command",
    [
        "echo python -m unittest",
        "python -m unittest.mock",
        "python -c 'import unittest'",
        "echo pnpm lint",
    ],
)
def test_inert_commands_do_not_claim_test_or_script_evidence(command: str) -> None:
    parsed = parse_workflow(workflow(command), "workflow.yml", scripts={"lint": "eslint ."})
    assert (
        parsed is not None
        and not _workflow_has(parsed, "tests")
        and not _workflow_has(parsed, "lint")
    )


@pytest.mark.parametrize(
    "source,runtime",
    [
        ({"app.py": "import sys\nprint(sys.version)\n"}, {"python"}),
        ({"README.md": "Python is mentioned here."}, set()),
        (
            {
                "vendor/app.py": "print('foreign')\n",
                "fixtures/app.py": "print('fixture')\n",
                "README.md": "docs",
            },
            set(),
        ),
        ({"docs/example.py": "print('documentation')\n"}, set()),
        ({"app.js": "console.log('own');\n"}, {"npm"}),
        ({"unknown.txt": "unknown"}, set()),
    ],
)
def test_manifest_free_runtime_preserves_empty_packages(
    tmp_path: Path, source: dict[str, str], runtime: set[str]
) -> None:
    result = digest(repository(tmp_path / "maw", source), tmp_path / "digest")
    side = Side.load(result.out_dir)
    assert side.deps["package_count"] == 0 and side.ecosystems == set()
    assert side.compatible_ecosystems == runtime
    donor = Side(
        label="donor",
        sha="f" * 40,
        url=None,
        root=None,
        traits={"ecosystems": ["python"], "type_checkers": ["mypy"]},
    )
    keys = {card.key for card in tool_candidates(donor, side)}
    assert ("tooling.type_checker.mypy" in keys) == ("python" in runtime)


def test_portable_file_inventory_ignores_recorded_runner_root(tmp_path: Path) -> None:
    prey = repository(
        tmp_path / "prey",
        {
            "app.py": "pass\n",
            ".github/dependabot.yml": "version: 2\nupdates: []\n",
            "other/LICENSE": "SPDX-License-Identifier: GPL-3.0-only\n",
        },
    )
    maw = repository(tmp_path / "maw", {"app.py": "pass\n"})
    pd, md = digest(prey, tmp_path / "pd"), digest(maw, tmp_path / "md")
    before = compare_digests(pd.out_dir, md.out_dir, options=CompareOptions(maw_license="MIT"))
    relocated = tmp_path / "relocated"
    shutil.copytree(pd.out_dir, relocated)
    manifest = json.loads((relocated / "manifest.json").read_text(encoding="utf-8"))
    manifest["prey"]["root"] = "/unavailable/linux/worktree"
    (relocated / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    after = compare_digests(relocated, md.out_dir, options=CompareOptions(maw_license="MIT"))

    def facts(result: CompareResult) -> list[tuple[str, str, list[str]]]:
        return [(c.id, c.license_mode, [e.path for e in c.evidence]) for c in result.candidates]

    assert facts(before) == facts(after)
    dependabot = next(c for c in after.candidates if c.key == "tooling.dependabot")
    assert dependabot.license_mode == "COPY"
    assert after.prey.find_files(".github/dependabot*") == [".github/dependabot.yml"]
    assert after.prey.find_files(".github/absent*") == []
    assert after.prey.evidence_capability == "inventory"
    # A coincidentally existing checkout cannot replace persisted file evidence.
    manifest["prey"]["root"] = str(maw)
    (relocated / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert Side.load(relocated).root is None
    assert Side.load(relocated).find_files(".github/dependabot*") == [".github/dependabot.yml"]


def test_explicit_public_artifact_comparison_uses_only_git_objects(tmp_path: Path) -> None:
    prey = repository(
        tmp_path / "donor",
        {"app.py": "pass\n", ".github/dependabot.yml": "version: 2\nupdates: []\n"},
    )
    pd = digest(prey, tmp_path / "pd")
    manifest = pd.manifest
    manifest["prey"].update(
        {
            "root": "/unavailable/linux/root",
            "label": "example/donor",
            "url": "https://github.com/example/donor",
        }
    )
    (pd.out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (prey / ".github/dependabot.yml").unlink()
    maw = repository(tmp_path / "maw", {"app.py": "pass\n"})
    result, _, _, _ = compare_for_maw(
        Target(slug=Slug.parse("example/donor")),
        maw,
        prey_digest_dir=pd.out_dir,
        prey_objects=prey,
        digest_options=DigestOptions(cache_root=tmp_path / "cache"),
        record_ledger=False,
    )
    assert result.prey.find_files(".github/dependabot*") == [".github/dependabot.yml"]
    assert result.prey.evidence_capability == "git-snapshot"
    with pytest.raises(CrabError, match="identity"):
        compare_for_maw(
            Target(slug=Slug.parse("example/other")),
            maw,
            prey_digest_dir=pd.out_dir,
            record_ledger=False,
        )


def test_snapshot_preserves_retained_supporting_evidence_and_rejects_forged_paths(
    tmp_path: Path,
) -> None:
    prey = repository(
        tmp_path / "donor",
        {
            "app.py": "pass\n",
            "testdata/check.yml": "jobs: {}\n",
            "vendor/blocked.yml": "jobs: {}\n",
            "build/blocked.yml": "jobs: {}\n",
        },
    )
    pd = digest(prey, tmp_path / "pd")
    inventory_path = pd.out_dir / "inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert "testdata/check.yml" in inventory["evidence_files"]
    original = Side.load(pd.out_dir)
    relocated = Side.load(pd.out_dir, objects=prey)
    assert relocated.inventory["evidence_files"] == original.inventory["evidence_files"]
    assert relocated.traits == original.traits
    assert relocated.find_files("testdata/*.yml") == ["testdata/check.yml"]
    original_paths = list(inventory["evidence_files"])
    for path in ("testdata/absent.yml", "vendor/blocked.yml", "build/blocked.yml"):
        inventory["evidence_files"] = [*original_paths, path]
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        with pytest.raises(CrabError, match="inconsistent"):
            Side.load(pd.out_dir, objects=prey)
    inventory["evidence_files"] = original_paths
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    manifest_path = pd.out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ignore"] = ["testdata/check.yml"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CrabError, match="inconsistent"):
        Side.load(pd.out_dir, objects=prey)
    manifest["ignore"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    inventory.pop("evidence_files")
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    assert Side.load(pd.out_dir, objects=prey).find_files("testdata/*.yml") == []


def test_partial_and_unavailable_inventories_remain_explicit() -> None:
    side = Side(label="old", sha="a" * 40, url=None, root=None)
    assert side.evidence_capability == "unavailable"
    side.inventory = {"evidence_files": [".github/dependabot.yml"], "coverage": {"healthy": False}}
    assert side.evidence_capability == "partial-inventory"


def test_recursive_portable_evidence_glob_includes_root_and_is_case_sensitive() -> None:
    side = Side(label="public", sha="a" * 40, url=None, root=None)
    side.inventory = {
        "evidence_files": ["CODEOWNERS", ".github/CODEOWNERS", "a/b/CODEOWNERS", "codeowners"]
    }
    assert side.find_files("**/CODEOWNERS", limit=10) == [
        ".github/CODEOWNERS",
        "CODEOWNERS",
        "a/b/CODEOWNERS",
    ]
