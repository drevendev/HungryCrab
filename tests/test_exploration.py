"""Coverage and a non-trait streaming gap; donor source is always inert data."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from test_discovery_inputs import repository

from hungry_crab.cache import Target
from hungry_crab.compare import compare_for_maw, load_menu
from hungry_crab.digest import DigestOptions
from hungry_crab.errors import CrabError
from hungry_crab.exploration import events, initialize, record, register, report
from hungry_crab.nutrients import Candidate

PLAN = {
    "scope": "bounded streaming for one receiving flow",
    "goals": "bounded memory",
    "flows": "read user input",
    "constraints": "stdlib; donor inert; no provider effects",
    "facets": ["performance-streaming"],
    "author": "synthetic operator",
    "attempt": "control-1",
    "donor_policy": "MIT-only",
}
BUFFERED = "def consume(stream):\n    return stream.read()\n"
STREAMING = (
    "def consume(stream):\n"
    "    for chunk in iter(lambda: stream.read(4096), b''):\n        yield chunk\n"
)


def pair(
    tmp_path: Path,
    *,
    gap: bool = True,
    hunger: str = "ideas-only",
    header: str = "",
    now: datetime | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    donor = repository(
        tmp_path / "donor",
        {
            "reader.py": (
                header + "def blocks(stream):\n"
                "    while chunk := stream.read(1024):\n        yield chunk\n"
            )
        },
    )
    maw = repository(
        tmp_path / "maw",
        {
            "app.py": BUFFERED if gap else STREAMING,
            ".crab.yml": f"ledger: none\nhunger:\n  code: {hunger}\n",
        },
    )
    result, _, _, _ = compare_for_maw(
        Target(path=donor),
        maw,
        digest_options=DigestOptions(cache_root=tmp_path / "cache", now=now),
        now=now,
        record_ledger=False,
    )
    assert result.meal_dir is not None
    directory = initialize(result.meal_dir, PLAN, maw=maw)
    menu = result.menu
    finding = {
        "key": "code.bounded-input",
        "title": "Bound input buffering",
        "behavior": "the donor reads bounded blocks",
        "gap": "the receiving function reads the entire input",
        "adaptation": "iterate over bounded reads in the existing flow",
        "maintenance": "one stdlib helper; no new package",
        "acceptance": "an input reader refuses unbounded reads and the output is unchanged",
        "author": "synthetic analyst",
        "tool": "pytest synthetic control",
        "attempt": "control-1",
        "reason": "bound memory for large input",
        "disposition": "verified-gap",
        "source_evidence": [
            {"sha": menu["prey"]["sha"], "path": "reader.py", "start": 1, "end": 3}
        ],
        "maw_evidence": [{"sha": menu["maw"]["sha"], "path": "app.py", "start": 1, "end": 2}],
    }
    return directory, donor, maw, finding


def observation(finding: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {
        "author": finding["author"],
        "tool": finding["tool"],
        "attempt": finding["attempt"],
        "reason": finding["reason"],
        "source_evidence": finding["source_evidence"],
        "maw_evidence": finding["maw_evidence"],
        **changes,
    }


def test_successful_shallow_comparisons_and_zero_cards_do_not_close_exploration(
    tmp_path: Path,
) -> None:
    directory, _, _, finding = pair(tmp_path)
    for n in range(100):
        record(
            directory,
            observation(
                finding,
                kind="phase",
                phase="comparison",
                state="PASS",
                repository="receiving",
                artifact=str(n),
                method="shallow structural comparison",
            ),
        )
    result = report(directory)
    assert result["denominator"] == 0
    assert result["pending_facets"] == ["performance-streaming"]
    assert not result["exploration_complete"]


def test_category_rejection_cannot_claim_individual_review(tmp_path: Path) -> None:
    directory, _, _, finding = pair(tmp_path)
    entry = observation(
        finding,
        kind="decision",
        hypothesis="bounded reads",
        disposition="out-of-scope",
        reviewed=True,
    )
    del entry["source_evidence"]
    with pytest.raises(CrabError, match="individual review requires"):
        record(directory, entry)
    entry["reviewed"] = False
    record(directory, entry)
    assert not report(directory)["exploration_complete"]


def test_genuine_evidenced_no_gap_control(tmp_path: Path) -> None:
    directory, donor, _, finding = pair(tmp_path, gap=False)
    record(
        directory,
        observation(
            finding,
            kind="facet",
            facet="performance-streaming",
            state="examined",
            hypothesis="bounded reads",
        ),
    )
    assert not report(directory)["exploration_complete"]
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="bounded reads",
            disposition="NO_GAP",
            examined_scope="app.consume uses bounded reads",
            reviewed=True,
        ),
    )
    assert report(directory)["exploration_complete"]
    finding["disposition"] = "already-present"
    with pytest.raises(CrabError, match="only an evidenced verified gap"):
        register(directory, finding, source_repo=donor)
    assert not any(event["record"]["kind"] == "registration" for event in events(directory))


def test_multiple_hypotheses_in_one_facet_remain_independently_pending(tmp_path: Path) -> None:
    directory, _, _, finding = pair(tmp_path, gap=False)
    for hypothesis, disposition, reviewed in (
        ("first deferred", "budget-deferred", False),
        ("second completed", "NO_GAP", True),
    ):
        record(
            directory,
            observation(
                finding,
                kind="facet",
                facet="performance-streaming",
                state="examined",
                hypothesis=hypothesis,
            ),
        )
        record(
            directory,
            observation(
                finding,
                kind="decision",
                hypothesis=hypothesis,
                disposition=disposition,
                reviewed=reviewed,
                **({"examined_scope": "bounded reads verified"} if reviewed else {}),
            ),
        )
    unresolved = report(directory)
    assert unresolved["pending_facets"] == []
    assert unresolved["unresolved_hypotheses"] == ["first deferred"]
    assert not unresolved["exploration_complete"]
    assert unresolved["events"] == 4

    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="first deferred",
            disposition="NO_GAP",
            reviewed=True,
            examined_scope="bounded reads verified on later review",
        ),
    )
    resolved = report(directory)
    assert resolved["unresolved_hypotheses"] == []
    assert resolved["exploration_complete"]
    assert resolved["events"] == 5


def test_later_inapplicable_facet_cannot_erase_unreviewed_work(tmp_path: Path) -> None:
    directory, _, _, finding = pair(tmp_path, gap=False)
    record(
        directory,
        observation(
            finding,
            kind="facet",
            facet="performance-streaming",
            state="examined",
            hypothesis="earlier unreviewed",
        ),
    )
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="earlier unreviewed",
            disposition="budget-deferred",
            reviewed=False,
        ),
    )
    record(
        directory,
        observation(finding, kind="facet", facet="performance-streaming", state="inapplicable"),
    )
    pending = report(directory)
    assert pending["facets"]["performance-streaming"] == "inapplicable"
    assert pending["pending_facets"] == []
    assert pending["unresolved_hypotheses"] == ["earlier unreviewed"]
    assert not pending["exploration_complete"]

    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="earlier unreviewed",
            disposition="NO_GAP",
            reviewed=True,
            examined_scope="later review resolves the same hypothesis",
        ),
    )
    assert report(directory)["exploration_complete"]


def test_decision_only_deferred_hypothesis_is_not_dropped(tmp_path: Path) -> None:
    directory, _, _, finding = pair(tmp_path, gap=False)
    assert report(directory)["unresolved_hypotheses"] == []
    record(
        directory,
        observation(finding, kind="facet", facet="performance-streaming", state="inapplicable"),
    )
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="decision without facet",
            disposition="not-investigated",
            reviewed=False,
        ),
    )
    pending = report(directory)
    assert pending["pending_facets"] == []
    assert pending["unresolved_hypotheses"] == ["decision without facet"]
    assert not pending["exploration_complete"]
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="decision without facet",
            disposition="NO_GAP",
            reviewed=True,
            examined_scope="explicitly inspected despite missing facet hypothesis",
        ),
    )
    assert report(directory)["exploration_complete"]


def test_different_facets_keep_independent_unresolved_history(tmp_path: Path) -> None:
    original, _, maw, finding = pair(tmp_path, gap=False)
    directory = initialize(
        original.parent.parent,
        {**PLAN, "facets": ["performance-streaming", "testing"]},
        maw=maw,
    )
    baseline = report(directory)
    assert baseline["unresolved_hypotheses"] == []
    assert baseline["pending_facets"] == ["performance-streaming", "testing"]
    for facet, hypothesis in (
        ("performance-streaming", "stream hypothesis"),
        ("testing", "test hypothesis"),
    ):
        record(
            directory,
            observation(
                finding,
                kind="facet",
                facet=facet,
                state="examined",
                hypothesis=hypothesis,
            ),
        )
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="stream hypothesis",
            disposition="NO_GAP",
            reviewed=True,
            examined_scope="bounded read verified",
        ),
    )
    pending = report(directory)
    assert pending["pending_facets"] == []
    assert pending["unresolved_hypotheses"] == ["test hypothesis"]
    assert not pending["exploration_complete"]


def test_non_trait_gap_registers_with_immutable_source_and_default_hunger(tmp_path: Path) -> None:
    directory, donor, _, finding = pair(tmp_path)
    event = register(directory, finding, source_repo=donor)
    card = event["record"]["card"]
    assert card["id"] == "crab:code:code.bounded-input"
    assert card["license_mode"] == "COPY" and card["origin"] == "licensed"
    assert card["serve_as"] == "idea"
    assert card["trace"]["exploration_snapshot"] == directory.name
    assert card["evidence"][0]["path"] == "reader.py"
    assert register(directory, finding, source_repo=donor) == event
    menu = load_menu(directory.parent.parent)
    assert menu is not None and any(c["id"] == card["id"] for c in menu["candidates"])
    assert menu["initial_candidates"] == [] and report(directory)["denominator"] == 0
    assert not report(directory)["exploration_complete"]


def test_hunger_hold_is_visible_and_does_not_change_policy(tmp_path: Path) -> None:
    directory, donor, maw, finding = pair(tmp_path, hunger="false")
    original = (maw / ".crab.yml").read_bytes()
    event = register(directory, finding, source_repo=donor)
    assert event["record"]["policy_blocked"]
    menu = load_menu(directory.parent.parent)
    assert menu is not None and menu["discovered_candidates"] and not menu["candidates"]
    assert (maw / ".crab.yml").read_bytes() == original


@pytest.mark.parametrize("origin", ["commenter", "unknown"])
def test_registration_cannot_widen_an_explicit_content_origin(tmp_path: Path, origin: str) -> None:
    directory, donor, _, finding = pair(tmp_path)
    finding["origin"] = origin
    with pytest.raises(CrabError, match="origins cannot be widened"):
        register(directory, finding, source_repo=donor)
    assert not events(directory)


def test_registration_rechecks_operative_file_grant(tmp_path: Path) -> None:
    directory, donor, _, finding = pair(
        tmp_path, header="# SPDX-License-Identifier: GPL-3.0-only\n"
    )
    with pytest.raises(CrabError, match=r"licen[cs]e|SPDX"):
        register(directory, finding, source_repo=donor)
    assert not events(directory)


@pytest.mark.parametrize("side", ["source_evidence", "maw_evidence"])
@pytest.mark.parametrize(
    "field,value", [("sha", "f" * 40), ("path", "../secret"), ("path", "absent.py"), ("end", 500)]
)
def test_fabricated_ranges_do_not_register(
    tmp_path: Path, side: str, field: str, value: Any
) -> None:
    directory, donor, _, finding = pair(tmp_path)
    finding[side][0][field] = value
    with pytest.raises(CrabError, match="valid immutable donor and receiving file ranges"):
        register(directory, finding, source_repo=donor)
    assert not events(directory)


def test_observations_keep_target_companion_and_ui_failure_separate(tmp_path: Path) -> None:
    directory, _, _, finding = pair(tmp_path)
    for repository_name, state in (("target", "PASS"), ("acquisition-companion", "FAIL")):
        record(
            directory,
            observation(
                finding,
                kind="phase",
                phase="review-ci",
                state=state,
                repository=repository_name,
                artifact="a" * 40,
                method="GitHub run",
                workflow="CI",
                job="tests",
                runner="ubuntu",
                executed_steps=["pytest"],
            ),
        )
    for method, state in (("GitHub publication", "PASS"), ("app attachment timeout", "FAIL")):
        record(
            directory,
            observation(
                finding,
                kind="phase",
                phase="publication",
                state=state,
                repository="target",
                artifact="PR 1",
                method=method,
            ),
        )
    result = report(directory)
    assert [e["state"] for e in result["phases"]["review-ci"]] == ["PASS", "FAIL"]
    assert [e["method"] for e in result["phases"]["publication"]] == [
        "GitHub publication",
        "app attachment timeout",
    ]
    assert result["phases"]["default-acceptance"] == "NOT_RUN"
    assert result["learning_audit"]["exploration_writes_learning"] is False


def test_event_history_is_append_only_and_content_bound(tmp_path: Path) -> None:
    directory, _, _, finding = pair(tmp_path)
    entry = observation(
        finding, kind="facet", facet="performance-streaming", state="budget-deferred"
    )
    first = record(directory, entry)
    assert record(directory, entry) == first
    path = directory / "event-00001.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["record"]["reason"] = "a retroactively invented reason"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CrabError, match="chain is inconsistent"):
        report(directory)


def test_later_served_dedup_preserves_original_denominator(tmp_path: Path) -> None:
    donor = repository(
        tmp_path / "donor",
        {"app.py": "pass\n", ".github/dependabot.yml": "version: 2\nupdates: []\n"},
    )
    maw = repository(
        tmp_path / "maw",
        {"app.py": "pass\n", ".crab.yml": "ledger: repo\n", ".gitignore": ".crab/\n"},
    )
    options = DigestOptions(cache_root=tmp_path / "cache")
    first, _, ledger, _ = compare_for_maw(Target(path=donor), maw, digest_options=options)
    assert first.meal_dir is not None and first.menu["candidates"]
    directory = initialize(first.meal_dir, PLAN, maw=maw)
    original = (directory / "snapshot.json").read_bytes()
    card = Candidate.from_dict(first.menu["candidates"][0])
    ledger.mark(card.id, "served", url="https://example.invalid/issues/1")
    ledger.entries[card.id].feedback = "rejected"  # A legacy vote without exploration evidence.
    ledger.save()
    later, _, _, _ = compare_for_maw(Target(path=donor), maw, digest_options=options)
    assert later.meal_dir is not None
    later_directory = initialize(later.meal_dir, PLAN, maw=maw)
    assert card.id not in {c["id"] for c in later.menu["candidates"]}
    assert card.id in report(later_directory)["unreviewed_candidates"]
    assert report(later_directory)["denominator"] == report(directory)["denominator"]
    assert (directory / "snapshot.json").read_bytes() == original
    assert later_directory != directory
    assert not report(later_directory)["exploration_complete"]
    audit = report(later_directory)["learning_audit"]
    assert audit["actual_learning_input_ids"] == [card.id]
    assert audit["unverified_legacy_feedback"] == [card.id]
    assert ledger.path is not None
    before = ledger.path.read_bytes()
    record(
        later_directory,
        {
            "kind": "decision",
            "hypothesis": "another uninspected behavior",
            "disposition": "not-investigated",
            "reviewed": False,
            "author": "synthetic operator",
            "tool": "pytest",
            "attempt": "no-learning-control",
            "reason": "no source investigation took place",
        },
    )
    assert ledger.path.read_bytes() == before


def test_dirty_and_policy_inputs_have_distinct_identities_at_the_same_commit(
    tmp_path: Path,
) -> None:
    directory, donor, maw, _ = pair(tmp_path)
    baseline = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    (maw / "app.py").write_text(STREAMING, encoding="utf-8")
    with pytest.raises(CrabError, match="maw changed"):
        initialize(directory.parent.parent, PLAN, maw=maw)

    def snapshot() -> dict[str, Any]:
        result, _, _, _ = compare_for_maw(
            Target(path=donor),
            maw,
            digest_options=DigestOptions(cache_root=tmp_path / "cache"),
            record_ledger=False,
        )
        assert result.meal_dir is not None
        path = initialize(result.meal_dir, PLAN, maw=maw) / "snapshot.json"
        return json.loads(path.read_text(encoding="utf-8"))

    dirty = snapshot()
    (maw / ".crab.yml").write_text("ledger: none\nhunger:\n  code: false\n", encoding="utf-8")
    policy = snapshot()
    assert (
        baseline["menu"]["maw"]["sha"]
        == dirty["menu"]["maw"]["sha"]
        == policy["menu"]["maw"]["sha"]
    )
    assert baseline["identities"]["maw_worktree"] != dirty["identities"]["maw_worktree"]
    assert dirty["identities"]["policy"] != policy["identities"]["policy"]
    assert (directory / "snapshot.json").read_text(encoding="utf-8")


def test_receiving_adaptation_has_a_real_bounded_read_control() -> None:
    class Reader:
        def __init__(self) -> None:
            self.remaining = 9000

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 4096, "unbounded input buffering"
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

    namespace: dict[str, Any] = {}
    # Execute only the explicitly authored synthetic receiving implementation.
    exec(BUFFERED, namespace)
    with pytest.raises(AssertionError, match="unbounded"):
        namespace["consume"](Reader())
    exec(STREAMING, namespace)
    assert b"".join(namespace["consume"](Reader())) == b"x" * 9000
