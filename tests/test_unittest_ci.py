from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import read_json

from hungry_crab.cache import Target
from hungry_crab.compare import compare_digests
from hungry_crab.digest import DigestOptions, DigestResult, run_digest
from hungry_crab.miners.ci import parse_workflow


@pytest.mark.parametrize(
    "command",
    [
        "python -m unittest discover -s tests -v",
        "python3 -m unittest",
        "python3.14 -m unittest tests.test_app",
        "python.exe -m unittest discover",
        "uv run python -m unittest discover",
        "python --version && python -m unittest discover",
    ],
)
def test_unittest_invocations_are_ci_test_tools(command: str) -> None:
    document = (
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: " + command + "\n"
    )
    parsed = parse_workflow(document, ".github/workflows/ci.yml")
    assert parsed is not None
    assert "unittest" in parsed["tools"]


@pytest.mark.parametrize(
    "command",
    [
        "echo python -m unittest",
        "python -c 'import unittest'",
        "python -m unittest.mock",
        "python -m unittest_helper",
        "echo unittest",
    ],
)
def test_unittest_mentions_are_not_ci_test_tools(command: str) -> None:
    document = (
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: " + command + "\n"
    )
    parsed = parse_workflow(document, ".github/workflows/ci.yml")
    assert parsed is not None
    assert "unittest" not in parsed["tools"]


def test_stdlib_ci_does_not_produce_a_missing_tests_nutrient(
    tmp_path: Path, py_digest: DigestResult
) -> None:
    maw = tmp_path / "stdlib-project"
    (maw / ".github/workflows").mkdir(parents=True)
    (maw / "tests").mkdir()
    (maw / "LICENSE").write_text(
        (Path(__file__).resolve().parents[1] / "LICENSE").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (maw / "tests/test_app.py").write_text(
        "import unittest\nclass TestApp(unittest.TestCase):\n    pass\n", encoding="utf-8"
    )
    (maw / ".github/workflows/ci.yml").write_text(
        "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: python -m unittest discover -s tests -v\n",
        encoding="utf-8",
    )
    result = run_digest(
        Target(path=maw), DigestOptions(out=tmp_path / "digest", cache_root=tmp_path / "cache")
    )
    traits = read_json(result, "traits.json")["traits"]
    assert traits["has_ci"] is True
    assert traits["has_tests"] is True
    assert traits["ci_runs_tests"] is True
    comparison = compare_digests(py_digest.out_dir, result.out_dir)
    assert "crab:ci:ci.runs-tests" not in {item.id for item in comparison.candidates}


def test_same_version_ci_detection_cache_is_remined(npm_app: Path, tmp_path: Path) -> None:
    options = DigestOptions(out=tmp_path / "digest", cache_root=tmp_path / "cache")
    first = run_digest(Target(path=npm_app), options)
    manifest_path = first.manifest_path
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert old["analysis_identity"]["ci_tool_detection"] == "unittest-v1"
    del old["analysis_identity"]["ci_tool_detection"]
    manifest_path.write_text(json.dumps(old), encoding="utf-8")
    refreshed = run_digest(Target(path=npm_app), DigestOptions(**options.__dict__))
    assert not refreshed.cached
    assert refreshed.manifest["crab_version"] == old["crab_version"]
    assert refreshed.manifest["prey"]["sha"] == old["prey"]["sha"]
    assert run_digest(Target(path=npm_app), DigestOptions(**options.__dict__)).cached
