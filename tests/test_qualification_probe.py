"""Typed original probes must enter each receiving target, including the control."""

from __future__ import annotations

from pathlib import Path

import pytest
from qualification_probe import python_probe
from test_discovery_inputs import repository

from hungry_crab.receiving_cases import project_results
from hungry_crab.receiving_probe import run_probe

TARGET = {"path": "app.py", "qualified_name": "rank"}
APP = (
    "from dataclasses import dataclass\n"
    "@dataclass\nclass Item:\n    score: int\n"
    "def rank(items):\n    return sorted(items, key=lambda item: item.score)\n"
)


def execute(tmp_path: Path, source: str) -> dict[str, object]:
    maw = repository(tmp_path / "receiving", {"app.py": APP})
    receipt = run_probe(
        tmp_path / "probe",
        maw,
        python_probe(source, TARGET, ["app"]),
        author="owned fixture",
        expected="Typed inputs reach the receiving target in each case",
        controls=["original positive-control"],
        original_receiving_owned=True,
        import_receiving=True,
        required_receiving_calls=[TARGET],
    )
    return project_results(receipt, maw)


def test_typed_receiving_values_and_checks_are_really_executed(tmp_path: Path) -> None:
    result = execute(
        tmp_path,
        "from app import Item, rank\n"
        "record_case('two', 'target', lambda: rank([Item(2), Item(1)]), "
        "[{'pointer':'/value/0/score', 'equals':1}])\n"
        "record_case('one', 'positive-control', lambda: rank([Item(3)]), "
        "[{'pointer':'/value/0/score', 'equals':3}])\n",
    )
    assert result["status"] == "executed"
    assert result["case_results_usable"] is True
    assert all(row["passed"] for row in result["cases"])


def test_setup_and_control_calls_cannot_mask_missing_target_entry(tmp_path: Path) -> None:
    result = execute(
        tmp_path,
        "from app import Item, rank\nrank([Item(9)])\n"
        "record_case('bad-arity', 'target', lambda: rank([], 2), "
        "[{'pointer':'/value', 'equals':[]}])\n"
        "record_case('good', 'positive-control', lambda: rank([]), "
        "[{'pointer':'/value', 'equals':[]}])\n",
    )
    assert result["case_results_usable"] is False
    assert result["cases"][0]["target_called"] is False
    assert result["cases"][1]["target_called"] is True


@pytest.mark.parametrize(
    "prefix",
    [
        "import os\n",
        "import donor\n",
        "fixture_root = None\n",
        "print('fake')\n",
        "def record_case(*args):\n    pass\n",
        "import math as record_case\n",
    ],
)
def test_unapproved_programs_are_rejected_before_execution(prefix: str) -> None:
    with pytest.raises(ValueError):
        python_probe(
            prefix + "record_case('a','target',lambda:1,[])\n"
            "record_case('b','positive-control',lambda:1,[])\n",
            TARGET,
            ["app"],
        )
