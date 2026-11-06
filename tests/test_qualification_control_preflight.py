"""Control preparation validates both modes through the actual current CLI.

Both digest roles use the current interpreter in this regression; this test
does not claim published-baseline interoperability or native qualification.
"""

from __future__ import annotations

import sys
from pathlib import Path

from qualification_controls import prepare
from research_host import read


def test_control_profile_is_written_only_after_actual_controller_preflight(tmp_path: Path) -> None:
    root = tmp_path / "owned-controls"
    repo = Path(__file__).resolve().parents[1]
    prepare(root, Path(sys.executable), repo)
    profile = read(root / "control-profile.json")
    assert profile["purpose"] == "engineering-control"
    for mode in ("runtime", "design"):
        state = read(root / (mode + "-preflight-session") / "session.json")
        assert state["spent"] == {"requests": 0, "donors": 0, "model_tokens": 0}
        assert state["context"] == profile["maws"][mode]["context"]
        assert len(state["needs"]) == 2
    assert not (root / "trials").exists()
