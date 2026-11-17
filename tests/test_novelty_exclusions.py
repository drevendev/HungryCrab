"""Frozen exclusion provenance and later supersession on owned inert sources."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git
from test_exploration import PLAN, STREAMING, observation, pair
from test_serve import FakeIssues, MAW_SLUG

from hungry_crab import exploration as exploration
from hungry_crab.analysis_context import fingerprint
from hungry_crab.cli import main
from hungry_crab.compare import load_menu, menu_candidates
from hungry_crab.errors import CrabError
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.novelty_exclusions import BASIS_SCHEMA, reconcile
from hungry_crab.serve import ServeOptions, serve
from hungry_crab.source_lines import physical_lines

PENDING = "refs/crab/qualification/pr-17"


def pending_commit(maw: Path) -> str:
    selected = git(maw, "symbolic-ref", "--short", "HEAD").strip()
    pin = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "checkout", "--detach", pin)
    (maw / "app.py").write_text(STREAMING, encoding="utf-8", newline="\n")
    git(maw, "add", "app.py")
    git(maw, "commit", "-m", "test: separately observed pending implementation")
    pending = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "update-ref", PENDING, pending)
    git(maw, "checkout", selected)
    return pending


def scope_plan(maw: Path, pending: str | None = None) -> dict[str, Any]:
    return {
        "prefixes": ["app.py"],
        "default_ref": git(maw, "symbolic-ref", "--short", "HEAD").strip(),
        "default_sha": git(maw, "rev-parse", "HEAD").strip(),
        "pending": [{"ref": PENDING, "sha": pending}] if pending else [],
        "pending_collection": {"complete": True, "as_of": "owned explicit provider control"},
    }


def setup(tmp_path: Path, *, pending: bool = True) -> tuple[Path, Path, Path, dict[str, Any]]:
    original, donor, maw, finding = pair(tmp_path, hunger="issues-only")
    pin = pending_commit(maw) if pending else None
    directory = exploration.initialize(
        original.parent.parent, {**PLAN, "novelty_scope": scope_plan(maw, pin)}, maw=maw
    )
    return directory, donor, maw, finding


def basis(scope: dict[str, Any], role: str = "receiving-pending") -> dict[str, Any]:
    target = next(t for t in scope["targets"] if t["role"] == role)
    file = target["files"][0]
    content = scope["sources"][file["content_sha256"]]["content"]
    window = "".join(physical_lines(content, keepends=True)[:2])
    return {
        "schema": BASIS_SCHEMA,
        "scope_sha256": fingerprint(scope),
        **{key: target[key] for key in ("role", "ref", "sha")},
        "evidence": [{"path": "app.py", "start": 1, "end": 2,
                      "content_sha256": hashlib.sha256(window.encode()).hexdigest()}],
    }


@pytest.mark.parametrize("role", ["receiving-selected", "receiving-default", "receiving-pending"])
def test_reviewed_exclusion_keeps_actual_role_pin_ranges_and_observation(
    tmp_path: Path, role: str
) -> None:
    directory, _, _, finding = setup(tmp_path)
    scope = exploration._snapshot(directory)["novelty_scope"]
    entry = observation(finding, kind="decision", hypothesis="bounded reads", reviewed=True,
                        disposition="duplicate", novelty_basis=basis(scope, role))
    event = exploration.record(directory, entry)
    actual = event["record"]["novelty_basis"]
    assert actual["observed_at"] == scope["observed_at"]
    assert actual["role"] == role and actual["ref"] == entry["novelty_basis"]["ref"]
    assert actual["evidence"][0]["file_sha256"]
    decision = exploration.report(directory)["decisions"][0]
    assert decision["novelty_status"] == "frozen-implementation-supported"
    assert exploration.record(directory, entry) == event


@pytest.mark.parametrize("change", ["role", "ref", "sha", "path", "range", "bytes", "scope", "time"])
def test_old_unrelated_or_changed_evidence_cannot_verify_pending_equivalence(
    tmp_path: Path, change: str
) -> None:
    directory, _, _, finding = setup(tmp_path)
    scope = exploration._snapshot(directory)["novelty_scope"]
    claimed = basis(scope)
    if change == "role":
        claimed["role"] = "receiving-default"
    elif change == "ref":
        claimed["ref"] = "refs/crab/qualification/pr-99"
    elif change == "sha":
        claimed["sha"] = scope["receiving_context"]["sha"]
    elif change == "path":
        claimed["evidence"][0]["path"] = "unrelated.py"
    elif change == "range":
        claimed["evidence"][0]["end"] = 999
    elif change == "bytes":
        claimed["evidence"][0]["content_sha256"] = "0" * 64
    elif change == "scope":
        claimed["scope_sha256"] = "0" * 64
    else:
        claimed["observed_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(CrabError, match="novelty"):
        exploration.record(directory, observation(
            finding, kind="decision", hypothesis="bounded reads", reviewed=True,
            disposition="duplicate", novelty_basis=claimed,
        ))
    assert not exploration.events(directory)


def test_plain_duplicate_prose_remains_unresolved_and_legacy_is_not_upgraded(tmp_path: Path) -> None:
    directory, _, _, finding = pair(tmp_path)
    entry = observation(finding, kind="decision", hypothesis="bounded reads",
                        reviewed=True, disposition="duplicate")
    with pytest.raises(CrabError, match="frozen novelty scope"):
        exploration.record(directory, entry)
    frozen = exploration._snapshot(directory)
    frozen.pop("novelty_scope")
    legacy = directory.parent / fingerprint(frozen)
    legacy.mkdir()
    exploration._write_new(legacy / "snapshot.json", frozen)
    exploration._append(legacy, entry)
    result = exploration.report(legacy)
    assert result["decisions"][0]["novelty_status"] == "legacy-analyst-assertion"
    assert result["unresolved_hypotheses"] == ["bounded reads"]
    assert not result["exploration_complete"]
    assert exploration.events(legacy)[0]["record"] == entry


def test_reinitialization_preserves_first_pending_observation(tmp_path: Path) -> None:
    directory, _, maw, _ = setup(tmp_path)
    original = exploration._snapshot(directory)
    before = (directory / "snapshot.json").read_bytes()
    git(maw, "update-ref", PENDING, original["receiving_context"]["sha"])
    assert exploration.initialize(directory.parent.parent, original["plan"], maw=maw) == directory
    assert (directory / "snapshot.json").read_bytes() == before


def reconciliation_plan(directory: Path, maw: Path, original: dict[str, Any]) -> dict[str, Any]:
    pin = pending_commit(maw)
    content = "".join(physical_lines(STREAMING, keepends=True)[:2])
    return {
        "operation": "later-pending", "original_event": original["sha256"],
        "scope": scope_plan(maw, pin),
        "novelty_basis": {
            "schema": BASIS_SCHEMA, "role": "receiving-pending", "ref": PENDING, "sha": pin,
            "evidence": [{"path": "app.py", "start": 1, "end": 2,
                          "content_sha256": hashlib.sha256(content.encode()).hexdigest()}],
        },
        "author": "owned analyst", "tool": "owned fixture", "attempt": "later-1",
        "reason": "a separately observed implementation supersedes this proposal now",
    }


def test_later_fix_is_an_append_only_supersession_and_holds_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, donor, maw, finding = setup(tmp_path, pending=False)
    finding["source_behavior"] = "reads bounded chunks"
    registered = exploration.register(directory, finding, source_repo=donor)
    identity = registered["record"]["card"]["id"]
    original = exploration.record(directory, observation(
        finding, kind="decision", hypothesis="bounded reads", disposition="verified-gap",
        reviewed=True, candidate_id=identity, selected=True,
    ))
    snapshot_bytes = (directory / "snapshot.json").read_bytes()
    old_events = exploration.events(directory)
    request = reconciliation_plan(directory, maw, original)
    source = tmp_path / "reconcile.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    assert main(["explore", "reconcile-novelty", str(directory), "--input", str(source)]) == 0
    event = json.loads(capsys.readouterr().out)
    assert event["record"]["original_disposition"] == "verified-gap"
    assert not event["record"]["baseline_presence_proven"]
    assert exploration.events(directory)[:len(old_events)] == old_events
    assert (directory / "snapshot.json").read_bytes() == snapshot_bytes
    result = exploration.report(directory)
    assert result["decisions"][0]["disposition"] == "verified-gap"
    assert result["decisions"][0]["selected"]
    assert result["novelty_reconciliations"][0]["status"] == "later-supersession"
    assert reconcile(directory, request) == event
    menu = load_menu(directory.parent.parent)
    assert menu is not None and identity in {c.id for c in menu_candidates(menu)}
    client = FakeIssues()
    with pytest.raises(CrabError, match="supersedes"):
        serve(directory.parent.parent, maw, ServeOptions(ids=[identity], mode="issue"),
              config=MawConfig.load(maw), ledger=Ledger(None), client=client,
              slug_lookup=lambda _: MAW_SLUG)
    assert not client.created and not client.labels
    changed = copy.deepcopy(request)
    changed["reason"] = "changed original interpretation"
    with pytest.raises(CrabError, match="different plan"):
        reconcile(directory, changed)


@pytest.mark.parametrize("missing", ["ref", "commit"])
def test_unknown_pending_scope_never_resolves_exclusion(tmp_path: Path, missing: str) -> None:
    original, _, maw, finding = pair(tmp_path)
    plan = scope_plan(maw)
    pin = "f" * 40 if missing == "commit" else git(maw, "rev-parse", "HEAD").strip()
    plan["pending"] = [{"ref": PENDING, "sha": pin}]
    directory = exploration.initialize(
        original.parent.parent, {**PLAN, "novelty_scope": plan}, maw=maw
    )
    scope = exploration._snapshot(directory)["novelty_scope"]
    assert scope["omissions"] and not scope["implementation_scope_complete"]
    fake = basis(scope, "receiving-selected")
    fake.update(role="receiving-pending", ref=PENDING, sha=pin)
    with pytest.raises(CrabError, match="unavailable"):
        exploration.record(directory, observation(
            finding, kind="decision", hypothesis="bounded reads", disposition="already-present",
            reviewed=True, novelty_basis=fake,
        ))
    assert not exploration.events(directory)


def test_provider_marker_control_and_ref_movement_before_effect(tmp_path: Path) -> None:
    directory, donor, maw, finding = setup(tmp_path)
    finding["source_behavior"] = "reads bounded chunks"
    registered = exploration.register(directory, finding, source_repo=donor)
    identity = registered["record"]["card"]["id"]
    options = ServeOptions(ids=[identity], mode="issue")
    client = FakeIssues({identity: {"number": 17, "url": "https://github.com/owned/maw/issues/17", "state": "open"}})
    report = serve(directory.parent.parent, maw, options, config=MawConfig.load(maw),
                   ledger=Ledger(None), client=client, slug_lookup=lambda _: MAW_SLUG)
    assert not client.created
    assert any("issue #17 exists" in row["reason"] for row in report.skipped)

    class MovingProvider(FakeIssues):
        def list_marked(self, slug: Any, label: str) -> dict[str, dict[str, Any]]:
            git(maw, "update-ref", PENDING, git(maw, "rev-parse", "HEAD").strip())
            return {}

    moving = MovingProvider()
    with pytest.raises(CrabError, match="ref changed"):
        serve(directory.parent.parent, maw, options, config=MawConfig.load(maw),
              ledger=Ledger(None), client=moving, slug_lookup=lambda _: MAW_SLUG)
    assert not moving.created and not moving.labels
