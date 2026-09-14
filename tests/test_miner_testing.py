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
    # Coverage is declared on the `go test` command line and in the upload action, in no
    # manifest and no config file. The CI miner had both written down while this one said
    # `configured: false`.
    assert data["coverage"]["configured"] is True
    assert data["coverage"]["service"] == "codecov"
    assert data["coverage"]["threshold"] is None, "a threshold is declared in a file or not at all"
    assert data["coverage"]["in_ci"] == {
        "workflow": ".github/workflows/ci.yml",
        "upload": "codecov",
        "flags": ["-coverprofile"],
    }
    text = read_md(go_digest, "tests.md")
    assert "testing (unit)" in text
    assert "upload to codecov, -coverprofile (.github/workflows/ci.yml)" in text


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


def _digest(tmp_path: Path, files: dict[str, str], name: str = "prey") -> dict:  # type: ignore[type-arg]
    prey = write_tree(tmp_path / name, files)
    result = run_digest(
        Target(path=prey),
        DigestOptions(out=tmp_path / f"{name}-digest", now=FIXED_NOW, cache_root=tmp_path / "c"),
    )
    return read_json(result, "tests.json")


_WORKFLOW_HEAD = "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"


def test_coverage_declared_only_on_the_test_command_line_counts(tmp_path: Path) -> None:
    """No `.coveragerc`, no coverage package, no upload: the flag on `go test` is the whole
    declaration, and a Go project has nowhere else to put it."""
    data = _digest(
        tmp_path,
        {
            "go.mod": GO_MODULE,
            "count.go": GO_SOURCE,
            "count_test.go": GO_TEST,
            ".github/workflows/ci.yml": _WORKFLOW_HEAD
            + "      - run: go test -race -coverprofile=cover.out ./...\n",
        },
    )
    assert data["coverage"]["configured"] is True
    assert data["coverage"]["service"] is None
    assert data["coverage"]["in_ci"]["flags"] == ["-coverprofile"]
    assert data["coverage"]["in_ci"]["upload"] is None
    assert data["configs"] == [], "the flag came from the workflow, not from a config file"


def test_a_test_step_without_a_coverage_flag_is_still_not_coverage(tmp_path: Path) -> None:
    data = _digest(
        tmp_path,
        {
            "go.mod": GO_MODULE,
            "count.go": GO_SOURCE,
            "count_test.go": GO_TEST,
            ".github/workflows/ci.yml": _WORKFLOW_HEAD + "      - run: go test -race ./...\n",
        },
    )
    assert data["coverage"] == {
        "configured": False,
        "threshold": None,
        "threshold_source": None,
        "service": None,
        "in_ci": None,
    }


def test_the_upload_action_names_the_coverage_service(tmp_path: Path) -> None:
    data = _digest(
        tmp_path,
        {
            "go.mod": GO_MODULE,
            "count.go": GO_SOURCE,
            "count_test.go": GO_TEST,
            ".github/workflows/ci.yml": _WORKFLOW_HEAD
            + "      - run: go test ./...\n      - uses: coverallsapp/github-action@v2\n",
        },
    )
    assert data["coverage"]["configured"] is True
    assert data["coverage"]["service"] == "coveralls"
    assert data["coverage"]["in_ci"] == {
        "workflow": ".github/workflows/ci.yml",
        "upload": "coveralls",
        "flags": [],
    }


PACKAGE_JSON = '{"name": "app", "version": "1.0.0", "devDependencies": {"vitest": "^2.0.0"}}\n'
PYTHON_TEST = (
    "import unittest\n\n\n"
    "class T(unittest.TestCase):\n    def test_x(self) -> None:\n        pass\n"
)


def test_a_python_test_under_a_marked_example_tree_is_not_a_framework(tmp_path: Path) -> None:
    """The example-tree exclusion reaches the test frameworks through `FileInfo.counted`.

    Filed as a leak (#54) after promptfoo reported `unittest (guess)`; the synthetic guard shows
    the exclusion holds, and promptfoo's Python tests live under `src/python/`, outside the
    examples, where they are the project's own and are counted on purpose.
    """
    data = _digest(
        tmp_path,
        {
            "package.json": PACKAGE_JSON,
            "src/index.ts": "export const x = 1;\n",
            "src/index.test.ts": "import { x } from './index';\n",
            "examples/python-client/requirements.txt": "requests\n",
            "examples/python-client/test_client.py": PYTHON_TEST,
        },
        name="excluded",
    )
    assert "unittest (guess)" not in data["frameworks"]
    assert data["frameworks"]["Vitest"] == "unit"
    assert data["test_files"] == 1

    data = _digest(
        tmp_path,
        {
            "package.json": PACKAGE_JSON,
            "src/index.ts": "export const x = 1;\n",
            "src/index.test.ts": "import { x } from './index';\n",
            "src/python/wrapper_test.py": PYTHON_TEST,
            "examples/python-client/requirements.txt": "requests\n",
            "examples/python-client/test_client.py": PYTHON_TEST,
        },
        name="own",
    )
    assert data["frameworks"]["unittest (guess)"] == "unit", "the project's own Python tests"
    assert data["test_files"] == 2, "the example's test file is still not counted"
