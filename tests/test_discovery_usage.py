"""Actual cost survives semantic rejection, expired leases and stopped sessions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_discovery_inputs import repository
from test_release_069_discovery import plan, receipt

from hungry_crab import discovery_session as sessions
from hungry_crab.cli import main
from hungry_crab.errors import CrabError


def setup_session(tmp_path: Path) -> tuple[Path, Path, dict[str, Any], datetime]:
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    directory = tmp_path / "session"
    now = datetime.now(UTC)
    sessions.initialize(directory, maw, plan(maw), now=now)
    return directory, maw, sessions.next_action(directory, now=now), now


def usage(
    action: dict[str, Any], tokens: int | None, operation: str = "model-stage-1"
) -> dict[str, Any]:
    return {
        "operation": operation,
        "attempt": action["attempt"],
        "author": "fixture host",
        "tool": "actual native usage fixture",
        "model_tokens": tokens,
    }


def test_refused_action_still_has_exact_cost_without_advancing(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    invalid = receipt(action, {"requests": 2})
    invalid["model_tokens"] = 73
    with pytest.raises(CrabError, match="one leased search"):
        sessions.record(directory, invalid, now=now)
    before = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    result = sessions.record_usage(directory, usage(action, 73), now=now)
    after = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    assert result["actual_model_tokens"] == 73
    assert after["needs"] == before["needs"] and after["lease"] == before["lease"]
    assert sessions.next_action(directory, now=now)["remaining_model_tokens"] == 30000 - 73
    assert after["events"][-1]["record"]["kind"] == "model-usage"
    assert not result["published"] and not result["acceptance_learning_written"]
    # Zero additional model use accompanies the subsequent deterministic effect.
    sessions.record(directory, receipt(action, {"requests": 0, "candidates": []}), now=now)
    assert sessions.report(directory)["actual_model_tokens"] == 73


def test_usage_retry_is_idempotent_but_changed_payload_is_not(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    original = usage(action, 41)
    sessions.record_usage(directory, original, now=now)
    raw = (directory / "session.json").read_bytes()
    assert sessions.record_usage(directory, original, now=now)["actual_model_tokens"] == 41
    assert (directory / "session.json").read_bytes() == raw
    with pytest.raises(CrabError, match="different receipt"):
        sessions.record_usage(directory, {**original, "model_tokens": 42}, now=now)
    with pytest.raises(CrabError, match="known discovery attempt"):
        sessions.record_usage(directory, {**original, "operation": "new", "attempt": "other"})
    assert (directory / "session.json").read_bytes() == raw


def test_expired_stopped_and_stale_receiving_cost_cannot_grant_work(tmp_path: Path) -> None:
    directory, maw, action, now = setup_session(tmp_path)
    expired = now + timedelta(minutes=11)
    assert sessions.next_action(directory, now=expired)["action"] == "reconcile"
    sessions.stop(directory, "external-dependency")
    (maw / "app.py").write_text("changed receiving code\n", encoding="utf-8")
    result = sessions.record_usage(directory, usage(action, 200), now=expired)
    assert result["finished"] and result["stop_reason"] == "external-dependency"
    assert result["actual_model_tokens"] == 200
    assert not result["scope_complete"] and result["needs"][0]["phase"] == "search"
    with pytest.raises(CrabError, match="receiving context changed"):
        sessions.next_action(directory, now=expired)


def test_overrun_is_retained_and_unknown_use_is_not_zero(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    result = sessions.record_usage(directory, usage(action, 30007), now=now)
    assert result["spent"]["model_tokens"] == 30007
    assert result["finished"] and result["stop_reason"] == "model-token-budget"
    assert not result["budget_within_limits"]
    assert sessions.next_action(directory, now=now)["finished"]
    unknown = sessions.record_usage(directory, usage(action, None, "interrupted-stage"), now=now)
    assert unknown["actual_model_tokens"] is None and not unknown["model_usage_complete"]
    assert unknown["spent"]["model_tokens"] == 30007
    assert not unknown["budget_within_limits"]


def test_public_usage_command_and_event_budget_leave_readable_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, _, action, now = setup_session(tmp_path)
    source = tmp_path / "usage.json"
    source.write_text(json.dumps(usage(action, 17)), encoding="utf-8")
    assert main(["discover", "usage", str(directory), "--input", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["actual_model_tokens"] == 17
    value = sessions._load(directory)
    while len(value["events"]) < 500:
        sessions._event(value, {"kind": "bounded-fixture-event"})
    sessions._save(directory, value)
    original = (directory / "session.json").read_bytes()
    with pytest.raises(CrabError, match="event budget exhausted"):
        sessions.record_usage(directory, usage(action, 1, "after-event-limit"), now=now)
    assert (directory / "session.json").read_bytes() == original
    assert sessions.report(directory)["actual_model_tokens"] == 17


@pytest.mark.parametrize("tokens", [-1, True, "unavailable"])
def test_invalid_usage_does_not_change_state(tmp_path: Path, tokens: object) -> None:
    directory, _, action, _ = setup_session(tmp_path)
    payload = usage(action, 0)
    payload["model_tokens"] = tokens
    raw = (directory / "session.json").read_bytes()
    with pytest.raises(CrabError, match="actual nonnegative"):
        sessions.record_usage(directory, payload)
    assert (directory / "session.json").read_bytes() == raw


def test_public_reserve_stop_keeps_unused_budget_and_late_costs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, _, action, now = setup_session(tmp_path)
    sessions.record_usage(directory, usage(action, 71), now=now)
    assert main(["discover", "stop", str(directory), "--reason", "model-budget-reserve"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["finished"] and result["stop_reason"] == "model-budget-reserve"
    assert result["actual_model_tokens"] == 71 and result["budget_within_limits"]
    assert not result["scope_complete"] and result["needs"][0]["phase"] == "search"
    raw = (directory / "session.json").read_bytes()
    assert sessions.stop(directory, "model-budget-reserve")["actual_model_tokens"] == 71
    assert (directory / "session.json").read_bytes() == raw
    late = sessions.record_usage(directory, usage(action, 29, "actual-late-receipt"))
    assert late["actual_model_tokens"] == 100 and late["stop_reason"] == "model-budget-reserve"
    assert late["finished_at"] == result["finished_at"]
    effects = []
    sessions.drive(directory, {"search": lambda value: effects.append(value)})
    assert effects == [] and sessions._load(directory)["lease"]["attempt"] == action["attempt"]
