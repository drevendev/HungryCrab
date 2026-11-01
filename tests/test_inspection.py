"""Actual cumulative reads survive continuation; models cannot invent citation roles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixture_builder import git
from test_discovery_inputs import repository
from test_exploration import pair

from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.exploration import events, record, report
from hungry_crab.exploration import initialize as initialize_exploration
from hungry_crab.inspection import contract, inspect, resolve, witnesses
from hungry_crab.source_find import find_source


def read_plan(side: str, path: str, operation: str) -> dict[str, object]:
    return {
        "side": side,
        "paths": [path],
        "operation": operation,
        "queries": [],
        "ranges": [{"path": path, "start": 1, "end": 2}],
    }


def test_actual_implementation_witness_survives_receiving_continuation(tmp_path: Path) -> None:
    directory, _, maw, _ = pair(tmp_path)
    first = inspect(directory, read_plan("donor", "reader.py", "first"))
    assert not first["idempotent"]
    donor = first["inspection"]["witnesses"][0]
    inspect(directory, read_plan("receiving", "app.py", "receiving"))
    all_reads = witnesses(directory)
    assert len(all_reads["witnesses"]) == 2
    receiving = next(r for r in all_reads["witnesses"] if r["side"] == "receiving")
    refs = resolve(directory, {"source_evidence": [donor["id"]], "maw_evidence": [receiving["id"]]})
    assert refs["source_evidence"][0]["path"] == "reader.py"
    assert refs["maw_evidence"][0]["sha"] == git(maw, "rev-parse", "HEAD").strip()
    assert len(report(directory)["inspections"]) == 2
    assert report(directory)["examined_hypotheses"] == []
    before = len(events(directory))
    assert inspect(directory, read_plan("donor", "reader.py", "first"))["idempotent"]
    assert len(events(directory)) == before
    schema = contract(directory)["citation_schema"]
    assert donor["id"] in schema["properties"]["source_evidence"]["items"]["enum"]
    assert receiving["id"] not in schema["properties"]["source_evidence"]["items"]["enum"]
    with pytest.raises(CrabError, match="other evidence role"):
        resolve(directory, {"source_evidence": [receiving["id"]]})
    with pytest.raises(CrabError, match="different plan"):
        inspect(directory, {**read_plan("donor", "reader.py", "first"), "need": "changed"})


def test_forged_or_tampered_inspection_never_authorizes_a_citation(tmp_path: Path) -> None:
    directory, _, _, _ = pair(tmp_path)
    with pytest.raises(CrabError, match="discover inspect"):
        record(
            directory,
            {
                "kind": "inspection",
                "reason": "examined",
                "author": "x",
                "tool": "x",
                "attempt": "x",
            },
        )
    result = inspect(directory, read_plan("donor", "reader.py", "real"))
    artifact = directory / result["inspection"]["artifact"]
    artifact.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CrabError, match="identity changed"):
        witnesses(directory)


def test_changed_receiving_code_refuses_inspection_before_an_event(tmp_path: Path) -> None:
    directory, _, maw, _ = pair(tmp_path)
    (maw / "app.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(CrabError, match="context changed"):
        inspect(directory, read_plan("donor", "reader.py", "stale"))
    assert not events(directory)


def test_literal_find_prioritizes_specific_later_locations_and_discloses_limits(
    tmp_path: Path,
) -> None:
    repo = repository(
        tmp_path / "source",
        {
            "a.py": "# error\n",
            "z.py": "def handle_parse_error():\n    return 1\n",
            "README.md": "handle_parse_error SECRET_README\n",
        },
    )
    pin = git(repo, "rev-parse", "HEAD").strip()
    result = find_source(repo, pin, ["error", "handle_parse_error"], limit=1)
    assert result["matches"][0]["path"] == "z.py"
    assert result["matching_lines_not_returned"] == 1
    limited = find_source(repo, pin, ["error"], max_files=1)
    assert limited["files_not_inspected"] == limited["eligible_files"] - 1
    assert "SECRET_README" not in json.dumps(result)
    assert result["license_permission"] == "not-established"
    with pytest.raises(CrabError):
        find_source(repo, "HEAD", ["error"])


def test_public_contract_exposes_actual_paths_and_preserves_empty_citation_roles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, _, _, _ = pair(tmp_path)
    plan = tmp_path / "contract.json"
    plan.write_text('{"terms":["reader"]}', encoding="utf-8")
    assert main(["discover", "contract", str(directory), "--input", str(plan)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "reader.py" in data["paths"]["donor"]["paths"]
    assert data["citation_schema"]["properties"]["source_evidence"]["maxItems"] == 0
    assert "pending/backlog" not in data["capabilities"]


def test_inspection_required_review_cannot_attest_to_unread_continuation(tmp_path: Path) -> None:
    directory, _, maw, finding = pair(tmp_path)
    snapshot = json.loads((directory / "snapshot.json").read_text())
    directory = initialize_exploration(
        directory.parent.parent, {**snapshot["plan"], "require_inspection_receipts": True}, maw=maw
    )
    payload = {
        **finding,
        "kind": "decision",
        "reviewed": True,
        "hypothesis": "bounded original flow",
    }
    with pytest.raises(CrabError, match="uninspected"):
        record(directory, payload)
    inspect(directory, read_plan("donor", "reader.py", "first-two-lines"))
    inspect(directory, read_plan("receiving", "app.py", "receiver"))
    with pytest.raises(CrabError, match="uninspected"):
        record(directory, payload)
    plan = read_plan("donor", "reader.py", "continue")
    plan["ranges"] = [{"path": "reader.py", "start": 1, "end": 3}]
    inspect(directory, plan)
    record(directory, payload)
    assert report(directory)["examined_hypotheses"] == ["bounded original flow"]
