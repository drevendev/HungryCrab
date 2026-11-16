"""Actual cost survives semantic rejection, expired leases and stopped sessions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from discovery_host_fixture import provider_call
from test_discovery_inputs import repository
from test_release_069_discovery import plan, receipt

from hungry_crab import discovery_session as sessions
from hungry_crab.cli import main
from hungry_crab.errors import CrabError

pytestmark = pytest.mark.usefixtures("owned_discovery_host")


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


def test_host_self_attestation_never_certifies_strict_budget(tmp_path: Path) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    directory = tmp_path / "session"
    now = datetime.now(UTC)
    initial = sessions.initialize(directory, maw, plan(maw), now=now)
    assert initial["host_metering_admission"] == "runtime-admitted-owned-fixture"
    assert initial["budget_arithmetic_within_limits"] is True
    assert initial["budget_within_limits"] is None

    action = sessions.next_action(directory, now=now)
    charged = sessions.record_usage(directory, usage(action, 31), now=now)
    assert charged["actual_model_tokens"] == 31
    assert charged["budget_arithmetic_within_limits"] is True
    assert charged["budget_within_limits"] is None
    assert sessions.report(directory)["budget_within_limits"] is None

    unknown = sessions.record_usage(directory, usage(action, None, "late-unknown"), now=now)
    assert unknown["budget_arithmetic_within_limits"] is None
    assert unknown["budget_within_limits"] is None


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
    assert result["actual_model_tokens"] == 71
    assert result["budget_arithmetic_within_limits"] is True
    assert result["budget_within_limits"] is None
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


@pytest.mark.parametrize(
    "change",
    [
        {"missing": True},
        {"preflight_confirmed": False},
        {"preflight_confirmed": "yes"},
        {"actual_model_usage": "estimated"},
        {"reporting_source": "unknown"},
        {"reporting_source": "ACTUAL_HOST_METER"},
        {"route": "supervised"},
        {"extra": "unexpected"},
    ],
)
def test_missing_or_invalid_host_metering_refuses_session_before_effects(
    tmp_path: Path, change: dict[str, Any]
) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    proposal = plan(maw)
    if change.pop("missing", False):
        proposal.pop("host_metering")
    else:
        proposal["host_metering"].update(change)
    directory = tmp_path / "session"
    with pytest.raises(CrabError, match="host metering"):
        sessions.initialize(directory, maw, proposal)
    assert not directory.exists()


def test_legacy_unqualified_lease_only_reconciles_without_new_provider_effects(
    tmp_path: Path,
) -> None:
    directory, _, action, now = setup_session(tmp_path)
    original = sessions._load(directory)
    original["plan"].pop("host_metering")
    sessions._save(directory, original)
    before = (directory / "session.json").read_bytes()

    pending = sessions.next_action(directory, now=now)
    assert pending["action"] == "reconcile"
    assert pending["attempt"] == action["attempt"]
    with pytest.raises(CrabError, match="host metering"):
        provider_call(directory, action["attempt"], "legacy-provider-call")
    assert (directory / "session.json").read_bytes() == before

    # Even an explicitly supplied "reconcile" handler must not execute through
    # drive(): a legacy/uncertain attempt is settlement-only, not fresh work.
    executed: list[str] = []

    def forbidden_handler(row: dict[str, Any]) -> dict[str, Any]:
        executed.append(row["attempt"])
        return receipt(row, {"requests": 0, "candidates": []})

    pending = sessions.drive(directory, {"reconcile": forbidden_handler})
    assert pending["status"] == "reconciliation-required"
    assert pending["pending"]["attempt"] == action["attempt"]
    assert executed == []
    assert (directory / "session.json").read_bytes() == before

    # The exact old charge still belongs to the original attempt and is allowed
    # to settle without opening any new search or leasing a replacement.
    charged = sessions.record_usage(directory, usage(action, 31), now=now)
    assert charged["actual_model_tokens"] == 31
    assert charged["spent"]["requests"] == 1
    assert charged["host_metering_admission"] == "legacy-unqualified"
    assert charged["budget_within_limits"] is None
    reread = sessions.report(directory)
    assert reread["host_metering_admission"] == "legacy-unqualified"
    assert reread["budget_within_limits"] is None
    assert reread["actual_model_tokens"] == 31
    assert sessions.next_action(directory, now=now)["action"] == "reconcile"


def test_drive_authorizes_host_dispatch_and_blocks_uncertain_replay(tmp_path: Path) -> None:
    directory, _, action, _ = setup_session(tmp_path)
    dispatched: list[str] = []

    def uncertain_host(row: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(row["attempt"])
        current = sessions._load(directory)
        assert any(
            event["record"].get("kind") == "host-handler-dispatch-authorized"
            and event["record"].get("attempt") == row["attempt"]
            and event["record"].get("phase") == "search"
            for event in current["events"]
        )
        raise RuntimeError("original host effect uncertain")

    with pytest.raises(RuntimeError, match="original host effect uncertain"):
        sessions.drive(directory, {"search": uncertain_host}, max_actions=1)
    assert dispatched == [action["attempt"]]
    assert sessions.report(directory)["spent"]["requests"] == 1
    after_first = (directory / "session.json").read_bytes()
    with pytest.raises(CrabError, match="already dispatched; reconcile"):
        sessions.drive(directory, {"search": uncertain_host}, max_actions=1)
    assert dispatched == [action["attempt"]]
    assert (directory / "session.json").read_bytes() == after_first


def test_search_receipt_needs_prior_provider_operation_authorization(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    receipt_without_auth = receipt(action, {"candidates": [{"repository": "one/tool"}]})
    before = (directory / "session.json").read_bytes()
    with pytest.raises(CrabError, match="prior operation authorization"):
        sessions.record(directory, receipt_without_auth, now=now)
    assert (directory / "session.json").read_bytes() == before
    assert sessions.report(directory)["needs"][0]["phase"] == "search"

    # The single prepaid search operation is authorized before its effect.
    provider_call(directory, action["attempt"], "fixture-provider-search", now=now)
    result = sessions.record(directory, receipt_without_auth, now=now)
    assert result["spent"]["requests"] == 1
    assert result["needs"][0]["phase"] == "acquire"
    assert sessions.record(directory, receipt_without_auth, now=now)["spent"]["requests"] == 1


def test_zero_request_search_cannot_hide_previously_authorized_provider_call(
    tmp_path: Path,
) -> None:
    directory, _, action, now = setup_session(tmp_path)
    provider_call(directory, action["attempt"], "fixture-provider-search", now=now)
    before = (directory / "session.json").read_bytes()
    with pytest.raises(CrabError, match="cannot be reported as zero requests"):
        sessions.record(
            directory,
            receipt(action, {"requests": 0, "candidates": []}),
            now=now,
        )
    assert (directory / "session.json").read_bytes() == before
    assert sessions.report(directory)["spent"]["requests"] == 1


def test_non_search_receipt_reconciles_all_authorized_provider_calls(tmp_path: Path) -> None:
    directory, _, search, now = setup_session(tmp_path)
    provider_call(directory, search["attempt"], "fixture-search", now=now)
    sessions.record(
        directory,
        receipt(search, {"candidates": [{"repository": "one/tool"}]}),
        now=now,
    )
    acquire = sessions.next_action(directory, now=now)
    assert acquire["action"] == "acquire"
    assert sessions.report(directory)["spent"]["requests"] == 1

    # An invented provider request cannot be backfilled by a semantic receipt.
    without_auth = (directory / "session.json").read_bytes()
    with pytest.raises(CrabError, match="non-search provider request count"):
        forged = receipt(acquire, {"status": "unavailable", "requests": 1})
        sessions.record(directory, forged, now=now)
    assert (directory / "session.json").read_bytes() == without_auth

    # Once an external request was authorized, omitting or underreporting it
    # must not complete the leased acquire action as if it were local-only.
    provider_call(directory, acquire["attempt"], "fixture-acquire", now=now)
    authorized = (directory / "session.json").read_bytes()
    for declared in ({}, {"requests": 0}, {"requests": True}):
        with pytest.raises(CrabError, match="non-search provider request count"):
            candidate = receipt(acquire, {"status": "unavailable", **declared})
            sessions.record(directory, candidate, now=now)
        assert (directory / "session.json").read_bytes() == authorized

    valid = sessions.record(directory, forged, now=now)
    assert valid["spent"]["requests"] == 2
    assert valid["needs"][0]["phase"] == "search"


def test_drive_does_not_accept_unqualified_search_callback_result(tmp_path: Path) -> None:
    directory, _, action, _ = setup_session(tmp_path)
    invoked: list[str] = []

    def bare_host(row: dict[str, Any]) -> dict[str, Any]:
        invoked.append(row["attempt"])
        return receipt(row, {"candidates": [{"repository": "one/tool"}]})

    with pytest.raises(CrabError, match="prior operation authorization"):
        sessions.drive(directory, {"search": bare_host}, max_actions=1)
    assert invoked == [action["attempt"]]
    state = sessions._load(directory)
    assert state["lease"]["attempt"] == action["attempt"]
    assert not any(event["record"].get("kind") == "action-result" for event in state["events"])
    with pytest.raises(CrabError, match="already dispatched; reconcile"):
        sessions.drive(directory, {"search": bare_host}, max_actions=1)
    assert invoked == [action["attempt"]]


@pytest.mark.parametrize("reason", ["model-budget-reserve", "external-dependency"])
def test_stopped_discovery_lease_cannot_authorize_new_host_or_provider_effects(
    tmp_path: Path, reason: str
) -> None:
    directory, _, action, now = setup_session(tmp_path)
    stopped = sessions.stop(directory, reason, now=now + timedelta(seconds=1))
    assert stopped["finished"] and stopped["stop_reason"] == reason
    assert stopped["spent"]["requests"] == 1
    before = (directory / "session.json").read_bytes()

    with pytest.raises(CrabError, match="finished discovery session"):
        provider_call(
            directory,
            action["attempt"],
            "post-stop-provider-request",
            now=now + timedelta(seconds=2),
        )
    with pytest.raises(CrabError, match="finished discovery session"):
        sessions.authorize_host_dispatch(directory, action["attempt"], "search")
    with pytest.raises(
        CrabError, match="finished discovery session cannot accept semantic results"
    ):
        sessions.record(
            directory,
            receipt(action, {"requests": 0, "candidates": [{"repository": "post-stop/illicit"}]}),
            now=now + timedelta(seconds=2),
        )
    assert (directory / "session.json").read_bytes() == before

    # Late original usage is accounting only; it cannot reopen the stopped lease.
    late = sessions.record_usage(directory, usage(action, 41), now=now + timedelta(seconds=3))
    assert late["finished"] and late["stop_reason"] == reason
    assert late["actual_model_tokens"] == 41
    assert late["spent"]["requests"] == 1
    assert sessions.next_action(directory, now=now + timedelta(seconds=4))["finished"]
