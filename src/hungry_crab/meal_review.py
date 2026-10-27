"""Evidence-state projections shared by offline HTML, CLI and experiment reporting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis_context import fingerprint
from .artifact_read import read_object
from .errors import CrabError
from .exploration import PHASES, matching_reports
from .typeutil import as_dict, as_list


def _sources(
    menu: dict[str, Any], directory: Path | None
) -> list[tuple[dict[str, Any], Path | None]]:
    if menu.get("kind") != "multi-prey":
        return [(menu, directory)]
    if directory is None or not (directory / "feeder.json").is_file():
        return []
    from .multifeed import load_multi

    _, paths, _ = load_multi(directory)
    return [(read_object(path / "menu.json"), path) for path in paths.values()]


def _selection(decisions: list[dict[str, Any]]) -> str:
    choices = {entry["selected"] for entry in decisions if "selected" in entry}
    if len(choices) > 1 or (
        True in choices
        and any(
            not entry.get("reviewed") or entry.get("disposition") != "verified-gap"
            for entry in decisions
        )
    ):
        return "conflicting-selection"
    return "selected" if choices == {True} else "not-selected" if choices == {False} else "unknown"


def _source_reviewed(report: dict[str, Any], nutrient_id: str) -> bool:
    decisions = [item for item in report["decisions"] if item.get("candidate_id") == nutrient_id]
    if decisions:
        return all(item.get("reviewed") for item in decisions)
    return any(
        as_dict(item.get("card")).get("id") == nutrient_id for item in report["registrations"]
    )


def project_menu(menu: dict[str, Any], directory: Path | None = None) -> dict[str, Any]:
    menu = {key: value for key, value in menu.items() if key != "analysis_review"}
    rows = [as_dict(row) for row in as_list(menu.get("candidates"))]
    sources = _sources(menu, directory)
    reports: list[dict[str, Any]] = []
    diagnostics = []
    unknown = (
        len(as_list(menu.get("preys"))) if not sources and menu.get("kind") == "multi-prey" else 0
    )
    initial: int | None = 0 if sources else None
    registered = 0
    for source, path in sources:
        original = source.get("initial_candidates")
        initial = (
            initial + len(original) if initial is not None and isinstance(original, list) else None
        )
        registered += len(as_list(source.get("discovered_candidates")))
        try:
            bound = matching_reports(path, source) if path is not None else []
        except (CrabError, KeyError, TypeError, ValueError):
            bound = []
            diagnostics.append(
                "Exploration evidence is unreadable or inconsistent; review is unknown."
            )
        unknown += not bool(bound)
        source_key = fingerprint(source.get("prey"))
        reports.extend({**item, "source_key": source_key} for item in bound)
    states = {}
    for row in rows:
        nutrient_id = str(row.get("id", ""))
        decisions = [
            decision
            for report in reports
            for decision in report["decisions"]
            if decision.get("candidate_id") == nutrient_id
        ]
        registrations = [
            entry
            for report in reports
            for entry in report["registrations"]
            if as_dict(entry.get("card")).get("id") == nutrient_id
        ]
        dispositions = sorted({str(item["disposition"]) for item in decisions})
        reviewed = bool(decisions) and all(item.get("reviewed") for item in decisions)
        review = "structural-seed"
        if len(dispositions) > 1:
            review = "conflicting-review"
        elif reviewed:
            review = "analyst-reviewed"
        elif decisions:
            review = "analyst-unresolved"
        elif registrations:
            review = "analyst-registered"
        # Source-specific judgement cannot be promoted to the aggregate's restrictive source.
        if menu.get("kind") == "multi-prey" and as_list(as_dict(row.get("trace")).get("sources")):
            expected_sources = {
                fingerprint(source.get("prey")) for source in row["trace"]["sources"]
            }
            reviewed_sources = {
                report["source_key"] for report in reports if _source_reviewed(report, nutrient_id)
            }
            if not expected_sources <= reviewed_sources:
                review = "source-review-unknown" if review != "structural-seed" else review
        states[nutrient_id] = {
            "review": review,
            "selection": _selection(decisions),
            "dispositions": dispositions,
            "independently_verified": False,
        }
    occurrences = sum(
        max(1, len(as_list(as_dict(row.get("trace")).get("sources")))) for row in rows
    )
    phases = {
        phase: [entry for report in reports for entry in as_list(report["phases"].get(phase))]
        for phase in PHASES
    }
    candidate_decisions: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        for entry in report["decisions"]:
            if entry.get("candidate_id"):
                candidate_decisions.setdefault(entry["candidate_id"], []).append(entry)
    selections = [_selection(entries) for entries in candidate_decisions.values()]
    selection_observed = any(state != "unknown" for state in selections)
    return {
        "counts": {
            "initial_occurrences": initial,
            "visible_occurrences": occurrences,
            "visible_unique_ids": len(rows),
            "hidden_occurrences": len(as_list(menu.get("hidden"))),
            "registered_occurrences": registered,
            "examined_hypotheses": sum(len(r["examined_hypotheses"]) for r in reports),
            "selected_changes": selections.count("selected")
            if selection_observed and "conflicting-selection" not in selections
            else None,
            "selection_conflicts": selections.count("conflicting-selection"),
            "implementation_observations": len(phases["implementation"]) or None,
            "ci_observations": len(phases["review-ci"]) or None,
            "publication_observations": len(phases["publication"]) or None,
            "default_acceptance_observations": len(phases["default-acceptance"]) or None,
        },
        "cards": states,
        "explorations": [
            {
                key: r.get(key)
                for key in (
                    "snapshot",
                    "scope",
                    "report_path",
                    "pending_facets",
                    "unresolved_hypotheses",
                    "unreviewed_candidates",
                    "exploration_complete",
                    "decisions",
                    "examined_hypotheses",
                )
            }
            for r in reports
        ],
        "phases": phases,
        "phase_evidence": "recorded analyst attestations; not independent provider verification",
        "unknown_source_explorations": unknown,
        "receiving_context": menu.get("receiving_context"),
        "context_status": (
            "legacy-unknown"
            if not menu.get("receiving_context")
            else "unknown"
            if not isinstance(as_dict(menu["receiving_context"]).get("worktree"), str)
            or as_dict(menu["receiving_context"])["worktree"] in {"", "unknown"}
            else "observed"
        ),
        "as_of": menu.get("generated_at"),
        "evidence_scope": "frozen snapshot; current checkout/provider truth is not rechecked",
        "diagnostics": diagnostics,
    }
