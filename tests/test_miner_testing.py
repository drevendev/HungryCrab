from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW
from helpers import read_json, read_md, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, DigestResult, run_digest


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


def test_go_test_landscape(go_digest: DigestResult) -> None:
    data = read_json(go_digest, "tests.json")
    assert data["has_tests"] is True
    assert data["test_files"] == 2
    # `go test` runs the standard library's harness, which is in no manifest; before this the
    # fixture reported two test files and no framework at all, as ossf/scorecard did with 271.
    assert data["frameworks"]["testing"] == "unit"
    # testify is declared as `github.com/stretchr/testify`, and the shared map is keyed on bare
    # package names, so it was unreachable until Go got a map of its own.
    assert data["frameworks"]["testify"] == "unit"
    text = read_md(go_digest, "tests.md")
    assert "testing (unit)" in text


GO_MODULE = "module example.com/plain\n\ngo 1.22\n"
GO_SOURCE = "package plain\n\nfunc Count(n int) int { return n + 1 }\n"
GO_TEST = (
    'package plain\n\nimport "testing"\n\n'
    "func TestCount(t *testing.T) {\n\tif Count(1) != 2 {\n\t\tt.Fail()\n\t}\n}\n"
)


def test_go_standard_library_alone_is_still_a_framework(tmp_path: Path) -> None:
    """A Go repository with no test dependency at all still tests, with `testing`.

    The fixture declares testify, so it cannot show this case on its own: the standard library
    has to be found with nothing in `go.mod` to find it by.
    """
    prey = write_tree(
        tmp_path / "prey",
        {"go.mod": GO_MODULE, "count.go": GO_SOURCE, "count_test.go": GO_TEST},
    )
    result = run_digest(
        Target(path=prey),
        DigestOptions(out=tmp_path / "digest", now=FIXED_NOW, cache_root=tmp_path / "cache"),
    )
    data = read_json(result, "tests.json")
    assert data["has_tests"] is True
    assert data["frameworks"] == {"testing": "unit"}
