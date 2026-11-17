"""Real callback boundaries with owned, model-free metering and provider ports."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from discovery_host_fixture import OwnedMeter, provider_call
from test_discovery_inputs import repository
from test_discovery_usage import setup_session, usage
from test_release_069_discovery import plan, receipt

from hungry_crab import discovery_session as sessions
from hungry_crab.cache import Slug
from hungry_crab.cli import main
from hungry_crab.discovery_frontier import frontier
from hungry_crab.discovery_history import targeted_issues
from hungry_crab.discovery_host import MeterIdentity, host_meter
from hungry_crab.discovery_search import budget_for_plan, search
from hungry_crab.errors import CrabError

pytestmark = pytest.mark.usefixtures("owned_discovery_host")


def test_plan_alone_cannot_admit_strict_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan(maw)), encoding="utf-8")
    directory = tmp_path / "session"
    with host_meter(None):
        assert (
            main(["discover", "init", str(directory), "--maw", str(maw), "--plan", str(source)])
            == 1
        )
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

    assert (
        sessions.execute_operation(directory, action["attempt"], "search", invoke, now=now)
        == "response"
    )
    with pytest.raises(CrabError, match="already admitted"):
        sessions.execute_operation(directory, action["attempt"], "search", invoke, now=now)
    assert effects == ["sent"]
    result = sessions.record(directory, receipt(action, {"candidates": []}), now=now)
    assert result["spent"]["requests"] == 1


def test_zero_request_leads_require_owned_fixture_provenance(tmp_path: Path) -> None:
    # This is a synthetic shape of a reviewed-native meter, not a production
    # adapter or proof of qualification. No provider/model call is made.
    class NativeShapedMeter(OwnedMeter):
        def preflight(self) -> MeterIdentity:
            identity = super().preflight()
            return MeterIdentity(identity.source, identity.evidence_sha256, "reviewed-native")

    with host_meter(NativeShapedMeter()):
        directory, _, action, now = setup_session(tmp_path / "native-shaped")
        before = (directory / "session.json").read_bytes()
        unsupported = receipt(
            action, {"requests": 0, "candidates": [{"repository": "one/tool"}]}
        )
        with pytest.raises(CrabError, match="zero-request search leads"):
            sessions.record(directory, unsupported, now=now)
        assert (directory / "session.json").read_bytes() == before
        # A genuinely empty unsupported/offline search is still legitimate.
        sessions.record(directory, receipt(action, {"requests": 0, "candidates": []}), now=now)
        assert sessions.report(directory)["needs"][0]["phase"] == "search"

    # Controlled test-fixture leads remain legal for existing model-free
    # receiving regression tests, without pretending provider search occurred.
    directory, _, action, now = setup_session(tmp_path / "owned")
    sessions.record(
        directory,
        receipt(action, {"requests": 0, "candidates": [{"repository": "one/tool"}]}),
        now=now,
    )
    assert sessions.next_action(directory, now=now)["action"] == "acquire"



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
        sessions.execute_operation(
            directory,
            action["attempt"],
            "model",
            fail,
            kind="model",
            model_token_limit=100,
            now=now,
        )
    result = sessions.report(directory)
    assert result["actual_model_tokens"] == 23
    assert result["needs"][0]["phase"] == "search"
    assert result["budget_within_limits"] is None
    assert sessions._load(directory)["lease"]["attempt"] == action["attempt"]


def test_nested_model_reservations_share_cap_and_provider_dispatch(tmp_path: Path) -> None:
    directory, _, search_action, now = setup_session(tmp_path)
    provider_call(directory, search_action["attempt"], "search", now=now)
    sessions.record(
        directory, receipt(search_action, {"candidates": [{"repository": "one/tool"}]}), now=now
    )
    action = sessions.next_action(directory, now=now)
    effects: list[str] = []

    def outer(_: int) -> None:
        assert sessions.next_action(directory, now=now)["action"] == "reconcile"
        with pytest.raises(CrabError, match="shared discovery model token allowance"):
            sessions.execute_operation(
                directory,
                action["attempt"],
                "too-large",
                lambda _: effects.append("denied"),
                kind="model",
                model_token_limit=15001,
                now=now,
            )
        sessions.execute_operation(
            directory,
            action["attempt"],
            "nested-provider",
            lambda _: effects.append("provider"),
            now=now,
        )
        sessions.execute_operation(
            directory,
            action["attempt"],
            "nested-model",
            lambda _: effects.append("model"),
            kind="model",
            model_token_limit=15000,
            now=now,
        )

    sessions.execute_operation(
        directory, action["attempt"], "outer", outer, kind="model", model_token_limit=15000, now=now
    )
    assert effects == ["provider", "model"]
    assert sessions.report(directory)["spent"]["requests"] == 2


def test_unknown_then_late_actual_cost_is_append_only_and_never_resumes(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    meter = OwnedMeter()
    meter.tokens = None
    with host_meter(meter):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "uncertain",
            lambda _: None,
            kind="model",
            model_token_limit=100,
            now=now,
        )
    stopped = sessions.report(directory)
    assert stopped["finished"] and stopped["actual_model_tokens"] is None
    events = sessions._load(directory)["events"]
    original = next(
        row["record"]["receipt"] for row in events if row["record"]["kind"] == "model-usage"
    )
    actual = {**original, "model_tokens": 37}
    reconciled = sessions.record_usage(directory, actual, now=now + timedelta(days=1))
    assert reconciled["actual_model_tokens"] == 37
    assert reconciled["finished_at"] == stopped["finished_at"]
    assert reconciled["stop_reason"] == "usage-unavailable"
    assert sessions._load(directory)["events"][: len(events)] == events
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
            sessions.execute_operation(
                directory, action["attempt"], "nested", effects.append, now=now
            )
        (maw / "app.py").write_text("changed\n", encoding="utf-8")

    with host_meter(meter):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "late",
            invoke,
            kind="model",
            model_token_limit=100,
            now=now,
        )
    assert not effects
    result = sessions.report(directory)
    assert result["finished"] and result["actual_model_tokens"] == 19
    assert result["needs"][0]["phase"] == "search"


def test_expired_port_and_native_overrun_fail_closed(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    effects: list[int] = []
    with pytest.raises(CrabError, match="expired before host operation"):
        sessions.execute_operation(
            directory, action["attempt"], "expired", effects.append, now=now + timedelta(minutes=11)
        )
    assert not effects
    meter = OwnedMeter()
    meter.tokens = 101
    with host_meter(meter):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "overrun",
            lambda _: None,
            kind="model",
            model_token_limit=100,
            now=now,
        )
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

    budget = budget_for_plan(
        {"session": str(directory), "attempt": action["attempt"], "operation": "search"}
    )
    assert search("owned", client=Client(), budget=budget)["requests"] == 1
    assert len(calls) == 1
    with pytest.raises(CrabError, match="one leased search"):
        search("second", client=Client(), budget=budget)
    assert len(calls) == 1


def test_in_flight_model_cannot_advance_and_missing_runtime_cannot_record(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    entry = receipt(action, {"requests": 0, "candidates": []})

    def model(_: int) -> None:
        with pytest.raises(CrabError, match="trusted host settlement"):
            sessions.record(directory, entry, now=now)

    sessions.execute_operation(
        directory,
        action["attempt"],
        "in-flight",
        model,
        kind="model",
        model_token_limit=100,
        now=now,
    )
    before = (directory / "session.json").read_bytes()
    with host_meter(None), pytest.raises(CrabError, match="trusted live host metering"):
        sessions.record(directory, entry, now=now)
    assert (directory / "session.json").read_bytes() == before
    sessions.record(directory, entry, now=now)


@pytest.mark.parametrize("failure", ["begin", "finish", "invalid-usage"])
def test_native_meter_failures_stop_without_inventing_zero(tmp_path: Path, failure: str) -> None:
    directory, _, action, now = setup_session(tmp_path)
    effects: list[int] = []

    class BrokenMeter(OwnedMeter):
        def begin(self, operation: str, token_limit: int) -> object:
            if failure == "begin":
                raise RuntimeError("native meter failed before dispatch")
            return operation

        def finish(self, ticket: object) -> int | None:
            if failure == "finish":
                raise RuntimeError("native usage channel lost")
            return True

    with host_meter(BrokenMeter()):
        if failure == "begin":
            with pytest.raises(RuntimeError, match="before dispatch"):
                sessions.execute_operation(
                    directory,
                    action["attempt"],
                    "failed",
                    effects.append,
                    kind="model",
                    model_token_limit=100,
                    now=now,
                )
        else:
            sessions.execute_operation(
                directory,
                action["attempt"],
                "failed",
                effects.append,
                kind="model",
                model_token_limit=100,
                now=now,
            )
    assert effects == ([] if failure == "begin" else [100])
    result = sessions.report(directory)
    assert result["finished"] and result["actual_model_tokens"] is None
    assert result["stop_reason"] == "usage-unavailable"


def test_changed_meter_and_event_exhaustion_refuse_before_effect(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    effects: list[int] = []

    class ChangedMeter(OwnedMeter):
        def preflight(self) -> MeterIdentity:
            return MeterIdentity("different source", "b" * 64, "owned-fixture")

    before = (directory / "session.json").read_bytes()
    with host_meter(ChangedMeter()):
        assert sessions.next_action(directory, now=now)["action"] == "reconcile"
        with pytest.raises(CrabError, match="identity changed"):
            sessions.execute_operation(
                directory, action["attempt"], "changed", effects.append, now=now
            )
    assert (directory / "session.json").read_bytes() == before
    value = sessions._load(directory)
    while len(value["events"]) < 498:
        sessions._event(value, {"kind": "owned-capacity-fixture"})
    sessions._save(directory, value)
    before = (directory / "session.json").read_bytes()
    with pytest.raises(CrabError, match="event budget exhausted before host operation"):
        sessions.execute_operation(directory, action["attempt"], "full", effects.append, now=now)
    assert (directory / "session.json").read_bytes() == before
    assert not effects


@pytest.mark.parametrize("channel", ["frontier", "issues"])
@pytest.mark.parametrize("requests", [2, 3])
def test_multi_request_channels_admit_each_actual_port(
    tmp_path: Path, channel: str, requests: int
) -> None:
    maw = repository(
        tmp_path / "maw",
        {"app.py": "def read():\n    return True\n", "links.txt": "https://github.com/one/tool\n"},
    )
    definition = plan(maw)
    definition["limits"] = {"requests": requests}
    directory = tmp_path / "session"
    sessions.initialize(directory, maw, definition)
    initial = sessions.next_action(directory)
    provider_call(directory, initial["attempt"], "search")
    sessions.record(directory, receipt(initial, {"candidates": [{"repository": "one/tool"}]}))
    action = sessions.next_action(directory)
    calls: list[str] = []

    class Client:
        def get(self, path: str, *, allow_missing: bool = False) -> Any:
            assert sessions._load(directory)["events"][-1]["record"]["kind"] == "operation-started"
            calls.append(path)
            if "/issues/" in path:
                return {"number": int(path.rsplit("/", 1)[1]), "title": "Owned issue"}
            if "/commits/" in path:
                return {"sha": "b" * 40}
            return {"full_name": "one/tool", "private": False, "default_branch": "main"}

    budget = budget_for_plan(
        {"session": str(directory), "attempt": action["attempt"], "operation": channel}
    )

    def acquire() -> dict[str, Any]:
        if channel == "issues":
            return targeted_issues(
                Slug.parse("one/tool"),
                [1, 2],
                hypothesis="owned",
                reason="owned evidence",
                budget=budget,
                client=Client(),
            )
        return frontier(
            [
                {
                    "from": "one/source",
                    "from_sha": action["context"]["sha"],
                    "source_repo": str(maw),
                    "path": "links.txt",
                    "start": 1,
                    "end": 1,
                    "target": "one/tool",
                    "relationship": "adjacent-tool",
                }
            ],
            budget=budget,
            clients={"github": Client()},
        )

    if requests == 2:
        with pytest.raises(CrabError, match="global discovery request budget"):
            acquire()
        assert len(calls) == 1
    else:
        assert acquire()["requests"] == 2
        assert len(calls) == 2
    assert sessions.report(directory)["spent"]["requests"] == requests


def test_reconciled_operation_cannot_clear_unattributed_unknown_stop(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    sessions.stop(directory, "usage-unavailable", now=now)
    unknown = usage(action, None)
    sessions.record_usage(directory, unknown, now=now)
    result = sessions.record_usage(directory, {**unknown, "model_tokens": 13}, now=now)
    assert result["spent"]["model_tokens"] == 13
    assert result["actual_model_tokens"] is None
    assert result["finished"] and result["stop_reason"] == "usage-unavailable"


def test_orphaned_operation_cannot_grant_new_effects_after_host_restart(tmp_path: Path) -> None:
    directory, _, action, now = setup_session(tmp_path)
    sessions.authorize_request(directory, action["attempt"], "interrupted-original", now=now)
    before = (directory / "session.json").read_bytes()
    pending = sessions.next_action(directory, now=now)
    assert pending["action"] == "reconcile" and pending["attempt"] == action["attempt"]
    effects: list[int] = []
    with pytest.raises(CrabError, match="uncertain original host operation"):
        sessions.execute_operation(
            directory, action["attempt"], "replacement", effects.append, now=now
        )
    assert not effects and (directory / "session.json").read_bytes() == before
    # Late accounting cannot manufacture the missing trusted terminal event.
    sessions.record_usage(directory, usage(action, 17, "interrupted-original"), now=now)
    assert sessions.next_action(directory, now=now)["action"] == "reconcile"
    assert sessions._load(directory)["lease"]["attempt"] == action["attempt"]


def test_broker_usage_and_completion_are_saved_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _, action, now = setup_session(tmp_path)
    meter = OwnedMeter()
    meter.tokens = 29
    writes: list[list[str]] = []
    save_original = sessions._save

    def observe_save(path: Path, value: dict[str, Any]) -> None:
        writes.append([event["record"]["kind"] for event in value["events"]])
        save_original(path, value)

    monkeypatch.setattr(sessions, "_save", observe_save)
    with host_meter(meter):
        sessions.execute_operation(
            directory,
            action["attempt"],
            "atomic-native",
            lambda _: None,
            kind="model",
            model_token_limit=100,
            now=now,
        )

    assert len(writes) == 2  # admission, then ONE settlement commit
    assert writes[0][-1] == "operation-started"
    assert writes[1][-2:] == ["model-usage", "operation-settled"]
    assert sessions.report(directory)["actual_model_tokens"] == 29


def test_trusted_recovery_closes_existing_usage_only_crash_without_replay(
    tmp_path: Path,
) -> None:
    directory, _, action, now = setup_session(tmp_path)
    operation = action["attempt"] + ":crashed-original"
    value = sessions._load(directory)
    sessions._event(
        value,
        {
            "kind": "operation-started",
            "attempt": action["attempt"],
            "operation": operation,
            "operation_kind": "model",
            "model_token_limit": 100,
            "at": now.isoformat(),
        },
    )
    sessions._save(directory, value)
    original_usage = {
        "attempt": action["attempt"],
        "operation": operation,
        "author": "trusted host operation broker",
        "tool": "native meter boundary",
        "model_tokens": 37,
    }
    sessions.record_usage(directory, original_usage, now=now)
    accounting_only = sessions.report(directory)
    assert accounting_only["spent"]["model_tokens"] == 37
    assert accounting_only["actual_model_tokens"] is None
    assert accounting_only["model_usage_complete"] is False
    assert sessions.next_action(directory, now=now)["action"] == "reconcile"
    before = (directory / "session.json").read_bytes()

    with host_meter(None), pytest.raises(CrabError, match="trusted live host"):
        sessions.reconcile_trusted_operation(directory, action["attempt"], operation, 37, now=now)
    assert (directory / "session.json").read_bytes() == before
    with host_meter(OwnedMeter()), pytest.raises(CrabError, match="already be persisted"):
        sessions.reconcile_trusted_operation(directory, action["attempt"], operation, 38, now=now)
    assert (directory / "session.json").read_bytes() == before

    # Trusted host has independently verified the original native receipt.
    # This path never dispatches the original operation a second time.
    with host_meter(OwnedMeter()):
        sessions.reconcile_trusted_operation(directory, action["attempt"], operation, 37, now=now)
        settled_bytes = (directory / "session.json").read_bytes()
        sessions.reconcile_trusted_operation(directory, action["attempt"], operation, 37, now=now)
        assert (directory / "session.json").read_bytes() == settled_bytes
        result = sessions.record(
            directory, receipt(action, {"requests": 0, "candidates": []}), now=now
        )
    events = [item["record"] for item in sessions._load(directory)["events"]]
    assert sum(row["kind"] == "model-usage" for row in events) == 1
    assert sum(row["kind"] == "operation-settled" for row in events) == 1
    assert result["actual_model_tokens"] == 37
    assert result["model_usage_complete"] is True
    assert not sessions._pending_operations(sessions._load(directory))
