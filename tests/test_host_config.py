from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from helpers import write_tree

from hungry_crab.errors import CrabError, UsageError
from hungry_crab.host import (
    CONFIG_FILE,
    DEFAULT_APPETITE,
    HostConfig,
    host_slug,
    write_default_config,
)


def test_defaults_without_a_file(tmp_path: Path) -> None:
    config = HostConfig.load(tmp_path)
    assert config.exists is False
    assert config.license is None
    assert config.mode == "normal"
    assert config.appetite == DEFAULT_APPETITE
    assert config.serve.issues == "ask"
    assert config.serve.label == "hungry-crab"
    assert config.ledger == "repo"
    assert config.ledger_path() == tmp_path.resolve() / ".crab" / "ledger.json"
    assert config.scoring == {}


def test_default_template_round_trips(tmp_path: Path) -> None:
    path = write_default_config(tmp_path)
    assert path.name == CONFIG_FILE
    config = HostConfig.load(tmp_path)
    assert config.exists is True
    assert config.appetite == DEFAULT_APPETITE
    assert config.serve.labels == ["hungry-crab"]
    assert config.attribution_file == "THIRD_PARTY_NOTICES.md"
    with pytest.raises(CrabError, match="already exists"):
        write_default_config(tmp_path)
    write_default_config(tmp_path, force=True)


def test_custom_values_and_ledger_modes(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            CONFIG_FILE: (
                "license: GPL-3.0-only\nmode: strict\nappetite:\n  deps: false\n"
                "  docs: ISSUES-ONLY\n  ci: 'yes'\nserve:\n  issues: auto\n"
                "  labels: [crab, food]\n  assignees: [dreven]\n"
                "  max_prs_per_run: 1\nledger: cache\nscoring:\n  categories:\n    ci: 0.5\n"
            )
        },
    )
    config = HostConfig.load(tmp_path)
    assert config.license == "GPL-3.0-only"
    assert config.mode == "strict"
    assert config.appetite["deps"] is False
    assert config.appetite["docs"] == "issues-only"
    assert config.appetite["ci"] is True
    assert config.appetite["tests"] is True, "unspecified categories keep the default"
    assert config.serve.issues == "auto"
    assert config.serve.labels == ["crab", "food"] and config.serve.label == "crab"
    assert config.serve.assignees == ["dreven"]
    assert config.serve.max_prs_per_run == 1
    assert config.ledger == "cache"
    ledger_path = config.ledger_path(tmp_path / "cache")
    assert ledger_path is not None and ledger_path.is_relative_to(tmp_path / "cache" / "hosts")
    assert config.scoring == {"categories": {"ci": 0.5}}
    write_tree(tmp_path, {CONFIG_FILE: "ledger: none\n"})
    assert HostConfig.load(tmp_path).ledger_path() is None


@pytest.mark.parametrize(
    "text",
    [
        "mode: loose\n",
        "appetite:\n  ci: maybe\n",
        "serve:\n  issues: sometimes\n",
        "ledger: disk\n",
        "a: [\n",
    ],
)
def test_invalid_values_are_usage_errors(tmp_path: Path, text: str) -> None:
    write_tree(tmp_path, {CONFIG_FILE: text})
    with pytest.raises(UsageError):
        HostConfig.load(tmp_path)


def test_write_scoring_keeps_other_keys(tmp_path: Path) -> None:
    write_tree(tmp_path, {CONFIG_FILE: "ledger: cache\nappetite:\n  deps: false\n"})
    config = HostConfig.load(tmp_path)
    config.write_scoring({"categories": {"ci": 0.95}})
    reloaded = HostConfig.load(tmp_path)
    assert reloaded.ledger == "cache"
    assert reloaded.appetite["deps"] is False
    assert reloaded.scoring == {"categories": {"ci": 0.95}}
    fresh = HostConfig.load(tmp_path / "other")
    (tmp_path / "other").mkdir()
    fresh.write_scoring({"traits": {"ci.cache": 1.0}})
    assert HostConfig.load(tmp_path / "other").scoring == {"traits": {"ci.cache": 1.0}}


def test_host_slug_needs_a_github_remote(tmp_path: Path, npm_app: Path) -> None:
    assert host_slug(npm_app) is None
    repo = tmp_path / "with-remote"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "git@github.com:example/host.git")
    slug = host_slug(repo)
    assert slug is not None and str(slug) == "example/host"
    git(repo, "remote", "set-url", "origin", "https://gitlab.com/example/host.git")
    assert host_slug(repo) is None
