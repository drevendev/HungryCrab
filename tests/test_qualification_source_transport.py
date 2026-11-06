"""Both arms retain a real physical prefix when a complete range is too large."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git
from qualification_cycle import Trial, raw_window
from test_discovery_inputs import repository


@pytest.mark.parametrize("arm", ["baseline", "candidate"])
def test_equal_source_pages_keep_original_coordinates(tmp_path: Path, arm: str) -> None:
    source = "\n".join("value_" + str(i) + " = " + repr("x" * 80) for i in range(60)) + "\n"
    donor_root = repository(tmp_path / "owned", {"mechanism.py": source})
    pin = git(donor_root, "rev-parse", "HEAD").strip()
    trial = Trial.__new__(Trial)
    trial.arm, trial.root = arm, tmp_path
    trial.frozen = {"host": {"initial_donor_bytes": 1024}}
    trial.state = {}
    trial.state_path = tmp_path / "state.json"
    trial.pool = {"owned": {"id": "owned", "sha": pin, "view": str(donor_root)}}
    ranges: list[dict[str, Any]] = [{"path": "mechanism.py", "start": 1, "end": 60}]
    window = raw_window(donor_root, pin, "mechanism.py", 1, 60, "donor")
    result = trial.budget_source(
        {"donor": "owned"}, {"id": "need"}, {"donor": ranges, "receiving": [], "windows": [window]}
    )
    page = result["windows"][0]
    assert page["start"] == 1 and 1 < page["end"] < 60
    assert page["content"] == "\n".join(source.splitlines()[: page["end"]])
    assert len((page["content"] + "\n").encode()) <= 1024
    omitted = trial.state["source_preparations"]["need:owned"]["omissions"]
    assert omitted[0]["start"] == page["end"] + 1 and omitted[0]["end"] == 60
