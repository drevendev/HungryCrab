"""Session-wide fixtures: the synthetic repositories and one digest of each.

Building a repository means running git a few dozen times, and digesting it runs every miner,
so both happen once per session. Tests read the resulting JSON files.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fixture_builder import FIXTURE_NAMES, build_fixture

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, DigestResult, run_digest

# Every fixture history ends before this date; "now" must be stable for staleness metrics.
FIXED_NOW = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def fixture_repos(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if shutil.which("git") is None:
        pytest.skip("git is required to build the fixture repositories")
    base = tmp_path_factory.mktemp("fixtures")
    return {name: build_fixture(name, base / name) for name in FIXTURE_NAMES}


@pytest.fixture(scope="session")
def npm_app(fixture_repos: dict[str, Path]) -> Path:
    return fixture_repos["npm-app"]


@pytest.fixture(scope="session")
def pyproject_cli(fixture_repos: dict[str, Path]) -> Path:
    return fixture_repos["pyproject-cli"]


@pytest.fixture(scope="session")
def dotnet_lib(fixture_repos: dict[str, Path]) -> Path:
    return fixture_repos["dotnet-lib"]


@pytest.fixture(scope="session")
def digests(
    fixture_repos: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, DigestResult]:
    base = tmp_path_factory.mktemp("digests")
    results: dict[str, DigestResult] = {}
    for name, path in fixture_repos.items():
        options = DigestOptions(
            out=base / name,
            now=FIXED_NOW,
            maw_license="MIT",
            cache_root=base / "cache",
        )
        results[name] = run_digest(Target(path=path), options)
    return results


@pytest.fixture(scope="session")
def npm_digest(digests: dict[str, DigestResult]) -> DigestResult:
    return digests["npm-app"]


@pytest.fixture(scope="session")
def py_digest(digests: dict[str, DigestResult]) -> DigestResult:
    return digests["pyproject-cli"]


@pytest.fixture(scope="session")
def dotnet_digest(digests: dict[str, DigestResult]) -> DigestResult:
    return digests["dotnet-lib"]
