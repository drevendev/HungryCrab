"""A successful prerequisite is not proof that a receiving flow executed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_discovery_inputs import repository

from hungry_crab.discovery_session import live_context
from hungry_crab.errors import CrabError
from hungry_crab.receiving_probe import run_probe, verify_receipt
from hungry_crab.receiving_runtime import runtime_preflight


@pytest.mark.parametrize("calls_receiver", [False, True])
def test_receipt_requires_actual_selected_function_call(
    tmp_path: Path, calls_receiver: bool
) -> None:
    maw = repository(
        tmp_path / "receiving", {"app.py": "def evaluate(value):\n    return value + 1\n"}
    )
    source = (
        "from app import evaluate\nassert evaluate(3) == 4\n"
        if calls_receiver
        else "import ast\nassert ast.parse('x = 1')\n"
    )
    path = run_probe(
        tmp_path / "probe",
        maw,
        source,
        author="original fixture host",
        expected="selected receiving flow and positive result",
        controls=["small known-correct input"],
        original_receiving_owned=True,
        import_receiving=True,
        required_receiving_calls=[{"path": "app.py", "qualified_name": "evaluate"}],
    )
    receipt = json.loads(path.read_text())
    assert receipt["exit_code"] == 0 and receipt["runtime"]["version"] == list(sys.version_info[:3])
    if calls_receiver:
        assert receipt["status"] == "executed"
        assert receipt["observed_receiving_calls"] == receipt["required_receiving_calls"]
        verify_receipt(path, live_context(maw), receipt["probe_sha256"])
    else:
        assert receipt["status"] == "required-receiving-flow-not-observed"
        with pytest.raises(CrabError, match="executed probe"):
            verify_receipt(path, live_context(maw), receipt["probe_sha256"])


def test_incompatible_receiving_runtime_refuses_before_launch(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving", {"pyproject.toml": '[project]\nrequires-python = ">=99.0"\n'}
    )
    output = tmp_path / "unstarted"
    with pytest.raises(CrabError, match="active interpreter violates"):
        run_probe(
            output,
            maw,
            "raise AssertionError('must not run')",
            author="fixture",
            expected="unavailable",
            controls=["unsupported current runtime"],
            original_receiving_owned=True,
        )
    assert not output.exists()


@pytest.mark.parametrize("constraint", [">=3.11,<4", "!=2.*", "~=3.11"])
def test_supported_runtime_constraints_keep_actual_interpreter(
    tmp_path: Path, constraint: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nrequires-python = " + json.dumps(constraint), encoding="utf-8"
    )
    result = runtime_preflight(tmp_path)
    assert result["status"] == "compatible" and not result["implicit_installation"]


def test_unknown_runtime_constraint_is_not_silently_accepted(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11rc1"', encoding="utf-8"
    )
    with pytest.raises(CrabError, match="unsupported Python constraint"):
        runtime_preflight(tmp_path)
