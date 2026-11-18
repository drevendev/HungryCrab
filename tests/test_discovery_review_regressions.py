"""Regressions from the PR #677 adversarial review; owned meters only."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def owned_session(tmp_path: Path) -> Iterator[Any]:
    from discovery_host_fixture import OwnedMeter
    from test_discovery_usage import setup_session

    from hungry_crab.discovery_host import host_meter

    meter = OwnedMeter()
    with host_meter(meter):
        directory, _, action, instant = setup_session(tmp_path)
        yield directory, action, instant, meter


def test_control_known_model_use_and_known_cap_violation(owned_session: Any) -> None:
    from hungry_crab import discovery_session as sessions

    directory, action, instant, meter = owned_session
    meter.tokens = 101
    sessions.execute_operation(
        directory,
        action["attempt"],
        "known-overrun",
        lambda _: None,
        kind="model",
        model_token_limit=100,
        now=instant,
    )
    result = sessions.report(directory)
    assert result["actual_model_tokens"] == 101
    assert result["budget_within_limits"] is False


def test_late_actual_usage_must_enforce_the_original_operation_cap(owned_session: Any) -> None:
    from hungry_crab import discovery_session as sessions

    directory, action, instant, meter = owned_session
    meter.tokens = None
    sessions.execute_operation(
        directory,
        action["attempt"],
        "late-overrun",
        lambda _: None,
        kind="model",
        model_token_limit=100,
        now=instant,
    )
    original = next(
        event["record"]["receipt"]
        for event in sessions._load(directory)["events"]
        if event["record"]["kind"] == "model-usage"
    )
    result = sessions.record_usage(directory, {**original, "model_tokens": 101})
    assert result["actual_model_tokens"] == 101
    assert result["budget_within_limits"] is False, result


def test_stopped_in_flight_model_cost_must_be_unknown(owned_session: Any) -> None:
    from hungry_crab import discovery_session as sessions

    directory, action, instant, meter = owned_session
    meter.tokens = 23
    observed: dict[str, Any] = {}

    def invoke(_: int) -> None:
        sessions.stop(directory, "external-dependency", now=instant)
        observed.update(sessions.report(directory))

    sessions.execute_operation(
        directory,
        action["attempt"],
        "in-flight",
        invoke,
        kind="model",
        model_token_limit=100,
        now=instant,
    )
    assert sessions.report(directory)["actual_model_tokens"] == 23
    assert observed["actual_model_tokens"] is None, observed
    assert observed["model_usage_complete"] is False, observed


def test_orphaned_model_cost_must_not_be_reported_as_zero(owned_session: Any) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import host_meter

    directory, action, instant, _ = owned_session

    class InterruptedMeter(OwnedMeter):
        def finish(self, ticket: object) -> int | None:
            raise KeyboardInterrupt("owned interruption of native usage retrieval")

    with host_meter(InterruptedMeter()), pytest.raises(KeyboardInterrupt):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "orphan",
            lambda _: None,
            kind="model",
            model_token_limit=100,
            now=instant,
        )
    assert sessions.next_action(directory, now=instant)["action"] == "reconcile"
    report = sessions.report(directory)
    assert report["actual_model_tokens"] is None, report
    assert report["model_usage_complete"] is False, report


def test_successful_begin_with_none_ticket_must_still_finish(owned_session: Any) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import host_meter

    directory, action, instant, _ = owned_session
    finished: list[object] = []

    class StatelessMeter(OwnedMeter):
        def begin(self, operation: str, token_limit: int) -> object:
            return None

        def finish(self, ticket: object) -> int | None:
            finished.append(ticket)
            return 23

    with host_meter(StatelessMeter()):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "stateless",
            lambda _: None,
            kind="model",
            model_token_limit=100,
            now=instant,
        )
    assert finished == [None]
    assert sessions.report(directory)["actual_model_tokens"] == 23


def test_expiry_during_meter_preflight_must_refuse_dispatch(
    owned_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import MeterIdentity, host_meter
    from hungry_crab.errors import CrabError

    directory, action, instant, _ = owned_session
    clock = [instant]
    effects: list[int] = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return clock[0]

    class SlowPreflight(OwnedMeter):
        def preflight(self) -> MeterIdentity:
            clock[0] = instant + timedelta(minutes=11)
            return super().preflight()

    monkeypatch.setattr(sessions, "datetime", Clock)
    with host_meter(SlowPreflight()), pytest.raises(CrabError, match="expired"):
        sessions.execute_operation(directory, action["attempt"], "expired", effects.append)
    assert effects == [], {"effects": effects, "clock": clock[0], "expires": action["expires_at"]}


@pytest.mark.parametrize("path", ["next", "authorize", "record"])
def test_other_admission_paths_refresh_time_after_preflight(
    owned_session: Any, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from discovery_host_fixture import OwnedMeter
    from test_release_069_discovery import receipt

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import MeterIdentity, host_meter
    from hungry_crab.errors import CrabError

    directory, action, instant, _ = owned_session
    clock = [instant]

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return clock[0]

    class SlowPreflight(OwnedMeter):
        def preflight(self) -> MeterIdentity:
            clock[0] = instant + timedelta(minutes=11)
            return super().preflight()

    monkeypatch.setattr(sessions, "datetime", Clock)
    before = (directory / "session.json").read_bytes()
    with host_meter(SlowPreflight()):
        if path == "next":
            result = sessions.next_action(directory)
            assert result["action"] == "reconcile"
            assert result["attempt"] == action["attempt"]
        else:
            with pytest.raises(CrabError, match="expired"):
                if path == "authorize":
                    sessions.authorize_request(directory, action["attempt"], "expired")
                else:
                    sessions.record(directory, receipt(action, {"requests": 0, "candidates": []}))
    assert (directory / "session.json").read_bytes() == before


def test_native_begin_cannot_extend_the_original_lease(
    owned_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import host_meter
    from hungry_crab.errors import CrabError

    directory, action, instant, _ = owned_session
    clock = [instant]
    effects: list[int] = []
    finished: list[object] = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return clock[0]

    class SlowBegin(OwnedMeter):
        def begin(self, operation: str, token_limit: int) -> object:
            clock[0] = instant + timedelta(minutes=11)
            return None

        def finish(self, ticket: object) -> int | None:
            finished.append(ticket)
            return 0

    monkeypatch.setattr(sessions, "datetime", Clock)
    with host_meter(SlowBegin()), pytest.raises(CrabError, match="expired"):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "slow-begin",
            effects.append,
            kind="model",
            model_token_limit=100,
        )
    assert not effects
    assert finished == [None]
    assert sessions.report(directory)["actual_model_tokens"] == 0
    assert sessions.next_action(directory)["action"] == "reconcile"


def test_stop_during_native_begin_prevents_dispatch_and_preserves_cost(owned_session: Any) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import host_meter
    from hungry_crab.errors import CrabError

    directory, action, instant, _ = owned_session
    effects: list[int] = []

    class StoppedBegin(OwnedMeter):
        def begin(self, operation: str, token_limit: int) -> object:
            sessions.stop(directory, "external-dependency", now=instant)
            return None

        def finish(self, ticket: object) -> int | None:
            return 23

    with host_meter(StoppedBegin()), pytest.raises(CrabError, match="finished discovery session"):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "stopped-begin",
            effects.append,
            kind="model",
            model_token_limit=100,
            now=instant,
        )
    result = sessions.report(directory)
    assert not effects
    assert result["actual_model_tokens"] == 23
    assert result["stop_reason"] == "external-dependency"
    assert result["finished_at"] == instant.isoformat()


def test_historical_late_cap_violation_is_reported_without_rewriting(owned_session: Any) -> None:
    from hungry_crab import discovery_session as sessions

    directory, action, instant, meter = owned_session
    meter.tokens = None
    sessions.execute_operation(
        directory,
        action["attempt"],
        "historic-overrun",
        lambda _: None,
        kind="model",
        model_token_limit=100,
        now=instant,
    )
    original = next(
        event["record"]["receipt"]
        for event in sessions._load(directory)["events"]
        if event["record"]["kind"] == "model-usage"
    )
    actual = {**original, "model_tokens": 101}
    sessions.record_usage(directory, actual, now=instant)
    value = sessions._load(directory)
    value.pop("meter_cap_violated")
    sessions._save(directory, value)
    before = (directory / "session.json").read_bytes()
    assert sessions.report(directory)["budget_within_limits"] is False
    repeated = sessions.record_usage(directory, actual, now=instant)
    assert repeated["actual_model_tokens"] == 101
    assert repeated["budget_within_limits"] is False
    assert (directory / "session.json").read_bytes() == before


def test_interrupted_host_callback_requires_original_attempt_reconciliation(
    owned_session: Any,
) -> None:
    from hungry_crab import discovery_session as sessions
    from hungry_crab.errors import CrabError

    directory, action, instant, _ = owned_session
    assert sessions.next_action(directory, now=instant)["action"] == "search"
    invoked: list[str] = []

    def interrupted(row: dict[str, Any]) -> dict[str, Any]:
        invoked.append(row["attempt"])
        raise RuntimeError("synthetic callback interruption")

    with pytest.raises(RuntimeError, match="synthetic callback interruption"):
        sessions.drive(directory, {"search": interrupted}, max_actions=1)

    state = sessions._load(directory)
    assert state["lease"]["attempt"] == action["attempt"]
    assert state["spent"]["requests"] == 1
    permits = [
        event["record"]
        for event in state["events"]
        if event["record"].get("kind") == "host-handler-dispatch-authorized"
    ]
    assert len(permits) == 1 and permits[0]["attempt"] == action["attempt"]
    assert not any(
        event["record"].get("kind") == "provider-request-authorized"
        for event in state["events"]
    )
    before = (directory / "session.json").read_bytes()

    pending = sessions.next_action(directory, now=instant)
    assert pending["action"] == "reconcile"
    assert pending["attempt"] == action["attempt"]
    assert "host callback already dispatched" in pending["reason"]
    resumed = sessions.drive(directory, {"search": interrupted}, max_actions=1)
    assert resumed["status"] == "reconciliation-required"
    assert resumed["pending"]["attempt"] == action["attempt"]
    assert invoked == [action["attempt"]]
    with pytest.raises(CrabError, match="already dispatched"):
        sessions.authorize_host_dispatch(directory, action["attempt"], "search")
    assert (directory / "session.json").read_bytes() == before


def test_completed_host_callback_receipt_does_not_leave_reconcile_lease(
    owned_session: Any,
) -> None:
    from test_release_069_discovery import receipt

    from hungry_crab import discovery_session as sessions

    directory, action, instant, _ = owned_session
    invoked: list[str] = []

    def completed(row: dict[str, Any]) -> dict[str, Any]:
        invoked.append(row["attempt"])
        return receipt(row, {"requests": 0, "candidates": []})

    result = sessions.drive(directory, {"search": completed}, max_actions=1)
    assert result["status"] == "host-action-limit"
    state = sessions._load(directory)
    assert state["lease"] is None
    assert state["spent"]["requests"] == 1
    assert invoked == [action["attempt"]]
    assert any(
        event["record"].get("kind") == "action-result"
        for event in state["events"]
    )

    later = sessions.next_action(directory, now=instant)
    assert later.get("action") != "reconcile"
    if not later.get("finished"):
        assert later["attempt"] != action["attempt"]
