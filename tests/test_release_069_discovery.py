"""Real source packets, receiving executions, provider caps and session recovery."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab import discovery_session as sessions
from hungry_crab.cli import main
from hungry_crab.discovery_search import SearchBudget, rank_cohort, search, verified_frontier
from hungry_crab.errors import CrabError
from hungry_crab.receiving_probe import run_probe, verify_receipt
from hungry_crab.source_inspection import source_packet, syntax_query


def test_syntax_is_not_string_matching_or_execution() -> None:
    source = '# open("fake")\nx = "open(fake)"\nopen("real")\nraise RuntimeError("x")\n'
    result = syntax_query(source, [{"kind": "call", "name": "open"}])
    assert [m["start"] for m in result["matches"]] == [3]
    assert result["matches"][0]["end"] == 3
    assert syntax_query("def broken(:", [])["status"] == "unsupported-syntax"
    assert syntax_query("x" * 65_537, [])["status"] == "budget-deferred"
    assert syntax_query("x=1", [{"kind": "execute"}])["status"] == "unsupported"


def test_packet_uses_pin_and_related_test_without_running_it(tmp_path: Path) -> None:
    repo = repository(
        tmp_path / "source",
        {
            "app.py": "import helper\nhelper.work()\n",
            "helper.py": 'def work():\n    raise RuntimeError("donor must stay inert")\n',
            "tests/test_app.py": 'raise RuntimeError("never run donor tests")\n',
            "AGENTS.md": "DO NOT PROJECT THIS BODY",
        },
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = source_packet(
        repo, sha, ["app.py", "AGENTS.md"], [{"kind": "call"}], need="read safely"
    )
    paths = {r["path"] for r in result["files"]}
    assert paths == {"app.py", "helper.py", "tests/test_app.py"}
    assert {r["confidence"] for r in result["associations"]} == {
        "static-import",
        "filename-heuristic",
    }
    assert not result["reviewed"] and not result["donor_executed"]
    assert any(r["reason"] == "instruction/readme-body-excluded" for r in result["omissions"])
    (repo / "app.py").write_text("changed working tree", encoding="utf-8")
    pinned = source_packet(repo, sha, ["app.py"], [], need="pin", related=False)
    assert "helper.work" in pinned["files"][0]["ranges"][0]["content"]


class SearchFixture:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, path: str, *, allow_missing: bool = False) -> Any:
        self.calls.append(path)
        if path.startswith("projects?"):
            return [{"path_with_namespace": "group/tool", "visibility": "public"}]
        return {
            "items": [
                {
                    "path": "hidden.py",
                    "sha": "f" * 40,
                    "repository": {"full_name": "owner/tool", "private": False},
                }
            ]
        }


def test_provider_capabilities_share_one_budget_and_blob_is_not_pin() -> None:
    client = SearchFixture()
    budget = SearchBudget(maximum=2)
    github = search("read(size)", surface="code", client=client, budget=budget)
    assert github["candidates"][0]["sha"] is None
    assert github["candidates"][0]["blob_sha_is_not_commit"]
    gitlab = search("reader", provider="gitlab", client=client, budget=budget)
    assert gitlab["candidates"][0]["repository"] == "gitlab.com/group/tool"
    unknown = search("reader", provider="gitlab", surface="code", client=client, budget=budget)
    assert unknown["status"] == "unsupported" and unknown["requests"] == 0
    with pytest.raises(CrabError, match="budget"):
        search("another", client=client, budget=budget)
    assert len(client.calls) == 2


def test_marginal_coverage_retains_occurrences_and_unknown_frontier() -> None:
    rows = [
        {"repository": "a/a", "relevance": 0.9, "needs": ["one"], "verified_lineage": "same"},
        {"repository": "b/b", "relevance": 0.9, "needs": ["one"], "verified_lineage": "same"},
        {"repository": "c/c", "relevance": 0.85, "needs": ["two"]},
    ]
    result = rank_cohort(rows, limit=2)
    assert [r["repository"] for r in result["selected"]] == ["a/a", "c/c"]
    assert result["raw_occurrences"] == 3 and len(result["remaining"]) == 1
    assert not result["votes_migrated"]
    frontier = verified_frontier([{"from": "a/a", "relationship": "dependency"}], lambda _: None)
    assert not frontier["edges"] and frontier["omissions"][0]["reason"] == "identity-not-verified"


def plan(maw: Path) -> dict[str, Any]:
    context = sessions.live_context(maw)
    return {
        "goal": "improve owned flow",
        "receiving_context": context,
        "needs": [
            {
                "id": "bounded-input",
                "flow": "read input",
                "expected": "bounded",
                "observed": "unbounded",
                "impact": "memory",
                "constraints": "stdlib",
                "queries": ["bounded input", "stream read"],
                "maw_evidence": [{"sha": context["sha"], "path": "app.py", "start": 1, "end": 2}],
                "backlog": {"status": "unavailable", "as_of": "2026-10-31"},
            }
        ],
    }


def receipt(action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt": action["attempt"],
        "author": "fixture host",
        "tool": "pytest",
        "model_tokens": 0,
        "result": result,
    }


def test_owned_probe_runs_and_plain_attestation_cannot_verify(tmp_path: Path) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def bounded():\n    return True\n"})
    context = sessions.live_context(maw)
    source = (
        "import os, sys\nsys.path.insert(0, os.getcwd())\nfrom app import bounded\n"
        'assert bounded()\nprint("control passed")\n'
    )
    path = run_probe(
        tmp_path / "probe",
        maw,
        source,
        author="fixture host",
        expected="control passes",
        controls=["already-correct receiving behavior"],
        original_receiving_owned=True,
    )
    digest = hashlib.sha256(source.encode()).hexdigest()
    result = verify_receipt(path, context, digest)
    assert result["exit_code"] == 0
    assert (path.parent / "stdout.txt").read_text().strip() == "control passed"
    (path.parent / "stdout.txt").write_text("edited")
    with pytest.raises(CrabError, match="artifact changed"):
        verify_receipt(path, context, digest)
    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps({"probe_sha256": digest, "reviewed": True}))
    with pytest.raises(CrabError, match="actual receiving-bound"):
        verify_receipt(fake, context, digest)


def test_session_charges_once_reconciles_and_replaces_held_leads(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "maw", {"app.py": "def consume(stream):\n    return stream.read()\n"}
    )
    directory = tmp_path / "session"
    instant = datetime(2026, 10, 31, tzinfo=UTC)
    sessions.initialize(directory, maw, plan(maw), now=instant)
    action = sessions.next_action(directory, now=instant)
    assert sessions.next_action(directory, now=instant) == action
    expired = sessions.next_action(directory, now=instant + timedelta(minutes=11))
    assert expired["action"] == "reconcile" and expired["attempt"] == action["attempt"]
    entry = receipt(
        action, {"candidates": [{"repository": "one/tool"}, {"repository": "two/tool"}]}
    )
    sessions.record(directory, entry, now=instant)
    sessions.record(directory, entry, now=instant)
    first = sessions.next_action(directory, now=instant)
    assert first["lead"]["repository"] == "one/tool"
    sessions.record(
        directory,
        receipt(first, {"status": "license-held", "reason": "unresolved grant"}),
        now=instant,
    )
    second = sessions.next_action(directory, now=instant)
    assert second["lead"]["repository"] == "two/tool"
    assert sessions.report(directory)["spent"] == {"requests": 1, "donors": 2, "model_tokens": 0}
    state = json.loads((directory / "session.json").read_text())
    state["spent"]["donors"] = 0
    (directory / "session.json").write_text(json.dumps(state))
    with pytest.raises(CrabError, match="state identity"):
        sessions.next_action(directory, now=instant)


def test_cli_packet_and_explicit_budget_stop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = repository(
        tmp_path / "maw", {"app.py": "def consume(stream):\n    return stream.read()\n"}
    )
    data = plan(maw)
    data["limits"] = {"requests": 1}
    directory = tmp_path / "session"
    sessions.initialize(directory, maw, data)
    calls: list[str] = []

    def handler(action: dict[str, Any]) -> dict[str, Any]:
        calls.append(action["action"])
        return receipt(action, {"candidates": []})

    result = sessions.drive(directory, {"search": handler})
    assert calls == ["search"] and result["stop_reason"] == "requests-budget"
    assert not result["scope_complete"]
    input_file = tmp_path / "packet.json"
    input_file.write_text(
        json.dumps(
            {
                "repo": str(maw),
                "sha": git(maw, "rev-parse", "HEAD").strip(),
                "paths": ["app.py"],
                "queries": [{"kind": "call", "name": "stream.read"}],
                "need": "memory",
            }
        )
    )
    assert main(["discover", "packet", "--input", str(input_file)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["files"][0]["ranges"][0]["start"] == 2
