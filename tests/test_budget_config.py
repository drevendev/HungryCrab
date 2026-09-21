from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.errors import UsageError
from hungry_crab.maw import MawConfig, write_default_config


def test_budget_policy_defaults_to_warn(tmp_path: Path) -> None:
    assert MawConfig.load(tmp_path).budget.policy == "warn"


@pytest.mark.parametrize("policy", ["warn", "enforce", "off"])
def test_budget_policy_loads_supported_values(tmp_path: Path, policy: str) -> None:
    (tmp_path / ".crab.yml").write_text(
        f"budget:\n  policy: {policy}\n",
        encoding="utf-8",
    )
    assert MawConfig.load(tmp_path).budget.policy == policy


def test_budget_policy_rejects_unknown_value(tmp_path: Path) -> None:
    (tmp_path / ".crab.yml").write_text(
        "budget:\n  policy: explode\n",
        encoding="utf-8",
    )
    with pytest.raises(UsageError, match=r"invalid budget.policy"):
        MawConfig.load(tmp_path)


def test_default_config_declares_warn_budget_policy(tmp_path: Path) -> None:
    path = write_default_config(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "budget:\n  policy: warn" in text
    assert MawConfig.load(tmp_path).budget.policy == "warn"
