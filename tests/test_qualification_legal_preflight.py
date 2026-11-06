"""Actual CLI HUMAN holds do not waste semantic calls or erase nested grants."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fixture_builder import git
from qualification_cycle import Trial
from research_host import Commands, read, write
from test_discovery_inputs import MIT, repository

from hungry_crab.discovery_session import live_context


@pytest.mark.parametrize("nested_permissive", [False, True])
def test_actual_arm_unknown_grant_is_held_but_nested_mit_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested_permissive: bool
) -> None:
    files = {
        "LICENSE": (
            MIT
            if nested_permissive
            else "Nimbus Exploration License: separate written permissions required.\n"
        ),
        "nested/value.py": "raise AssertionError('DONOR MUST NEVER EXECUTE')\n",
    }
    if nested_permissive:
        files["nested/LICENSE"] = MIT
    source = repository(tmp_path / "source", files)
    maw = repository(
        tmp_path / "receiver", {"app.py": "value = 1\n", ".crab.yml": "ledger: none\n"}
    )
    trial = Trial.__new__(Trial)
    trial.arm, trial.maw = "candidate", maw
    trial.context = live_context(maw)
    trial.root = tmp_path / "host"
    trial.root.mkdir()
    trial.state_path = trial.root / "host-state.json"
    trial.state = {"meals": {}, "admissions": []}
    trial.frozen = {"calibration_only": True}
    trial.pool = {
        "owned": {
            "id": "owned",
            "repository": "owned/fixture",
            "view": str(source),
            "sha": git(source, "rev-parse", "HEAD").strip(),
            "arm_digests": {"candidate": "unused"},
        }
    }
    trial.commands = Commands(trial.root / "cli", Path(sys.executable), Path(__file__).parents[1])
    trial.session = None
    if nested_permissive:
        # Owned unit fixture for an overbroad overview: the actual selected path
        # really is MIT. This is not an override of an unknown operative parent
        # grant and never edits an experimental donor's licensing evidence.
        meal = trial.compare_meal({"donor": "owned"}, {"id": "need"})
        overview = read(meal / "menu.json")
        assert overview["verdict"]["mode"] == "COPY"
        overview["verdict"] = {"mode": "HUMAN", "reason": "Owned broad-overview test fixture"}
        fixture = trial.root / "broad-overview-fixture"
        fixture.mkdir()
        write(fixture / "menu.json", overview)
        monkeypatch.setattr(trial, "compare_meal", lambda *_: fixture)
    held = trial.legal_hold(
        {"donor": "owned"},
        {"id": "need"},
        {"donor": [{"path": "nested/value.py", "start": 1, "end": 1}]},
    )
    meal = trial.compare_meal({"donor": "owned"}, {"id": "need"})
    assert read(meal / "menu.json")["verdict"]["mode"] == "HUMAN"
    assert held is not nested_permissive
    if held:
        row = trial.state["unassessed_preparations"][0]
        assert row["utility"] == "unassessed" and row["native_assessment_tokens"] == 0
    else:
        assert not trial.state.get("unassessed_preparations")
    assert not (trial.root / "native").exists()
