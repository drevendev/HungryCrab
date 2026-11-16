"""Real callback boundaries with owned, model-free metering and provider ports."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from discovery_host_fixture import OwnedMeter, provider_call
from test_discovery_usage import setup_session, usage
from test_release_069_discovery import plan, receipt
from test_discovery_inputs import repository

from hungry_crab import discovery_session as sessions
from hungry_crab.cli import main
from hungry_crab.discovery_host import host_meter
from hungry_crab.discovery_search import budget_for_plan, search
from hungry_crab.errors import CrabError

pytestmark = pytest.mark.usefixtures("owned_discovery_host")


def test_plan_alone_cannot_admit_strict_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan(maw)), encoding="utf-8")
    directory = tmp_path / "session"
    with host_meter(None):
        assert main(["discover", "init", str(directory), "--maw", str(maw), "--plan", str(source)]) == 1
    assert "trusted live host metering" in capsys.readouterr().err
    assert not directory.exists()


def test_unavailable_meter_refuses_before_directory_or_callback(tmp_path: Path) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    meter = OwnedMeter()
    meter.available = False
    with host_meter(meter), pytest.raises(CrabError, match="preflight is unavailable"):
        sessions.initialize(tmp_path / "refused", maw, plan(maw))
    assert not (tmp_path / "refused").exists()
    directory, _, action, _ = setup_session(tmp_path / "active")
    before = (directory / "session.json").read_bytes()
    effects: list[int] = []
    with host_meter(meter), pytest.raises(CrabError, match="preflight is unavailable"):
        sessions.execute_operation(directory, action["attempt"], "refused", effects.append)
    assert effects == [] and (directory / "session.json").read_bytes() == before


def test_provider_effect_sees_admission_and_cannot_replay(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    effects: list[str] = []

    def invoke(_: int) -> str:
        records = [event["record"] for event in sessions._load(directory)["events"]]
        assert records[-1]["kind"] == "operation-started"
        assert records[-2]["kind"] == "provider-request-authorized"
        effects.append("sent")
        return "response"

    assert sessions.execute_operation(directory, action["attempt"], "search", invoke, now=now) == "response"
    with pytest.raises(CrabError, match="already admitted"):
        sessions.execute_operation(directory, action["attempt"], "search", invoke, now=now)
    assert effects == ["sent"]
    result = sessions.record(directory, receipt(action, {"candidates": []}), now=now)
    assert result["spent"]["requests"] == 1


def test_raw_authorization_and_semantic_usage_cannot_replace_broker(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    sessions.authorize_request(directory, action["attempt"], "raw-permit", now=now)
    # A manually supplied usage receipt is accounting only, not trusted settlement.
    sessions.record_usage(directory, usage(action, 0, "raw-permit"), now=now)
    with pytest.raises(CrabError, match="trusted host settlement"):
        sessions.record(directory, receipt(action, {"candidates": []}), now=now)
    other, _, leased, instant = setup_session(tmp_path / "other")
    forged = receipt(leased, {"requests": 0, "candidates": []})
    forged["model_tokens"] = 17
    with pytest.raises(CrabError, match="semantic receipts must charge zero"):
        sessions.record(other, forged, now=instant)


def test_model_meter_accounts_exception_before_semantic_interpretation(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    meter = OwnedMeter()
    meter.tokens = 23

    def fail(allowance: int) -> None:
        assert allowance == 100
        raise RuntimeError("owned invocation failed after usage")

    with host_meter(meter), pytest.raises(RuntimeError, match="after usage"):
        sessions.execute_operation(directory, action["attempt"], "model", fail,
            kind="model", model_token_limit=100, now=now)
    result = sessions.report(directory)
    assert result["actual_model_tokens"] == 23
    assert result["needs"][0]["phase"] == "search"
    assert result["budget_within_limits"] is None
    assert sessions._load(directory)["lease"]["attempt"] == action["attempt"]


def test_nested_model_reservations_share_cap_and_provider_dispatch(tmp_path: Path) -> None:
    directory, _, search_action, now = setup_session(tmp_path)
    provider_call(directory, search_action["attempt"], "search", now=now)
    sessions.record(directory, receipt(search_action, {"candidates": [{"repository": "one/tool"}]}), now=now)
    action = sessions.next_action(directory, now=now)
    effects: list[str] = []

    def outer(_: int) -> None:
        with pytest.raises(CrabError, match="shared discovery model token allowance"):
            sessions.execute_operation(directory, action["attempt"], "too-large", lambda _: effects.append("denied"),
                kind="model", model_token_limit=15001, now=now)
        sessions.execute_operation(directory, action["attempt"], "nested-provider", lambda _: effects.append("provider"), now=now)
        sessions.execute_operation(directory, action["attempt"], "nested-model", lambda _: effects.append("model"),
            kind="model", model_token_limit=15000, now=now)

    sessions.execute_operation(directory, action["attempt"], "outer", outer,
        kind="model", model_token_limit=15000, now=now)
    assert effects == ["provider", "model"]
    assert sessions.report(directory)["spent"]["requests"] == 2


def test_unknown_then_late_actual_cost_is_append_only_and_never_resumes(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    meter = OwnedMeter()
    meter.tokens = None
    with host_meter(meter):
        sessions.execute_operation(directory, action["attempt"], "uncertain", lambda _: None,
            kind="model", model_token_limit=100, now=now)
    stopped = sessions.report(directory)
    assert stopped["finished"] and stopped["actual_model_tokens"] is None
    events = sessions._load(directory)["events"]
    original = next(row["record"]["receipt"] for row in events if row["record"]["kind"] == "model-usage")
    actual = {**original, "model_tokens": 37}
    reconciled = sessions.record_usage(directory, actual, now=now + timedelta(days=1))
    assert reconciled["actual_model_tokens"] == 37
    assert reconciled["finished_at"] == stopped["finished_at"]
    assert reconciled["stop_reason"] == "usage-unavailable"
    assert sessions._load(directory)["events"][:len(events)] == events
    before = (directory / "session.json").read_bytes()
    assert sessions.record_usage(directory, actual)["actual_model_tokens"] == 37
    assert (directory / "session.json").read_bytes() == before
    with pytest.raises(CrabError, match="finished discovery session"):
        provider_call(directory, action["attempt"], "late-new-effect")


def test_late_stop_and_stale_receiving_keep_cost_but_block_nested_effect(tmp_path: Path) -> None:
    directory, maw, action, now = setup_session(tmp_path)
    meter = OwnedMeter()
    meter.tokens = 19
    effects: list[int] = []

    def invoke(_: int) -> None:
        sessions.stop(directory, "external-dependency", now=now)
        with pytest.raises(CrabError, match="finished discovery session"):
            sessions.execute_operation(directory, action["attempt"], "nested", effects.append, now=now)
        (maw / "app.py").write_text("changed\n", encoding="utf-8")

    with host_meter(meter):
        sessions.execute_operation(directory, action["attempt"], "late", invoke,
            kind="model", model_token_limit=100, now=now)
    assert not effects
    result = sessions.report(directory)
    assert result["finished"] and result["actual_model_tokens"] == 19
    assert result["needs"][0]["phase"] == "search"


def test_expired_port_and_native_overrun_fail_closed(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    effects: list[int] = []
    with pytest.raises(CrabError, match="expired before host operation"):
        sessions.execute_operation(directory, action["attempt"], "expired", effects.append, now=now + timedelta(minutes=11))
    assert not effects
    meter = OwnedMeter()
    meter.tokens = 101
    with host_meter(meter):
        sessions.execute_operation(directory, action["attempt"], "overrun", lambda _: None,
            kind="model", model_token_limit=100, now=now)
    result = sessions.report(directory)
    assert result["actual_model_tokens"] == 101 and result["finished"]
    assert result["budget_within_limits"] is False


def test_session_search_wraps_actual_provider_port(tmp_path: Path) -> None:
    directory, _, action, _ = setup_session(tmp_path)
    calls: list[str] = []

    class Client:
        def get(self, path: str, *, allow_missing: bool = False) -> Any:
            calls.append(path)
            assert sessions._load(directory)["events"][-1]["record"]["kind"] == "operation-started"
            return {"items": []}

    budget = budget_for_plan({"session": str(directory), "attempt": action["attempt"], "operation": "search"})
    assert search("owned", client=Client(), budget=budget)["requests"] == 1
    assert len(calls) == 1
    with pytest.raises(CrabError, match="one leased search"):
        search("second", client=Client(), budget=budget)
    assert len(calls) == 1
