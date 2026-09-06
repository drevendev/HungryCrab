from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from helpers import write_tree

from hungry_crab.errors import CrabError, UsageError
from hungry_crab.maw import (
    CONFIG_FILE,
    DEFAULT_HUNGER,
    MawConfig,
    maw_slug,
    write_default_config,
)


def test_defaults_without_a_file(tmp_path: Path) -> None:
    config = MawConfig.load(tmp_path)
    assert config.exists is False
    assert config.license is None
    assert config.mode == "normal"
    assert config.hunger == DEFAULT_HUNGER
    assert config.serve.issues == "ask"
    assert config.serve.label == "hungry-crab"
    assert config.ledger == "repo"
    assert config.ledger_path() == tmp_path.resolve() / ".crab" / "ledger.json"
    assert config.scoring == {}


def test_default_template_round_trips(tmp_path: Path) -> None:
    path = write_default_config(tmp_path)
    assert path.name == CONFIG_FILE
    config = MawConfig.load(tmp_path)
    assert config.exists is True
    assert config.hunger == DEFAULT_HUNGER
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
                "license: GPL-3.0-only\nmode: strict\nhunger:\n  deps: false\n"
                "  docs: ISSUES-ONLY\n  ci: 'yes'\nserve:\n  issues: auto\n"
                "  labels: [crab, food]\n  assignees: [dreven]\n"
                "  max_prs_per_run: 1\nledger: cache\nscoring:\n  categories:\n    ci: 0.5\n"
            )
        },
    )
    config = MawConfig.load(tmp_path)
    assert config.license == "GPL-3.0-only"
    assert config.mode == "strict"
    assert config.hunger["deps"] is False
    assert config.hunger["docs"] == "issues-only"
    assert config.hunger["ci"] is True
    assert config.hunger["tests"] is True, "unspecified categories keep the default"
    assert config.serve.issues == "auto"
    assert config.serve.labels == ["crab", "food"] and config.serve.label == "crab"
    assert config.serve.assignees == ["dreven"]
    assert config.serve.max_prs_per_run == 1
    assert config.ledger == "cache"
    ledger_path = config.ledger_path(tmp_path / "cache")
    assert ledger_path is not None and ledger_path.is_relative_to(tmp_path / "cache" / "maws")
    assert config.scoring == {"categories": {"ci": 0.5}}
    write_tree(tmp_path, {CONFIG_FILE: "ledger: none\n"})
    assert MawConfig.load(tmp_path).ledger_path() is None


@pytest.mark.parametrize(
    "text",
    [
        "mode: loose\n",
        "hunger:\n  ci: maybe\n",
        "serve:\n  issues: sometimes\n",
        "ledger: disk\n",
        "a: [\n",
    ],
)
def test_invalid_values_are_usage_errors(tmp_path: Path, text: str) -> None:
    write_tree(tmp_path, {CONFIG_FILE: text})
    with pytest.raises(UsageError):
        MawConfig.load(tmp_path)


def test_write_scoring_keeps_other_keys(tmp_path: Path) -> None:
    write_tree(tmp_path, {CONFIG_FILE: "ledger: cache\nhunger:\n  deps: false\n"})
    config = MawConfig.load(tmp_path)
    config.write_scoring({"categories": {"ci": 0.95}})
    reloaded = MawConfig.load(tmp_path)
    assert reloaded.ledger == "cache"
    assert reloaded.hunger["deps"] is False
    assert reloaded.scoring == {"categories": {"ci": 0.95}}
    fresh = MawConfig.load(tmp_path / "other")
    (tmp_path / "other").mkdir()
    fresh.write_scoring({"traits": {"ci.cache": 1.0}})
    assert MawConfig.load(tmp_path / "other").scoring == {"traits": {"ci.cache": 1.0}}


def test_maw_slug_needs_a_github_remote(tmp_path: Path, npm_app: Path) -> None:
    assert maw_slug(npm_app) is None
    repo = tmp_path / "with-remote"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "git@github.com:example/maw.git")
    slug = maw_slug(repo)
    assert slug is not None and str(slug) == "example/maw"
    git(repo, "remote", "set-url", "origin", "https://gitlab.com/example/maw.git")
    assert maw_slug(repo) is None


def test_the_old_appetite_key_is_an_error_not_a_shrug(tmp_path: Path) -> None:
    """A silently ignored appetite block is a maw eating what it had switched off."""
    write_tree(tmp_path, {CONFIG_FILE: "appetite:\n  deps: false\n"})
    with pytest.raises(UsageError) as caught:
        MawConfig.load(tmp_path)
    assert "appetite" in caught.value.message
    assert "hunger" in (caught.value.hint or "")
