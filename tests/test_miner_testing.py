from __future__ import annotations

from helpers import read_json, read_md

from hungry_crab.digest import DigestResult


def test_npm_test_landscape(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "tests.json")
    assert data["has_tests"] is True
    assert data["test_files"] == 3
    assert data["src_files"] == 7
    assert data["test_dirs"] == ["e2e"]
    assert {"Vitest", "Playwright", "fast-check", "Vitest coverage"} <= set(data["frameworks"])
    assert data["frameworks"]["Playwright"] == "e2e"
    assert data["special"]["e2e"] is True
    assert data["special"]["property"] is True
    assert data["special"]["snapshot"] is False
    assert data["coverage"]["configured"] is True
    assert data["coverage"]["threshold"] is None
    assert {c["tool"] for c in data["configs"]} == {"playwright"}
    text = read_md(npm_digest, "tests.md")
    assert "Playwright (e2e)" in text


def test_python_test_landscape(py_digest: DigestResult) -> None:
    data = read_json(py_digest, "tests.json")
    assert data["test_files"] == 3
    assert data["test_dirs"] == ["tests"]
    assert {"pytest", "Hypothesis", "pytest-cov", "coverage.py"} <= set(data["frameworks"])
    assert data["coverage"]["threshold"] == 80
    assert data["coverage"]["threshold_source"] == "pyproject.toml (--cov-fail-under)"
    assert data["special"]["property"] is True
    assert data["special"]["e2e"] is False


def test_dotnet_test_landscape(dotnet_digest: DigestResult) -> None:
    data = read_json(dotnet_digest, "tests.json")
    assert data["test_files"] == 2
    assert data["src_files"] == 4
    assert {"xUnit", "FluentAssertions", "coverlet", "BenchmarkDotNet", ".NET test SDK"} <= set(
        data["frameworks"]
    )
    assert data["special"]["benchmarks"] is True
    assert data["coverage"]["configured"] is True
    assert data["coverage"]["threshold"] is None
    assert data["test_dirs"] == ["tests", "tests/Crustacean.Tests"]
