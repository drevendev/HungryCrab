"""A repository's own test fixtures must not be digested as if they were its code."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import copy_repo, read_json, write_tree

from hungry_crab.cache import Target
from hungry_crab.compare import compare_for_host
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.fs import is_ignored
from hungry_crab.host import CONFIG_FILE


@pytest.mark.parametrize(
    ("path", "patterns", "expected"),
    [
        ("tests/fixtures/repos/a/package.json", ["tests/fixtures/**"], True),
        ("tests/fixtures/repos/a/package.json", ["tests/fixtures"], True),
        ("tests/fixtures/repos/a/package.json", ["tests/fixtures/*"], True),
        ("tests/fixtures", ["tests/fixtures/**"], True),
        ("tests/test_cli.py", ["tests/fixtures/**"], False),
        ("src/app.py", ["*.md"], False),
        ("docs/x.md", ["*.md"], True),
        ("src/app.py", [], False),
        ("src/app.py", ["", "  "], False),
        ("vendor/lib/x.go", ["vendor/**", "examples/**"], True),
    ],
)
def test_is_ignored(path: str, patterns: list[str], expected: bool) -> None:
    assert is_ignored(path, patterns) is expected


def test_digest_without_ignore_sees_the_fixtures(npm_app: Path, tmp_path: Path) -> None:
    """The failing case that started this: a host whose fixtures are foreign stacks."""
    host = tmp_path / "host"
    write_tree(
        host,
        {
            "pyproject.toml": '[project]\nname = "thing"\nversion = "0.1.0"\n',
            "src/thing/__init__.py": "x = 1\n",
        },
    )
    copy_repo(npm_app, host / "tests" / "fixtures" / "npm-app")
    result = run_digest(Target(path=host), DigestOptions(out=tmp_path / "d1", now=FIXED_NOW))
    traits = read_json(result, "traits.json")["traits"]
    assert "npm" in traits["ecosystems"], "without ignore the fixture counts as the host's stack"
    assert "eslint" in traits["linters"]
    assert traits["has_e2e_tests"] is True


def test_ignore_keeps_the_fixtures_out_of_the_digest(npm_app: Path, tmp_path: Path) -> None:
    host = tmp_path / "host"
    write_tree(
        host,
        {
            "pyproject.toml": '[project]\nname = "thing"\nversion = "0.1.0"\n',
            "src/thing/__init__.py": "x = 1\n",
            CONFIG_FILE: "ignore:\n  - tests/fixtures/**\n",
        },
    )
    copy_repo(npm_app, host / "tests" / "fixtures" / "npm-app")
    result = run_digest(Target(path=host), DigestOptions(out=tmp_path / "d2", now=FIXED_NOW))
    inventory = read_json(result, "inventory.json")
    traits = read_json(result, "traits.json")["traits"]
    assert inventory["ignored"]["patterns"] == ["tests/fixtures/**"]
    assert inventory["ignored"]["files"] > 20
    assert traits["ecosystems"] == ["python"], "the npm fixture is not this host's stack"
    assert traits["linters"] == [] and traits["formatters"] == []
    assert traits["has_e2e_tests"] is False
    assert traits["test_frameworks"] == []
    assert result.manifest["ignore"] == ["tests/fixtures/**"]
    top = {row["path"] for row in inventory["top_level"]}
    assert "tests" not in top


def test_explicit_ignore_option_wins_over_the_config(npm_app: Path, tmp_path: Path) -> None:
    host = tmp_path / "host"
    write_tree(host, {"pyproject.toml": '[project]\nname = "t"\nversion = "0"\n'})
    copy_repo(npm_app, host / "vendored")
    options = DigestOptions(out=tmp_path / "d3", now=FIXED_NOW, ignore=["vendored/**"])
    result = run_digest(Target(path=host), options)
    assert read_json(result, "traits.json")["traits"]["ecosystems"] == ["python"]


def test_compare_applies_the_host_ignore(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    """The prey keeps its whole tree; only the host's fixtures are excluded."""
    host = copy_repo(pyproject_cli, tmp_path / "host")
    copy_repo(npm_app, host / "tests" / "fixtures" / "npm-app")
    write_tree(host, {CONFIG_FILE: "ignore:\n  - tests/fixtures/**\nledger: none\n"})
    result, _, _, config = compare_for_host(
        Target(path=npm_app),
        host,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache"),
        now=FIXED_NOW,
    )
    assert config.ignore == ["tests/fixtures/**"]
    assert result.host.trait("ecosystems") == ["python"]
    assert result.prey.trait("ecosystems") == ["npm"], "the prey is digested whole"
    ids = {c.id for c in result.candidates}
    assert "crab:tooling:tooling.linter.eslint" not in ids, "the host is not an npm project"
