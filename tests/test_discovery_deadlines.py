"""A cached attempt never authorizes work beyond the whole-session deadline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_discovery_inputs import repository
from test_discovery_usage import usage
from test_release_069_discovery import plan, receipt

from hungry_crab import discovery_session as sessions


def setup(tmp_path: Path, *, seconds: int = 1) -> tuple[Path, dict[str, Any], datetime]:
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    start = datetime.now(UTC) - timedelta(seconds=10)
    definition = plan(maw)
    definition["limits"] = {"seconds": seconds}
    directory = tmp_path / "session"
    sessions.initialize(directory, maw, definition, now=start)
    return directory, sessions.next_action(directory, now=start), start


@pytest.mark.parametrize("elapsed", [-1, 1, 2])
def test_deadline_stops_cached_lease_and_keeps_actual_cost_settlement(
    tmp_path: Path, elapsed: int
) -> None:
    directory, action, start = setup(tmp_path)
    assert action["expires_at"] == (start + timedelta(seconds=1)).isoformat()
    assert action["remaining_seconds"] == 1
    end = start + timedelta(seconds=elapsed)
    result = sessions.next_action(directory, now=end)
    assert result["finished"] and result["stop_reason"] == "wall-time-budget-or-clock-invalid"
    assert result["wall_time_seconds"] == elapsed
    assert result["wall_time_within_limits"] is (elapsed == 1)
    assert result["budget_within_limits"] is (elapsed == 1)
    assert sessions._load(directory)["lease"]["attempt"] == action["attempt"]
    charged = sessions.record_usage(directory, usage(action, 17), now=end + timedelta(days=1))
    assert charged["actual_model_tokens"] == 17
    assert charged["wall_time_seconds"] == elapsed
    assert charged["needs"][0]["phase"] == "search"
    assert sessions.record_usage(directory, usage(action, 17))["actual_model_tokens"] == 17
    overrun = sessions.record_usage(directory, usage(action, 30000, "late-overrun"))
    assert overrun["stop_reason"] == "model-token-budget"
    assert overrun["wall_time_seconds"] == elapsed
    assert not overrun["budget_within_limits"]


def test_drive_does_not_invoke_a_handler_after_session_deadline(tmp_path: Path) -> None:
    directory, _, _ = setup(tmp_path)
    effects: list[str] = []

    def handler(action: dict[str, Any]) -> dict[str, Any]:
        effects.append(action["action"])
        return receipt(action, {"requests": 0, "candidates": []})

    result = sessions.drive(directory, {"search": handler})
    assert effects == []
    assert result["finished"] and result["wall_time_seconds"] > 1
    assert not result["wall_time_within_limits"] and not result["budget_within_limits"]


def test_attempt_reconciliation_within_session_budget_is_unchanged(tmp_path: Path) -> None:
    directory, action, start = setup(tmp_path, seconds=2700)
    cached = sessions.next_action(directory, now=start + timedelta(seconds=30))
    assert cached["remaining_seconds"] == 2670
    expired = sessions.next_action(directory, now=start + timedelta(minutes=11))
    assert expired["action"] == "reconcile" and expired["attempt"] == action["attempt"]
    assert sessions.report(directory)["wall_time_seconds"] is None


def test_late_accounting_does_not_extend_a_finished_investigation(tmp_path: Path) -> None:
    directory, action, start = setup(tmp_path, seconds=2700)
    result = sessions.stop(directory, "external-dependency", now=start + timedelta(seconds=2))
    assert result["wall_time_seconds"] == 2 and result["wall_time_within_limits"]
    later = sessions.record_usage(directory, usage(action, 29), now=start + timedelta(days=1))
    assert later["finished_at"] == result["finished_at"]
    assert later["wall_time_seconds"] == 2 and later["wall_time_within_limits"]
    assert later["stop_reason"] == "external-dependency" and later["budget_within_limits"]


def test_legacy_terminal_duration_stays_unknown_after_late_accounting(tmp_path: Path) -> None:
    directory, action, start = setup(tmp_path, seconds=2700)
    value = sessions._load(directory)
    value.update(finished=True, stop_reason="external-dependency")
    value.pop("finished_at")
    value.pop("wall_time_seconds")
    sessions._save(directory, value)
    result = sessions.record_usage(directory, usage(action, 31), now=start + timedelta(days=1))
    assert result["actual_model_tokens"] == 31
    assert result["wall_time_seconds"] is result["wall_time_within_limits"] is None


def test_late_actual_result_preserves_terminal_time_without_granting_work(tmp_path: Path) -> None:
    directory, action, start = setup(tmp_path, seconds=2700)
    ended = sessions.stop(directory, "external-dependency", now=start + timedelta(seconds=2))
    entry = receipt(action, {"requests": 0, "candidates": []})
    entry["model_tokens"] = 37
    result = sessions.record(directory, entry, now=start + timedelta(days=1))
    assert result["actual_model_tokens"] == 37
    assert result["wall_time_seconds"] == 2 and result["finished_at"] == ended["finished_at"]
    assert result["stop_reason"] == "external-dependency" and result["budget_within_limits"]
    assert sessions.next_action(directory, now=start + timedelta(days=2))["finished"]
