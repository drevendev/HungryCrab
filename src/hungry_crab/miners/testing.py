"""Tests miner: the test landscape (frameworks, layout, coverage, special kinds of tests)."""

from __future__ import annotations

import re
import tomllib
from typing import Any

from ..mdutil import MdDoc
from ..typeutil import as_dict
from .base import FileInfo, MineContext, MinerResult

TEST_DIR_NAMES = frozenset(
    {"test", "tests", "__tests__", "spec", "specs", "e2e", "integration", "unit", "testing"}
)
_TEST_PROJECT_SUFFIXES = (".Tests", ".Test", ".UnitTests", ".IntegrationTests")
_TEST_FILE_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:^|/)test_[^/]+\.py$",
        r"_test\.py$",
        r"(?:^|/)conftest\.py$",
        r"\.test\.[cm]?[jt]sx?$",
        r"\.spec\.[cm]?[jt]sx?$",
        r"Tests?\.cs$",
        r"_test\.go$",
        r"_spec\.rb$",
        r"Test\.java$",
        r"Tests?\.kt$",
        r"Tests\.swift$",
        r"_test\.rs$",
        r"\.test\.dart$",
    )
)

# package name (lower-case) -> (framework, kind)
FRAMEWORK_BY_PACKAGE: dict[str, tuple[str, str]] = {
    "jest": ("Jest", "unit"),
    "vitest": ("Vitest", "unit"),
    "mocha": ("Mocha", "unit"),
    "ava": ("AVA", "unit"),
    "jasmine": ("Jasmine", "unit"),
    "karma": ("Karma", "unit"),
    "uvu": ("uvu", "unit"),
    "tap": ("node-tap", "unit"),
    "@playwright/test": ("Playwright", "e2e"),
    "playwright": ("Playwright", "e2e"),
    "cypress": ("Cypress", "e2e"),
    "puppeteer": ("Puppeteer", "e2e"),
    "webdriverio": ("WebdriverIO", "e2e"),
    "@testing-library/react": ("Testing Library", "unit"),
    "@testing-library/vue": ("Testing Library", "unit"),
    "@testing-library/dom": ("Testing Library", "unit"),
    "fast-check": ("fast-check", "property"),
    "jsverify": ("jsverify", "property"),
    "msw": ("MSW", "mock"),
    "nock": ("nock", "mock"),
    "sinon": ("Sinon", "mock"),
    "supertest": ("supertest", "integration"),
    "testcontainers": ("Testcontainers", "integration"),
    "@testcontainers/postgresql": ("Testcontainers", "integration"),
    "c8": ("c8", "coverage"),
    "nyc": ("nyc", "coverage"),
    "@vitest/coverage-v8": ("Vitest coverage", "coverage"),
    "@vitest/coverage-istanbul": ("Vitest coverage", "coverage"),
    "istanbul": ("Istanbul", "coverage"),
    "@storybook/test-runner": ("Storybook test runner", "visual"),
    "chromatic": ("Chromatic", "visual"),
    "jest-image-snapshot": ("jest-image-snapshot", "snapshot"),
    "tinybench": ("tinybench", "bench"),
    "benchmark": ("benchmark.js", "bench"),
    "@codspeed/vitest-plugin": ("CodSpeed", "bench"),
    "pytest": ("pytest", "unit"),
    "pytest-cov": ("pytest-cov", "coverage"),
    "coverage": ("coverage.py", "coverage"),
    "hypothesis": ("Hypothesis", "property"),
    "pytest-benchmark": ("pytest-benchmark", "bench"),
    "tox": ("tox", "runner"),
    "nox": ("nox", "runner"),
    "pytest-xdist": ("pytest-xdist", "runner"),
    "pytest-asyncio": ("pytest-asyncio", "unit"),
    "pytest-mock": ("pytest-mock", "mock"),
    "responses": ("responses", "mock"),
    "respx": ("respx", "mock"),
    "vcrpy": ("vcrpy", "mock"),
    "syrupy": ("syrupy", "snapshot"),
    "snapshottest": ("snapshottest", "snapshot"),
    "atheris": ("Atheris", "fuzz"),
    "mutmut": ("mutmut", "mutation"),
    "pytest-playwright": ("Playwright", "e2e"),
    "selenium": ("Selenium", "e2e"),
    "locust": ("Locust", "load"),
    "xunit": ("xUnit", "unit"),
    "xunit.runner.visualstudio": ("xUnit", "unit"),
    "nunit": ("NUnit", "unit"),
    "mstest.testframework": ("MSTest", "unit"),
    "microsoft.net.test.sdk": (".NET test SDK", "runner"),
    "fluentassertions": ("FluentAssertions", "assert"),
    "shouldly": ("Shouldly", "assert"),
    "moq": ("Moq", "mock"),
    "nsubstitute": ("NSubstitute", "mock"),
    "fakeiteasy": ("FakeItEasy", "mock"),
    "coverlet.collector": ("coverlet", "coverage"),
    "coverlet.msbuild": ("coverlet", "coverage"),
    "fscheck": ("FsCheck", "property"),
    "benchmarkdotnet": ("BenchmarkDotNet", "bench"),
    "verify.xunit": ("Verify", "snapshot"),
    "verify.nunit": ("Verify", "snapshot"),
    "microsoft.playwright": ("Playwright", "e2e"),
    "bogus": ("Bogus", "fixture"),
    "autofixture": ("AutoFixture", "fixture"),
    "stryker": ("Stryker", "mutation"),
    "proptest": ("proptest", "property"),
    "quickcheck": ("quickcheck", "property"),
    "criterion": ("Criterion", "bench"),
    "insta": ("insta", "snapshot"),
    "testify": ("testify", "unit"),
    "gomock": ("gomock", "mock"),
}

CONFIG_FILES: dict[str, str] = {
    "jest.config.js": "jest",
    "jest.config.ts": "jest",
    "jest.config.mjs": "jest",
    "jest.config.cjs": "jest",
    "vitest.config.ts": "vitest",
    "vitest.config.js": "vitest",
    "vitest.config.mts": "vitest",
    "vitest.workspace.ts": "vitest",
    "vitest.workspace.js": "vitest",
    "playwright.config.ts": "playwright",
    "playwright.config.js": "playwright",
    "cypress.config.ts": "cypress",
    "cypress.config.js": "cypress",
    "cypress.json": "cypress",
    "karma.conf.js": "karma",
    ".mocharc.yml": "mocha",
    ".mocharc.json": "mocha",
    ".mocharc.js": "mocha",
    "pytest.ini": "pytest",
    "tox.ini": "tox",
    "noxfile.py": "nox",
    ".coveragerc": "coverage",
    "codecov.yml": "codecov",
    ".codecov.yml": "codecov",
    ".nycrc": "nyc",
    ".nycrc.json": "nyc",
    "coverlet.runsettings": "coverlet",
    ".runsettings": "dotnet-test",
    "stryker-config.json": "stryker",
    "wallaby.js": "wallaby",
}
_UNIT_CONFIG_TOOLS = {"jest", "vitest", "mocha", "karma", "pytest"}
_E2E_CONFIG_TOOLS = {"playwright": "Playwright", "cypress": "Cypress"}
_COVERAGE_CONFIG_TOOLS = {"codecov", "nyc", "coverage", "coverlet"}

_THRESHOLD_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("--cov-fail-under", re.compile(r"--cov-fail-under[=\s]+(\d+)")),
    ("fail_under", re.compile(r"fail[_-]under\s*[=:]\s*(\d+)")),
    (
        "coverageThreshold",
        re.compile(
            r"coverageThreshold[\s\S]{0,200}?(?:lines|statements|branches|functions)\s*:\s*(\d+)"
        ),
    ),
    (
        "thresholds",
        re.compile(r"thresholds[\s\S]{0,200}?(?:lines|statements|branches|functions)\s*:\s*(\d+)"),
    ),
    ("check-coverage", re.compile(r"(?:check-coverage|\"lines\")[^\n]{0,40}?(\d{2,3})")),
    ("Threshold", re.compile(r"<Threshold>\s*(\d+)")),
)
_THRESHOLD_CANDIDATES = (
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    ".coveragerc",
    "package.json",
    "Makefile",
    "justfile",
    "Taskfile.yml",
)
_BENCH_DIRS = {"bench", "benchmarks", "benchmark"}
_ADR_LIKE_TEST_DIRS = {"e2e", "integration"}


def is_test_file(info: FileInfo) -> bool:
    parts = info.path.split("/")
    dirs = parts[:-1]
    if any(part.lower() in TEST_DIR_NAMES for part in dirs):
        return info.is_code or info.ext in {".json", ".snap", ".yml", ".yaml"}
    if any(part.endswith(_TEST_PROJECT_SUFFIXES) for part in dirs):
        return True
    return any(pattern.search(info.path) for pattern in _TEST_FILE_RES)


def _test_dirs(test_files: list[FileInfo]) -> list[str]:
    found: set[str] = set()
    for info in test_files:
        parts = info.path.split("/")
        for index, part in enumerate(parts[:-1]):
            if part.lower() in TEST_DIR_NAMES or part.endswith(_TEST_PROJECT_SUFFIXES):
                found.add("/".join(parts[: index + 1]))
    return sorted(found)[:20]


class TestingMiner:
    name = "testing"
    requires: tuple[str, ...] = ("inventory", "deps")
    json_file = "tests.json"
    md_file = "tests.md"

    def run(self, ctx: MineContext) -> MinerResult:
        files = [f for f in ctx.files() if f.counted]
        test_files = [f for f in files if is_test_file(f)]
        test_paths = {f.path for f in test_files}
        src_files = [f for f in files if f.is_code and f.path not in test_paths]
        test_dirs = _test_dirs(test_files)

        frameworks = self._frameworks(ctx, files, test_files)
        configs = [
            {"path": f.path, "tool": CONFIG_FILES[f.name]}
            for f in files
            if f.name in CONFIG_FILES and f.depth <= 2
        ]
        for config in configs:
            tool = config["tool"]
            if tool in _UNIT_CONFIG_TOOLS:
                frameworks.setdefault(tool, "unit")
            elif tool in ("tox", "nox"):
                frameworks.setdefault(tool, "runner")
            elif tool in _E2E_CONFIG_TOOLS:
                frameworks.setdefault(_E2E_CONFIG_TOOLS[tool], "e2e")

        threshold, threshold_source = self._coverage_threshold(ctx, configs)
        coverage_configured = (
            threshold is not None
            or any(kind == "coverage" for kind in frameworks.values())
            or any(c["tool"] in _COVERAGE_CONFIG_TOOLS for c in configs)
        )
        coverage_service = "codecov" if any(c["tool"] == "codecov" for c in configs) else None

        kinds = set(frameworks.values())
        dir_names = {part for f in files for part in f.path.split("/")[:-1]}
        top_dirs = {f.path.split("/")[0] for f in files if "/" in f.path}
        special = {
            "e2e": "e2e" in kinds or any(d.split("/")[-1] == "e2e" for d in test_dirs),
            "property": "property" in kinds,
            "snapshot": "snapshot" in kinds
            or any(
                f.ext == ".snap" or "__snapshots__" in f.path or f.name.endswith(".verified.txt")
                for f in files
            ),
            "fuzz": "fuzz" in kinds or bool(dir_names & {"fuzz", "fuzzing"}),
            "mutation": "mutation" in kinds,
            "benchmarks": "bench" in kinds
            or bool(top_dirs & _BENCH_DIRS)
            or any(".bench." in f.name for f in files),
            "integration": "integration" in kinds
            or any(d.split("/")[-1] == "integration" for d in test_dirs),
            "mocking": "mock" in kinds,
        }
        test_loc = sum(f.loc for f in test_files)
        src_loc = sum(f.loc for f in src_files)
        data: dict[str, Any] = {
            "has_tests": bool(test_files),
            "test_files": len(test_files),
            "src_files": len(src_files),
            "test_to_src_ratio": round(len(test_files) / len(src_files), 2) if src_files else None,
            "test_loc": test_loc,
            "src_loc": src_loc,
            "test_loc_ratio": round(test_loc / src_loc, 2) if src_loc else None,
            "test_dirs": test_dirs,
            "frameworks": dict(sorted(frameworks.items())),
            "configs": configs,
            "coverage": {
                "configured": coverage_configured,
                "threshold": threshold,
                "threshold_source": threshold_source,
                "service": coverage_service,
            },
            "special": special,
            "sample_test_files": [f.path for f in test_files[:15]],
        }
        return MinerResult(self.name, data, doc=self._doc(ctx, data))

    def _frameworks(
        self, ctx: MineContext, files: list[FileInfo], test_files: list[FileInfo]
    ) -> dict[str, str]:
        dep_names: dict[str, list[str]] = ctx.extra("deps")["names"]
        frameworks: dict[str, str] = {}
        for names in dep_names.values():
            for name in names:
                hit = FRAMEWORK_BY_PACKAGE.get(name)
                if hit:
                    frameworks[hit[0]] = hit[1]
        if ctx.exists("pyproject.toml"):
            try:
                tool_table = as_dict(tomllib.loads(ctx.read("pyproject.toml")).get("tool"))
            except tomllib.TOMLDecodeError:
                tool_table = {}
            if "pytest" in tool_table:
                frameworks.setdefault("pytest", "unit")
            if "coverage" in tool_table:
                frameworks.setdefault("coverage.py", "coverage")
        if any(f.name == "conftest.py" for f in files):
            frameworks.setdefault("pytest", "unit")
        python_tests = any(f.ext == ".py" for f in test_files)
        if python_tests and not any(k in frameworks for k in ("pytest", "unittest (guess)")):
            frameworks["unittest (guess)"] = "unit"
        return frameworks

    def _coverage_threshold(
        self, ctx: MineContext, configs: list[dict[str, str]]
    ) -> tuple[int | None, str | None]:
        candidates = list(_THRESHOLD_CANDIDATES)
        candidates += [c["path"] for c in configs]
        candidates += [f.path for f in ctx.find("*.csproj") if "Test" in f.path][:10]
        candidates += [f.path for f in ctx.files() if f.path.startswith(".github/workflows/")][:10]
        for rel in candidates:
            if not ctx.exists(rel):
                continue
            text = ctx.read(rel, limit=200_000)
            for label, pattern in _THRESHOLD_RES:
                match = pattern.search(text)
                if match:
                    value = int(match.group(1))
                    if 0 < value <= 100:
                        return value, f"{rel} ({label})"
        return None, None

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"Tests: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        if not data["has_tests"]:
            summary.para("No test files detected.")
        coverage = data["coverage"]
        ratio = data["test_to_src_ratio"]
        threshold = (
            f"{coverage['threshold']}% from {coverage['threshold_source']}"
            if coverage["threshold"]
            else "none"
        )
        summary.kv(
            [
                ("Test files", data["test_files"]),
                ("Source files", data["src_files"]),
                ("Test/source file ratio", ratio if ratio is not None else "n/a"),
                ("Test LOC / source LOC", f"{data['test_loc']} / {data['src_loc']}"),
                (
                    "Frameworks",
                    ", ".join(f"{k} ({v})" for k, v in data["frameworks"].items())
                    or "none detected",
                ),
                ("Test directories", ", ".join(data["test_dirs"]) or "none"),
                ("Coverage configured", coverage["configured"]),
                ("Coverage threshold", threshold),
                ("Coverage service", coverage["service"] or "none"),
            ]
        )
        special = doc.section("Special kinds of tests", priority=2)
        special.kv(
            [
                (name.replace("_", " ").capitalize(), value)
                for name, value in data["special"].items()
            ]
        )
        if data["configs"]:
            configs = doc.section("Configuration files", priority=3)
            configs.bullets((f"{c['path']} ({c['tool']})" for c in data["configs"]), max_items=15)
        if data["sample_test_files"]:
            sample = doc.section("Sample test files", priority=4)
            sample.bullets(data["sample_test_files"], max_items=15)
        return doc
