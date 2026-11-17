"""Full PR #677 review probes; synthetic usage, no remote provider or model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from discovery_host_fixture import OwnedMeter
from test_discovery_usage import setup_session, usage

from hungry_crab import discovery_session as sessions
from hungry_crab.discovery_host import host_meter
from hungry_crab.errors import CrabError


@pytest.mark.parametrize("legacy", [False, True])
def test_partial_reconciliation_keeps_every_original_unknown(
    tmp_path: Path, legacy: bool
) -> None:
    with host_meter(OwnedMeter()):
        directory, _, action, instant = setup_session(tmp_path)
    original = usage(action, None, "original-unknown")
    stopped = sessions.record_usage(directory, original, now=instant)
    if legacy:
        # The pre-broker schema stored the false flag and receipt, without an
        # unresolved-operation list. It remains readable accounting evidence.
        value = sessions._load(directory)
        value.pop("unresolved_usage")
        value.pop("meter_identity")
        value["plan"].pop("host_metering")
        sessions._save(directory, value)
    newer = usage(action, None, "second-unknown")
    sessions.record_usage(directory, newer, now=instant)
    events = sessions._load(directory)["events"]
    partial = sessions.record_usage(directory, {**newer, "model_tokens": 11}, now=instant)
    assert partial["spent"]["model_tokens"] == 11
    assert partial["actual_model_tokens"] is None, partial
    assert partial["model_usage_complete"] is False, partial
    assert partial["budget_arithmetic_within_limits"] is None, partial
    assert sessions._load(directory)["events"][: len(events)] == events
    assert partial["stop_reason"] == stopped["stop_reason"]
    assert partial["finished_at"] == stopped["finished_at"]
    completed = sessions.record_usage(directory, {**original, "model_tokens": 7}, now=instant)
    assert completed["actual_model_tokens"] == 18
    assert completed["finished"]
    before = (directory / "session.json").read_bytes()
    assert sessions.record_usage(directory, {**original, "model_tokens": 7}) == completed
    assert (directory / "session.json").read_bytes() == before


@pytest.mark.parametrize("unknown", [False, True])
def test_late_accounting_cannot_spend_in_flight_settlement_capacity(
    tmp_path: Path, unknown: bool
) -> None:
    meter = OwnedMeter()
    meter.tokens = None if unknown else 17
    with host_meter(meter):
        directory, _, action, instant = setup_session(tmp_path)
        value = sessions._load(directory)
        while len(value["events"]) < 492:
            sessions._event(value, {"kind": "owned-capacity-fixture"})
        sessions._save(directory, value)
        accepted: list[int] = []
        refused: list[int] = []

        def invoke(_: int) -> None:
            for index in range(10):
                before = (directory / "session.json").read_bytes()
                try:
                    sessions.record_usage(
                        directory, usage(action, 0, f"late-accounting-{index}"), now=instant
                    )
                    accepted.append(index)
                except CrabError:
                    refused.append(index)
                    assert (directory / "session.json").read_bytes() == before

        sessions.execute_operation(
            directory,
            action["attempt"],
            "original-in-flight",
            invoke,
            kind="model",
            model_token_limit=100,
            now=instant,
        )
    assert accepted and refused
    result = sessions.report(directory)
    assert result["spent"]["model_tokens"] == (0 if unknown else 17)
    assert result["actual_model_tokens"] == (None if unknown else 17)
    assert not sessions._pending_operations(sessions._load(directory))
    assert len(sessions._load(directory)["events"]) <= 500
    if unknown:
        assert result["finished"] and result["stop_reason"] == "usage-unavailable"


def test_trusted_usage_only_recovery_retains_its_terminal_slot(tmp_path: Path) -> None:
    with host_meter(OwnedMeter()):
        directory, _, action, instant = setup_session(tmp_path)
        operation = action["attempt"] + ":original"
        value = sessions._load(directory)
        sessions._event(
            value,
            {
                "kind": "operation-started",
                "attempt": action["attempt"],
                "operation": operation,
                "operation_kind": "model",
                "model_token_limit": 100,
                "at": instant.isoformat(),
            },
        )
        sessions._save(directory, value)
        original: dict[str, Any] = {
            "attempt": action["attempt"],
            "operation": operation,
            "author": "trusted host operation broker",
            "tool": "native meter boundary",
            "model_tokens": 17,
        }
        sessions.record_usage(directory, original, now=instant)
        value = sessions._load(directory)
        while len(value["events"]) < 495:
            sessions._event(value, {"kind": "owned-capacity-fixture"})
        sessions._save(directory, value)
        for index in range(10):
            try:
                sessions.record_usage(
                    directory, usage(action, 0, f"recovery-{index}"), now=instant
                )
            except CrabError:
                break
        sessions.reconcile_trusted_operation(
            directory, action["attempt"], operation, 17, now=instant
        )
        assert sessions.report(directory)["actual_model_tokens"] == 17
        assert not sessions._pending_operations(sessions._load(directory))
