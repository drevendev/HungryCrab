from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from hungry_crab.cache import Slug
from hungry_crab.cli import main
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.loop import LOOP_SCHEMA, Loop, atomic_json, ci_state, state_lock
from hungry_crab.maw import MawConfig

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
SHA = "a" * 40
URL = "https://github.com/example/maw/pull/1"


class Provider:
    def __init__(self) -> None:
        self.issue_count = 0
        self.pr_count = 0
        self.rows: dict[str, dict[str, Any]] = {}
        self.changed: dict[str, list[dict[str, Any]]] = {}
        self.calls = 0

    def counts(self) -> tuple[int, int]:
        self.calls += 1
        return self.issue_count, self.pr_count

    def artifact(self, url: str) -> dict[str, Any]:
        self.calls += 1
        return copy.deepcopy(self.rows[url])

    def find_pr(self, marker: str) -> dict[str, Any] | None:
        return next(
            (
                copy.deepcopy(row)
                for row in self.rows.values()
                if str(row.get("body", "")).startswith(marker)
            ),
            None,
        )

    def files(self, url: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.changed[url])

    def close_pr(self, url: str) -> None:
        self.rows[url]["state"] = "closed"


def artifact(
    url: str = URL,
    *,
    sha: str = SHA,
    merged: bool = False,
    state: str = "open",
    ci: str = "success",
) -> dict[str, Any]:
    return {
        "html_url": url,
        "head": {"sha": sha},
        "state": state,
        "body": "<!-- crab:tests:tests.unit -->\nA proposal",
        "merged_at": "today" if merged else None,
        "merge_commit_sha": sha if merged else None,
        "checks": [{"id": 1, "name": "tests", "status": "completed", "conclusion": ci}],
        "statuses": [],
    }


@pytest.fixture
def loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Loop:
    monkeypatch.setattr("hungry_crab.loop.maw_slug", lambda _: Slug("example", "maw"))
    root = tmp_path / "maw"
    root.mkdir()
    (root / ".crab.yml").write_text(
        yaml.safe_dump(
            {
                "loop": {
                    "autonomy": "work",
                    "work_authorized": True,
                    "prey": ["example/prey", "example/other"],
                    "budget": {"phases_per_day": 50},
                }
            }
        ),
        encoding="utf-8",
    )
    instance = Loop(root, provider=Provider(), now=NOW)
    instance.init()
    return instance


def force_phase(loop: Loop, phase: str, **fields: Any) -> None:
    data = loop._load()
    data.update(phase=phase, active=None, attempt=0, waiting_on=None, **fields)
    atomic_json(loop.path, data)


def record(loop: Loop, phase: str, receipt: dict[str, Any], result: str = "ok") -> dict[str, Any]:
    ready = loop.next()
    assert ready["ready"]
    return loop.record(
        ready["active"]["token"],
        phase,
        result,
        note="No useful change" if result == "skip" else "",
        receipt=receipt,
    )


def test_round_starts_with_goal_then_fixed_distinct_prey(loop: Loop) -> None:
    result = record(loop, "crave", {"goal": "Improve tests", "tokens": 120, "cost_usd": 0.01})
    assert result["phase"] == "hunt" and result["goal"] == "Improve tests"
    ready = loop.next()
    token = ready["active"]["token"]
    before = loop.path.read_bytes()
    for prey in (
        ["other/repo"],
        ["example/prey"] * 2,
        [],
        ["example/prey", "example/other", "x/y"],
    ):
        with pytest.raises(CrabError, match="HUNT"):
            loop.record(token, "hunt", "ok", receipt={"prey": prey})
        assert loop.path.read_bytes() == before
    result = loop.record(token, "hunt", "ok", receipt={"prey": ["example/prey", "example/other"]})
    assert result["current_prey"] == "example/prey" and result["phase"] == "eat"
    assert result["history"][0]["tokens"] == 120


def test_lease_is_exclusive_phase_bound_and_cannot_be_replayed(loop: Loop) -> None:
    ready = loop.next()
    token = ready["active"]["token"]
    before = loop.path.read_bytes()
    assert loop.next()["blocked_reason"] == "phase already leased"
    assert loop.path.read_bytes() == before
    for wrong_token, wrong_phase in (("wrong", "crave"), (token, "hunt")):
        with pytest.raises(CrabError):
            loop.record(wrong_token, wrong_phase, "ok", receipt={"goal": "Tests"})
    loop.record(token, "crave", "ok", receipt={"goal": "Tests"})
    with pytest.raises(CrabError):
        loop.record(token, "crave", "ok", receipt={"goal": "Tests"})


def test_pause_survives_new_instance_and_disallows_inflight_record(loop: Loop) -> None:
    ready = loop.next()
    loop.pause(True)
    restarted = Loop(loop.maw, provider=loop.provider, now=NOW + timedelta(minutes=1))
    before = loop.path.read_bytes()
    assert restarted.next()["blocked_reason"] == "paused"
    assert loop.path.read_bytes() == before
    with pytest.raises(CrabError, match="paused"):
        restarted.record(ready["active"]["token"], "crave", "ok", receipt={"goal": "Tests"})
    restarted.pause(False)
    result = restarted.record(ready["active"]["token"], "crave", "ok", receipt={"goal": "Tests"})
    assert result["history"][0]["wall_seconds"] == 60


def test_crashed_phase_retries_boundedly_without_advancing(loop: Loop) -> None:
    for attempt in range(1, 4):
        ready = loop.next()
        assert ready["attempt"] == attempt and ready["phase"] == "crave"
        token = ready["active"]["token"]
        loop.now += timedelta(minutes=61)
        with pytest.raises(CrabError, match="expired"):
            loop.record(token, "crave", "ok", receipt={"goal": "Tests"})
    exhausted = loop.next()
    assert not exhausted["ready"] and "retry limit" in exhausted["blocked_reason"]
    assert len(exhausted["history"]) == 3
    loop.acknowledge()
    assert loop.next()["ready"]


def test_daily_budget_resets_in_utc_and_failed_work_costs_a_phase(loop: Loop) -> None:
    loop.settings.phases_per_day = 1
    ready = loop.next()
    loop.record(ready["active"]["token"], "crave", "fail", note="Provider failed")
    before = loop.path.read_bytes()
    assert "daily phase budget" in loop.next()["blocked_reason"]
    assert loop.path.read_bytes() == before
    loop.now += timedelta(days=1)
    retry = loop.next()
    assert retry["ready"] and retry["phases_today"] == 1 and retry["attempt"] == 2


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "12"])
def test_invalid_cost_receipt_never_changes_state(loop: Loop, value: object) -> None:
    ready = loop.next()
    before = loop.path.read_bytes()
    with pytest.raises(UsageError):
        loop.record(
            ready["active"]["token"], "crave", "ok", receipt={"goal": "Tests", "cost_usd": value}
        )
    assert loop.path.read_bytes() == before


@pytest.mark.parametrize(
    "value",
    [
        {"budget": {"phsaes_per_day": 5}},
        {"autonomy": "auto"},
        {"work_authorized": "true"},
        {"budget": {"open_prs_max": True}},
        {"budget": {"prey_per_round": 4}},
        {"prey": "example/prey"},
        {"max_attempts": 0},
        {"budget": []},
        {"unknown": True},
    ],
)
def test_loop_configuration_fails_closed(loop: Loop, value: dict[str, Any]) -> None:
    loop.config.path.write_text(yaml.safe_dump({"loop": value}), encoding="utf-8")
    with pytest.raises(UsageError):
        MawConfig.load(loop.maw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "hungry-crab.loop/2"),
        ("phase", "merge"),
        ("maw", "elsewhere"),
        ("paused", "false"),
        ("prs", [{}]),
        ("history", None),
        ("attempt", True),
    ],
)
def test_malformed_state_is_preserved_not_reset(loop: Loop, field: str, value: object) -> None:
    data = loop._load()
    data[field] = value
    atomic_json(loop.path, data)
    before = loop.path.read_bytes()
    with pytest.raises(CrabError):
        loop.next()
    assert loop.path.read_bytes() == before


def test_control_registry_cannot_authorize_work_for_a_foreign_maw(
    loop: Loop, tmp_path: Path
) -> None:
    control = tmp_path / "control"
    (control / ".crab").mkdir(parents=True)
    (control / ".crab" / "maws.yml").write_text(
        yaml.safe_dump(
            {
                "maws": [
                    {
                        "repo": "example/maw",
                        "path": "../maw",
                        "autonomy": "work",
                        "cadence": "weekly",
                        "prey": ["example/other"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loop.config.path.write_text("loop:\n  autonomy: serve\n", encoding="utf-8")
    foreign = Loop(loop.maw, control, provider=loop.provider, now=NOW)
    foreign.init()
    assert foreign.settings.autonomy == "serve"
    assert foreign.settings.prey == ["example/other"]
    assert foreign.settings.cadence == "weekly"
    force_phase(foreign, "grow")
    before = foreign.path.read_bytes()
    assert not foreign.next()["ready"]
    assert foreign.path.read_bytes() == before
    assert loop.path.read_bytes() != foreign.path.read_bytes()


def test_limits_and_policy_block_without_spending(loop: Loop) -> None:
    assert isinstance(loop.provider, Provider)
    force_phase(loop, "serve")
    loop.provider.issue_count = 10
    before = loop.path.read_bytes()
    assert "open artifact" in loop.next()["blocked_reason"]
    assert loop.path.read_bytes() == before
    loop.settings.autonomy = "read"
    assert "read autonomy" in loop.next()["blocked_reason"]
    force_phase(loop, "grow")
    loop.settings.autonomy = "work"
    loop.settings.work_authorized = False
    assert "work_authorized" in loop.next()["blocked_reason"]


def test_read_serve_rounds_can_finish_after_human_skip_work(loop: Loop) -> None:
    force_phase(loop, "grow")
    loop.settings.autonomy = "serve"
    assert not loop.next()["ready"]
    loop.acknowledge(skip_work=True)
    result = record(loop, "taste", {"lesson": "Small test fixtures are useful"})
    assert result["phase"] == "molt"
    assert record(loop, "molt", {}, "skip")["phase"] == "harden"
    assert record(loop, "harden", {}, "skip")["round"] == 2
    assert loop.status()["phase"] == "hunt"


def test_ci_wait_and_human_merge_are_zero_cost_and_detect_head_changes(loop: Loop) -> None:
    assert isinstance(loop.provider, Provider)
    loop.provider.rows[URL] = artifact(ci=None)  # type: ignore[arg-type]
    pr = {"round": 1, "phase": "grow", "url": URL, "sha": SHA, "state": "open", "files": []}
    force_phase(loop, "trial", prs=[pr])
    data = loop._load()
    data["waiting_on"] = {"kind": "ci", "reason": "waiting for CI"}
    atomic_json(loop.path, data)
    before = loop.path.read_bytes()
    assert not loop.next()["ready"]
    assert loop.path.read_bytes() == before
    loop.provider.rows[URL] = artifact()
    result = record(loop, "trial", {"tests_passed": True})
    assert result["phase"] == "taste"
    before = loop.path.read_bytes()
    assert not loop.next()["ready"]
    assert loop.path.read_bytes() == before
    loop.provider.rows[URL] = artifact(sha="b" * 40, merged=True)
    assert "head changed" in loop.next()["blocked_reason"]


def test_molt_harden_wait_for_merges_not_model_claims(loop: Loop) -> None:
    assert isinstance(loop.provider, Provider)
    loop.provider.rows[URL] = artifact(merged=True)
    pr = {"round": 1, "phase": "grow", "url": URL, "sha": SHA, "state": "open", "files": []}
    force_phase(loop, "taste", prs=[pr])
    data = loop._load()
    data["waiting_on"] = {"kind": "merge", "reason": "waiting for human merge"}
    atomic_json(loop.path, data)
    ready = loop.next()
    assert ready["ready"] and ready["prs"][0]["state"] == "merged"
    loop.record(ready["active"]["token"], "taste", "ok", receipt={"lesson": "Test first"})
    force_phase(loop, "harden", prs=[{**pr, "state": "merged"}])
    ready = loop.next()
    with pytest.raises(CrabError, match="hardened"):
        loop.record(ready["active"]["token"], "harden", "skip", note="No release")


def test_ci_uses_latest_check_and_legacy_status_and_rejects_empty_suite() -> None:
    value = artifact()
    value["checks"].insert(
        0, {"id": 0, "name": "tests", "status": "completed", "conclusion": "failure"}
    )
    assert ci_state(value) == "pass"
    value["statuses"] = [{"state": "pending"}]
    assert ci_state(value) == "pending"
    value["statuses"] = [{"state": "failure"}]
    assert ci_state(value) == "fail"
    assert ci_state({}) == "pending"


def test_lock_is_nonblocking_and_released_after_exception(loop: Loop) -> None:
    with pytest.raises(RuntimeError), state_lock(loop.path):
        with pytest.raises(CrabError, match="another process"), state_lock(loop.path):
            pytest.fail("second lock unexpectedly succeeded")
        raise RuntimeError("simulated crash")
    assert loop.next()["ready"]


def test_cli_roundtrip_needs_no_provider_and_pause_has_no_required_flags(
    loop: Loop, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("hungry_crab.loop_cli.maw_slug", lambda _: None)
    monkeypatch.chdir(loop.maw)
    assert main(["loop", "next", "--json"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["schema"] == LOOP_SCHEMA and ready["ready"]
    assert main(["loop", "pause"]) == 0
    capsys.readouterr()
    assert main(["loop", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["paused"]


def test_trial_can_drop_an_unmerged_head_and_finish_an_empty_round(loop: Loop) -> None:
    assert isinstance(loop.provider, Provider)
    loop.provider.rows[URL] = artifact()
    pr = {"round": 1, "phase": "grow", "url": URL, "sha": SHA, "state": "open", "files": []}
    force_phase(loop, "trial", prs=[pr])
    ready = loop.next()
    result = loop.record(
        ready["active"]["token"],
        "trial",
        "skip",
        note="Local tests regress",
        receipt={"drop_pr": URL},
    )
    assert result["phase"] == "taste" and result["prs"][0]["state"] == "closed"
    assert loop.provider.rows[URL]["state"] == "closed"
    assert "Local tests regress" in result["notes"]


def test_human_drop_rejects_changed_head_and_closed_release_retry_gets_new_revision(
    loop: Loop,
) -> None:
    assert isinstance(loop.provider, Provider)
    loop.provider.rows[URL] = artifact(sha="b" * 40)
    pr = {"round": 1, "phase": "grow", "url": URL, "sha": SHA, "state": "open", "files": []}
    force_phase(loop, "trial", prs=[pr])
    before = loop.path.read_bytes()
    with pytest.raises(CrabError, match="changed"):
        loop.acknowledge(drop_pr=URL)
    assert loop.path.read_bytes() == before and loop.provider.rows[URL]["state"] == "open"
    loop.provider.rows[URL] = artifact(ci="failure")
    assert loop.acknowledge(drop_pr=URL)["phase"] == "taste"
    force_phase(loop, "harden", prs=[{**pr, "phase": "harden", "state": "closed"}])
    old = loop.marker(loop._load())
    loop.acknowledge()
    assert loop.marker(loop._load()) != old and loop.branch(loop._load()).endswith("-v1")


@pytest.mark.parametrize("text", ['{"phase":"crave","phase":"hunt"}', '{"cost_usd": NaN}'])
def test_json_duplicate_keys_and_nonfinite_values_are_refused(tmp_path: Path, text: str) -> None:
    from hungry_crab.loop import read_json

    path = tmp_path / "receipt.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CrabError):
        read_json(path)
