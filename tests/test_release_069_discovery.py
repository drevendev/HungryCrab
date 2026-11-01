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


@pytest.mark.parametrize("receiving_name", ["reader-one", "reader-two", "reader-three"])
def test_invoked_host_executes_replacements_and_an_original_probe(
    tmp_path: Path, receiving_name: str
) -> None:
    """Controlled host judgments test execution, not independent discovery precision."""
    from hungry_crab.attribution import GitSourceReader
    from hungry_crab.cache import Target
    from hungry_crab.compare import compare_for_maw
    from hungry_crab.digest import DigestOptions
    from hungry_crab.exploration import initialize as explore_init
    from hungry_crab.exploration import record as explore_record
    from hungry_crab.licensing.path_grants import GrantResolver

    maw = repository(
        tmp_path / receiving_name,
        {
            "app.py": "def consume(stream):\n    return stream.read()\n"
            "def safe(stream):\n    return stream.read(4)\n",
            ".crab.yml": "ledger: none\n",
        },
    )
    donors = {
        name: repository(
            tmp_path / name,
            {
                "reader.py": "def consume(stream):\n    return stream.read(4)\n",
                **({"LICENSE": "Internal use only.\n"} if name == "held" else {}),
            },
        )
        for name in ("present", "duplicate", "held", "gap")
    }
    directory = tmp_path / "session"
    sessions.initialize(directory, maw, plan(maw))
    children: dict[str, Path] = {}
    menus: dict[str, dict[str, Any]] = {}
    outcomes = {"present": "already-present", "duplicate": "duplicate", "gap": "verified-gap"}
    executed: list[str] = []

    def search_handler(action: dict[str, Any]) -> dict[str, Any]:
        return receipt(action, {"candidates": [{"repository": name} for name in donors]})

    def acquire(action: dict[str, Any]) -> dict[str, Any]:
        name = action["lead"]["repository"]
        executed.append(name)
        if name == "held":
            sha = git(donors[name], "rev-parse", "HEAD").strip()
            assert (
                GrantResolver(GitSourceReader(donors[name]), sha).resolve("reader.py").expression
                is None
            )
            return receipt(
                action, {"status": "license-held", "reason": "actual restrictive source grant"}
            )
        comparison, _, _, _ = compare_for_maw(
            Target(path=donors[name]),
            maw,
            digest_options=DigestOptions(cache_root=tmp_path / "cache"),
            record_ledger=False,
        )
        assert comparison.meal_dir is not None
        menus[name] = comparison.menu
        return receipt(action, {"meal_dir": str(comparison.meal_dir)})

    def inspect(action: dict[str, Any]) -> dict[str, Any]:
        name = action["lead"]["repository"]
        data = json.loads((directory / "session.json").read_text())
        meal = Path(data["needs"][0]["meal_dir"])
        child = explore_init(
            meal,
            {
                "scope": "controlled receiving flow",
                "goals": "bounded reads",
                "flows": "consume user input",
                "constraints": "stdlib and inert donor",
                "facets": ["performance-streaming"],
                "author": "fixture host",
                "attempt": name,
            },
            maw=maw,
        )
        children[name] = child
        menu = menus[name]
        explore_record(
            child,
            {
                "kind": "decision",
                "hypothesis": "bounded input",
                "disposition": outcomes[name],
                "reviewed": True,
                "author": "fixture host",
                "tool": "controlled host fixture",
                "attempt": name,
                "reason": "controlled source/receiving comparison; host-supplied judgment",
                "source_evidence": [
                    {"sha": menu["prey"]["sha"], "path": "reader.py", "start": 1, "end": 2}
                ],
                "maw_evidence": [
                    {"sha": menu["maw"]["sha"], "path": "app.py", "start": 1, "end": 4}
                ],
            },
        )
        return receipt(action, {"exploration": str(child)})

    def verify(action: dict[str, Any]) -> dict[str, Any]:
        if action["lead"]["repository"] != "gap":
            return receipt(action, {"evidence_level": "source-supported"})
        source = (
            "import os, sys\nsys.path.insert(0, os.getcwd())\nfrom app import consume, safe\n"
            "class Bounded:\n    def read(self, size=-1):\n"
            '        if size < 0: raise ValueError("unbounded")\n        return b"data"\n'
            'assert safe(Bounded()) == b"data"\n'
            "try: consume(Bounded())\n"
            'except ValueError: print("unbounded receiver; bounded control passes")\n'
            'else: raise AssertionError("expected original receiving defect")\n'
        )
        path = run_probe(
            tmp_path / "original-probe",
            maw,
            source,
            author="fixture host",
            expected="bounded control succeeds and the original receiving defect is observed",
            controls=["already-correct safe function", "unbounded target rejects bounded input"],
            original_receiving_owned=True,
        )
        assert read_json(path)["exit_code"] == 0
        return receipt(
            action,
            {
                "evidence_level": "behavior-verified",
                "expected": "bounded reads",
                "observed": "unbounded target; bounded control passes",
                "probe_owner": "receiving",
                "probe_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "receiving_sha": action["context"]["sha"],
                "execution_receipt": str(path),
            },
        )

    def decide(action: dict[str, Any]) -> dict[str, Any]:
        name = action["lead"]["repository"]
        return receipt(
            action,
            {
                "disposition": outcomes[name],
                "hypothesis": "bounded input",
                "reason": "controlled reviewed outcome",
                "adaptation": "change only the receiving input flow",
                "maintenance": "stdlib helper and original regression probe",
                "novelty": "controlled fixture's seeded gap",
            },
        )

    result = sessions.drive(
        directory,
        {
            "search": search_handler,
            "acquire": acquire,
            "inspect": inspect,
            "verify": verify,
            "decide": decide,
        },
    )
    assert executed == ["present", "duplicate", "held", "gap"]
    assert result["stop_reason"] == "scoped-goals-met" and result["scope_complete"]
    assert result["spent"] == {"requests": 1, "donors": 4, "model_tokens": 0}
    assert (
        result["needs"][0]["outcomes"][-1]["verification"]["evidence_level"] == "behavior-verified"
    )
    assert not result["published"] and not result["acceptance_learning_written"]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def test_distinct_receiving_needs_get_a_turn_after_failed_donor_and_empty_search(
    tmp_path: Path,
) -> None:
    """A first need must not exhaust the shared pool before the next is even searched."""
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return True\n"})
    definition = plan(maw)
    definition["needs"].append(
        {
            **definition["needs"][0],
            "id": "second-receiving-need",
            "queries": ["different evidence"],
        }
    )
    directory = tmp_path / "session"
    instant = datetime.now(UTC)
    sessions.initialize(directory, maw, definition, now=instant)

    first = sessions.next_action(directory, now=instant)
    assert first["need"] == "bounded-input" and first["action"] == "search"
    sessions.record(
        directory, receipt(first, {"candidates": [{"repository": "one/tool"}]}), now=instant
    )
    acquire = sessions.next_action(directory, now=instant)
    assert acquire["need"] == first["need"] and acquire["action"] == "acquire"
    sessions.record(
        directory,
        receipt(acquire, {"status": "license-held", "reason": "no path grant"}),
        now=instant,
    )

    second = sessions.next_action(directory, now=instant)
    assert second["need"] == "second-receiving-need" and second["action"] == "search"
    sessions.record(directory, receipt(second, {"candidates": []}), now=instant)

    resumed = sessions.next_action(directory, now=instant)
    assert resumed["need"] == "bounded-input" and resumed["action"] == "search"
    assert sessions.report(directory)["spent"] == {
        "requests": 3,
        "donors": 1,
        "model_tokens": 0,
    }


def test_pending_need_rotates_after_reviewed_decision_not_mid_inspection() -> None:
    needs = [
        {"id": "first", "status": "pending", "phase": "acquire"},
        {"id": "second", "status": "pending", "phase": "search"},
    ]
    reserve = {
        "kind": "reserved-action",
        "need": "first",
        "action": "decide",
        "attempt": "original-attempt",
    }
    completed = {
        "kind": "action-result",
        "receipt": {
            "attempt": "original-attempt",
            "result": {"disposition": "duplicate"},
        },
    }
    assert (
        sessions._pending_need(
            {"needs": needs, "events": [{"record": reserve}, {"record": completed}]}
        )["id"]
        == "second"
    )
    reserve["action"] = "inspect"
    assert (
        sessions._pending_need(
            {"needs": needs, "events": [{"record": reserve}, {"record": completed}]}
        )["id"]
        == "first"
    )


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


def test_cross_channel_requests_are_precharged_and_uncertain_replay_is_refused(
    tmp_path: Path,
) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return 1\n"})
    data = plan(maw)
    data["limits"] = {"requests": 2}
    directory = tmp_path / "session"
    sessions.initialize(directory, maw, data)
    action = sessions.next_action(directory)
    sessions.authorize_request(directory, action["attempt"], "provider-search")
    with pytest.raises(CrabError, match="reconcile"):
        sessions.authorize_request(directory, action["attempt"], "provider-search")
    sessions.record(directory, receipt(action, {"candidates": [{"repository": "one/tool"}]}))
    action = sessions.next_action(directory)
    sessions.authorize_request(directory, action["attempt"], "targeted-issue")
    with pytest.raises(CrabError, match="global discovery request budget"):
        sessions.authorize_request(directory, action["attempt"], "frontier-identity")
    assert sessions.report(directory)["spent"]["requests"] == 2


def test_actual_overspend_and_unavailable_usage_are_honest_stops(tmp_path: Path) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def read():\n    return 1\n"})
    for label in ("overrun", "unknown"):
        directory = tmp_path / label
        sessions.initialize(directory, maw, plan(maw))
        action = sessions.next_action(directory)
        if label == "overrun":
            entry = receipt(action, {"candidates": []})
            entry["model_tokens"] = 30_001
            result = sessions.record(directory, entry)
            assert result["actual_model_tokens"] == 30_001
            assert result["stop_reason"] == "model-token-budget"
        else:
            result = sessions.stop(directory, "usage-unavailable")
            assert result["actual_model_tokens"] is None
            assert sessions.next_action(directory)["stop_reason"] == "usage-unavailable"
        assert not result["budget_within_limits"]
        assert not result["scope_complete"]


def test_design_acceptance_requires_a_real_receiving_bound_artifact(tmp_path: Path) -> None:
    from hungry_crab.receiving_probe import verify_design_artifact

    context = {"sha": "a" * 40}
    with pytest.raises(CrabError, match="actual acceptance artifact"):
        verify_design_artifact(None, context)
    document = tmp_path / "design.md"
    document.write_text("Original receiving design: bound each read and retain explicit omissions.")
    artifact = {
        "path": str(document),
        "sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
        "receiving_context": context,
        "original_receiving_owned": True,
        "criteria": ["Receiving flow has a finite memory bound and visible omitted evidence."],
        "assessment": "Reviewed against the receiving flow as a design proposal.",
    }
    verify_design_artifact(artifact, context)
    document.write_text("PASS")
    with pytest.raises(CrabError, match="changed"):
        verify_design_artifact(artifact, context)


def test_frontier_qualifies_actual_reference_and_independent_provider_pin(tmp_path: Path) -> None:
    from hungry_crab.discovery_frontier import frontier

    source = repository(tmp_path / "source", {"links.txt": "https://github.com/owner/tool\n"})
    sha = git(source, "rev-parse", "HEAD").strip()
    root = {
        "from": "owner/source",
        "from_sha": sha,
        "source_repo": str(source),
        "path": "links.txt",
        "start": 1,
        "end": 1,
        "target": "owner/tool",
        "relationship": "adjacent-tool",
    }

    class Client:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, path: str, *, allow_missing: bool = False) -> Any:
            self.calls.append(path)
            if "/commits/" in path:
                return {"sha": "b" * 40}
            return {"full_name": "owner/tool", "private": False, "default_branch": "main"}

    client = Client()
    result = frontier([root], budget=SearchBudget(maximum=2), clients={"github": client})
    assert result["requests"] == 2 and len(client.calls) == 2
    assert result["edges"][0]["sha"] == "b" * 40
    assert result["edges"][0]["depth"] == 1
    missing = frontier(
        [{**root, "target": "owner/tool-suffix"}],
        budget=SearchBudget(maximum=2),
        clients={"github": client},
    )
    assert not missing["edges"] and len(client.calls) == 2
