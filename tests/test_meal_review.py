from __future__ import annotations

import copy
from pathlib import Path

from test_exploration import observation, pair
from test_multifeed import menu

from hungry_crab.compare import load_menu
from hungry_crab.exploration import record, register
from hungry_crab.meal_review import project_menu
from hungry_crab.multifeed import merge_menus
from hungry_crab.report import render_report


def test_registration_joins_derived_menu_and_separate_phase_observations(tmp_path: Path) -> None:
    directory, donor, _, finding = pair(tmp_path)
    registered = register(directory, finding, source_repo=donor)
    nutrient_id = registered["record"]["card"]["id"]
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="bounded reads",
            candidate_id=nutrient_id,
            disposition="verified-gap",
            reviewed=True,
            selected=True,
        ),
    )
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="another question",
            disposition="budget-deferred",
            reviewed=False,
        ),
    )
    record(
        directory,
        observation(
            finding,
            kind="phase",
            phase="publication",
            state="PASS",
            repository="target",
            artifact="PR 1",
            method="recorded provider observation",
        ),
    )
    current = load_menu(directory.parent.parent)
    assert current is not None
    projected = project_menu(current, directory.parent.parent)
    assert projected["cards"][nutrient_id]["review"] == "analyst-reviewed"
    assert projected["counts"]["selected_changes"] == 1
    assert projected["counts"]["publication_observations"] == 1
    assert projected["counts"]["default_acceptance_observations"] is None
    assert projected["explorations"][0]["unresolved_hypotheses"] == ["another question"]
    assert not projected["explorations"][0]["exploration_complete"]
    rendered = render_report(directory.parent.parent)
    assert "analyst-reviewed" in rendered and "another question" in rendered
    assert "budget-deferred" in rendered and "verified-gap" in rendered
    assert "not independent provider verification" in rendered
    unrelated = copy.deepcopy(current)
    unrelated["maw"]["sha"] = "f" * 40
    assert project_menu(unrelated, directory.parent.parent)["explorations"] == []


def test_prose_and_permissive_source_do_not_establish_readiness() -> None:
    sources = {"a": menu("x/a"), "b": menu("x/b", "HUMAN", score=0.01)}
    sources["a"]["candidates"][0].update(why="arbitrary analyst text", how="already tested")
    aggregate = merge_menus(sources)
    result = project_menu(aggregate)
    assert result["counts"]["visible_occurrences"] == 2
    assert result["counts"]["visible_unique_ids"] == 1
    assert result["cards"][aggregate["candidates"][0]["id"]]["review"] == "structural-seed"
    assert aggregate["candidates"][0]["license_mode"] == "HUMAN"
    assert result["counts"]["selected_changes"] is None


def test_later_hold_cannot_leave_a_conflicting_candidate_counted_as_selected(
    tmp_path: Path,
) -> None:
    directory, donor, _, finding = pair(tmp_path)
    nutrient_id = register(directory, finding, source_repo=donor)["record"]["card"]["id"]
    for hypothesis, disposition, reviewed, selected in (
        ("bounded reads", "verified-gap", True, True),
        ("receiving policy", "policy-blocked", False, False),
    ):
        record(
            directory,
            observation(
                finding,
                kind="decision",
                hypothesis=hypothesis,
                candidate_id=nutrient_id,
                disposition=disposition,
                reviewed=reviewed,
                selected=selected,
            ),
        )
    current = load_menu(directory.parent.parent)
    assert current is not None
    projected = project_menu(current, directory.parent.parent)
    assert projected["counts"]["selected_changes"] is None
    assert projected["cards"][nutrient_id]["selection"] == "conflicting-selection"
    assert projected["cards"][nutrient_id]["review"] == "conflicting-review"
    assert projected["counts"]["selection_conflicts"] == 1
    assert "conflicting-selection" in render_report(directory.parent.parent)
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="receiving policy",
            candidate_id=nutrient_id,
            disposition="verified-gap",
            reviewed=True,
            selected=True,
        ),
    )
    resolved = project_menu(current, directory.parent.parent)
    assert resolved["counts"]["selected_changes"] == 1
    assert resolved["counts"]["selection_conflicts"] == 0


def test_current_unresolved_decision_does_not_inherit_registered_review(tmp_path: Path) -> None:
    directory, donor, _, finding = pair(tmp_path)
    nutrient_id = register(directory, finding, source_repo=donor)["record"]["card"]["id"]
    record(
        directory,
        observation(
            finding,
            kind="decision",
            hypothesis="remaining investigation",
            candidate_id=nutrient_id,
            disposition="budget-deferred",
            reviewed=False,
            selected=False,
        ),
    )
    current = load_menu(directory.parent.parent)
    assert current is not None
    projected = project_menu(current, directory.parent.parent)
    assert projected["cards"][nutrient_id]["review"] == "analyst-unresolved"
    assert projected["counts"]["selected_changes"] == 0
