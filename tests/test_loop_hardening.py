from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fixture_builder import git
from test_loop import SHA, URL, Loop, Provider, artifact, force_phase, state_lock
from test_loop import loop as _loop_fixture
from test_loop_evidence import evidence
from test_loop_serving import _serve_round
from test_serve import FakeIssues

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.ledger import Ledger
from hungry_crab.loop import atomic_json, merge_ci_state
from hungry_crab.loop_work import serve_phase
from hungry_crab.nutrients import Candidate

loop = _loop_fixture


@pytest.mark.parametrize("edited", ["menu.json", "notes.json", "meal.json"])
def test_accepted_eat_inputs_are_frozen_before_serve_effects(
    loop: Loop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edited: str
) -> None:
    force_phase(loop, "eat", prey=["example/prey"])
    ready = loop.next()
    receipt = evidence(loop, tmp_path / "meal")
    loop.record(ready["active"]["token"], "eat", "ok", receipt=receipt)
    (tmp_path / "meal" / edited).write_text("{}\n", encoding="utf-8")
    token = loop.next()["active"]["token"]
    monkeypatch.setattr(
        "hungry_crab.loop_work.serve", lambda *_args, **_kwargs: pytest.fail("publication ran")
    )
    before = loop.path.read_bytes()
    with pytest.raises(CrabError, match="changed after acceptance"):
        serve_phase(loop, token, "example/prey", "crab:tests:tests.unit")
    assert loop.path.read_bytes() == before


@pytest.mark.parametrize("damage", ["wrong-prey", "wrong-maw-root", "same-digest"])
def test_eat_refuses_substituted_digest_identities(loop: Loop, tmp_path: Path, damage: str) -> None:
    force_phase(loop, "eat", prey=["example/prey"])
    ready = loop.next()
    receipt = evidence(loop, tmp_path / "meal")
    meal_path = Path(receipt["meal"])
    meal = json.loads(meal_path.read_text(encoding="utf-8"))
    if damage == "same-digest":
        meal["maw_digest"] = meal["prey_digest"]
        atomic_json(meal_path, meal)
    else:
        key = "prey_digest" if damage == "wrong-prey" else "maw_digest"
        manifest_path = Path(meal[key]) / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["prey"]["label" if damage == "wrong-prey" else "root"] = "unrelated"
        atomic_json(manifest_path, manifest)
    before = loop.path.read_bytes()
    with pytest.raises(CrabError):
        loop.record(ready["active"]["token"], "eat", "ok", receipt=receipt)
    assert loop.path.read_bytes() == before


@pytest.mark.parametrize("merged", [False, True])
def test_old_ledger_artifacts_cannot_be_adopted_or_closed_by_a_new_round(
    loop: Loop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, merged: bool
) -> None:
    card = Candidate(
        "tests", "tests.unit", "Tests", "Tests", origin="licensed", license_mode="COPY"
    )
    token = _serve_round(loop, tmp_path, card)
    ledger = Ledger.load(loop.config.ledger_path(), maw=loop.maw.name)
    ledger.ensure(card)
    ledger.mark(card.id, "merged" if merged else "served", url=URL)
    ledger.save()
    assert isinstance(loop.provider, Provider)
    loop.provider.rows[URL] = artifact(merged=merged)
    before, ledger_before = loop.path.read_bytes(), ledger.path.read_bytes()
    monkeypatch.setattr(
        "hungry_crab.loop_work.serve", lambda *_args, **_kwargs: pytest.fail("publication ran")
    )
    with pytest.raises(CrabError, match="authorized transaction"):
        serve_phase(loop, token, "example/prey", card.id)
    assert loop.path.read_bytes() == before and ledger.path.read_bytes() == ledger_before
    assert not loop._load()["prs"]


def test_pause_revokes_an_already_running_lease_while_the_state_lock_is_busy(loop: Loop) -> None:
    force_phase(loop, "grow")
    ready = loop.next()
    data = loop._load()
    with state_lock(loop.path):
        assert loop.pause(True) == {"paused": True, "revocation_pending": True}
        with pytest.raises(CrabError, match="paused"):
            loop.active(data, ready["active"]["token"])
    assert loop.status()["paused"]
    loop.pause(False)
    assert not loop.status()["paused"]


def test_working_tree_edits_cannot_grant_work_and_revocation_is_immediate(loop: Loop) -> None:
    policy = yaml.safe_load((loop.maw / ".crab.yml").read_text())
    policy["loop"]["work_authorized"] = False
    (loop.maw / ".crab.yml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    git(loop.maw, "add", ".crab.yml")
    git(loop.maw, "commit", "-m", "test: owner revoked work")
    policy["loop"]["work_authorized"] = True
    (loop.maw / ".crab.yml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    assert not loop._work_allowed()


@pytest.mark.parametrize("revoked", ["ledger", "prs-off", "prs-ask"])
@pytest.mark.parametrize("effect", ["provider", "push", "ledger"])
def test_live_grow_policy_withdrawal_stops_the_next_provider_effect(
    loop: Loop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revoked: str, effect: str
) -> None:
    card = Candidate(
        "tests", "tests.unit", "Tests", "Tests", origin="licensed", license_mode="COPY"
    )
    _serve_round(loop, tmp_path, card)
    issue_url = "https://github.com/example/maw/issues/1"
    ledger = Ledger.load(loop.config.ledger_path(), maw=loop.maw.name)
    ledger.ensure(card)
    ledger.mark(card.id, "served", url=issue_url)
    ledger.save()
    assert isinstance(loop.provider, Provider)
    loop.provider.rows[issue_url] = {
        "body": f"<!-- {card.id} -->\n",
        "state": "open",
        "html_url": issue_url,
    }
    force_phase(loop, "grow", issues=[issue_url])
    token = loop.next()["active"]["token"]
    effects: list[str] = []

    class Client(FakeIssues):
        def run_gh(self, *_args: str) -> str:
            effects.append("provider write")
            return "unexpected"

    monkeypatch.setattr("hungry_crab.loop_work.GhIssueClient", lambda **_: Client())
    monkeypatch.setattr("hungry_crab.loop_work.check_push_target", lambda *_: None)
    import subprocess

    from hungry_crab.fetch.git import GitRunner

    original_run = subprocess.run

    def process(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if "push" in argv:
            effects.append("push")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return original_run(argv, **kwargs)

    monkeypatch.setattr("hungry_crab.fetch.git.subprocess.run", process)

    def revoke_before_effect(*_args: Any, **kwargs: Any) -> None:
        policy_path = loop.maw / ".crab.yml"
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        if revoked == "ledger":
            policy["ledger"] = "none"
        else:
            policy["serve"]["prs"] = revoked.removeprefix("prs-")
        policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
        if effect == "provider":
            kwargs["client"].run_gh("pr", "create")
        elif effect == "push":
            GitRunner(loop.maw).run("push", "origin", "fixture")
        else:
            kwargs["ledger"].save()

    monkeypatch.setattr("hungry_crab.loop_work.serve", revoke_before_effect)
    before, ledger_before = loop.path.read_bytes(), ledger.path.read_bytes()
    with pytest.raises(CrabError, match="no longer authorizes"):
        serve_phase(loop, token, "example/prey", card.id)
    assert not effects
    assert loop.path.read_bytes() == before and ledger.path.read_bytes() == ledger_before
    # The lease guard must not escape into ordinary caller-owned operations after refusal.
    ledger.save()


def test_working_tree_pr_permission_cannot_replace_committed_consent(loop: Loop) -> None:
    policy_path = loop.maw / ".crab.yml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["serve"]["prs"] = "ask"
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    git(loop.maw, "add", ".crab.yml")
    git(loop.maw, "commit", "-m", "test: owner withdrew automatic PR consent")
    policy["serve"]["prs"] = "auto"
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    assert not loop._work_allowed(publish_pr=True)


@pytest.mark.parametrize("same_tree", [False, True])
def test_pr_only_ci_requires_explicit_policy_and_identical_tested_tree(same_tree: bool) -> None:
    truth = artifact(merged=True)
    truth["merge_ci"] = {"sha": SHA, "checks": [], "statuses": []}
    truth["merge_tree_matches_head"] = same_tree
    assert merge_ci_state(truth) == "pending"
    assert merge_ci_state(truth, allow_pr_only=True) == ("pass" if same_tree else "pending")


def test_drop_never_closes_another_publishers_pr(loop: Loop) -> None:
    assert isinstance(loop.provider, Provider)
    loop.provider.rows[URL] = artifact()
    loop.provider.rows[URL]["user"] = {"login": "someone-else"}
    force_phase(
        loop,
        "trial",
        prs=[{"round": 1, "phase": "grow", "state": "open", "url": URL, "sha": SHA, "files": []}],
    )
    with pytest.raises(CrabError, match="not owned"):
        loop.acknowledge(drop_pr=URL)
    assert loop.provider.rows[URL]["state"] == "open"


def test_changed_owned_head_can_be_acknowledged_and_must_be_retrialled(loop: Loop) -> None:
    assert isinstance(loop.provider, Provider)
    marker = "<!-- crab:transaction:" + "a" * 32 + " -->"
    truth = artifact(sha="b" * 40)
    truth["body"] += "\n" + marker
    loop.provider.rows[URL] = truth
    loop.provider.changed[URL] = [{"filename": "src/app.py", "status": "modified"}]
    force_phase(
        loop,
        "trial",
        prs=[
            {
                "round": 1,
                "phase": "grow",
                "state": "open",
                "url": URL,
                "sha": SHA,
                "files": [],
                "transaction": marker,
            }
        ],
        waiting_on={"kind": "human", "reason": "PR head changed"},
    )
    loop.acknowledge()
    assert loop._load()["prs"][0]["sha"] == "b" * 40
    ready = loop.next()
    loop.record(ready["active"]["token"], "trial", "ok", receipt={"tests_passed": True})
    assert loop._load()["waiting_on"]["kind"] == "merge"


def test_round_advance_discards_issue_and_transaction_scope(loop: Loop) -> None:
    data = loop._load()
    data.update(phase="harden", issues=["old issue"])
    loop._advance(data, "skip")
    assert data["round"] == 2 and data["issues"] == []


def test_crash_recovery_adopts_only_the_durable_current_transaction(
    loop: Loop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert isinstance(loop.provider, Provider)
    provider = loop.provider

    class Client(FakeIssues):
        def create(
            self, slug: Slug, title: str, body: str, labels: list[str], assignees: list[str]
        ) -> str:
            url = super().create(slug, title, body, labels, assignees)
            provider.rows[url] = {
                "html_url": url,
                "body": body,
                "state": "open",
                "user": {"login": provider.identity()},
            }
            return url

    client = Client()
    monkeypatch.setattr("hungry_crab.loop_work.GhIssueClient", lambda **_: client)
    card = Candidate(
        "tests", "tests.unit", "Tests", "Tests", origin="licensed", license_mode="COPY"
    )
    token = _serve_round(loop, tmp_path, card)
    save = loop._save

    def crash_after_effect(data: dict[str, Any]) -> None:
        if data["issues"]:
            raise CrabError("synthetic crash after provider and ledger success")
        save(data)

    monkeypatch.setattr(loop, "_save", crash_after_effect)
    with pytest.raises(CrabError, match="synthetic crash"):
        serve_phase(loop, token, "example/prey", card.id)
    assert loop._load()["issues"] == [] and loop._load()["transactions"]
    monkeypatch.setattr(loop, "_save", save)
    serve_phase(loop, token, "example/prey", card.id)
    assert len(client.created) == 1
    assert len(loop._load()["issues"]) == 1
