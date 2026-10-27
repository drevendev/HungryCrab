"""A small offline fixture for frozen paired reporting, not a discovery quality result."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import paired_report
import pytest
from test_exploration import pair

from hungry_crab.analysis_context import fingerprint, installed_source_identity, ledger_identity
from hungry_crab.errors import CrabError
from hungry_crab.exploration import register


def experiment(tmp_path: Path, *, changed_input: str = "") -> tuple[Path, dict[str, str]]:
    directory, donor, _, finding = pair(
        tmp_path / "fixture", now=datetime(2026, 10, 27, tzinfo=UTC)
    )
    register(directory, finding, source_repo=donor)
    meal = directory.parent.parent
    info = json.loads((meal / "meal.json").read_text())
    raw = tmp_path / "raw"
    shutil.copytree(meal, raw)
    for side in ("prey", "maw"):
        shutil.copytree(info[f"{side}_digest"], raw / f"{side}-digest")
    source = installed_source_identity()
    current = json.loads((raw / "menu.json").read_text())
    manifests = {
        side: json.loads((raw / f"{side}-digest" / "manifest.json").read_text())
        for side in ("prey", "maw")
    }
    plan = {
        "arms": ["a", "b"],
        "pairs": ["synthetic"],
        "repeats": 2,
        "arm_sources": {"a": source, "b": source},
        "shared_inputs": {
            "as_of": current["generated_at"],
            "receiving": {
                "synthetic": {
                    "sha": current["maw"]["sha"],
                    "worktree": "clean",
                    "selected_ref": None
                    if changed_input == "selected-ref"
                    else current["receiving_context"]["selected_ref"],
                }
            },
            "prey": {"synthetic": {"sha": current["prey"]["sha"]}},
            "policy": {"synthetic": fingerprint(current["analysis_inputs"]["policy"])},
            "ledger": {"synthetic": ledger_identity(current["analysis_inputs"]["ledger"])},
            "backlog": {"status": "not-acquired"},
            "coverage": "synthetic complete source snapshot",
            "limits": {"hypotheses": 2},
            "held_out_protocol": "synthetic controls only",
            "digest_inputs": {
                "synthetic": {
                    side: paired_report.digest_input_identity(manifest)
                    for side, manifest in manifests.items()
                }
            },
        },
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    root = paired_report.freeze(plan_path, tmp_path / "experiment")
    if changed_input and changed_input != "selected-ref":
        manifest = manifests["prey"]
        if changed_input == "depth":
            manifest["depth"] = "deep"
        elif changed_input == "budget":
            manifest["budget"]["markdown_total"] += 1
        elif changed_input == "api":
            manifest["analysis_identity"]["api_sha256"] = "0" * 64
        (raw / "prey-digest" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    costs = tmp_path / "measurements.json"
    costs.write_text(
        json.dumps(
            {"wall_seconds": 1.25, "acquisition_wall_seconds": 3.0, "installed_source": source}
        ),
        encoding="utf-8",
    )
    schedule = json.loads((root / "experiment.json").read_text())["schedule"]
    roots = {}
    for run in schedule:
        paired_report.seal(root, run["id"], raw, costs)
        roots[run["id"]] = str(raw)
    return root, roots


def test_paired_tokens_duplicates_and_relocation_preserve_raw_bytes(tmp_path: Path) -> None:
    root, roots = experiment(tmp_path)
    original = Path(next(iter(roots.values())))
    before = paired_report._inventory(original)
    result = paired_report.report(root, roots)
    manifest = json.loads((original / "prey-digest" / "manifest.json").read_text())
    assert (
        result["runs"][0]["tokens"]["prey"]["markdown_tokens_est"]
        == manifest["markdown_tokens_est"]
    )
    assert result["runs"][0]["costs"]["cost_usd"] is None
    assert result["runs"][0]["costs"]["model_tokens_in"] is None
    assert result["runs"][0]["costs"]["acquisition_wall_seconds"] == 3.0
    assert result["runs"][0]["costs"]["wall_seconds"] == 1.25
    assert result["unique_artifact_bundles"] == 1
    assert result["receiving_scoped_ids"] == 1
    assert all(pair["equal"] for pair in result["semantic_equal_pairs"])
    relocated = tmp_path / "relocated"
    shutil.copytree(original, relocated)
    moved = paired_report.report(root, dict.fromkeys(roots, str(relocated)))
    assert [run["tokens"] for run in moved["runs"]] == [run["tokens"] for run in result["runs"]]
    assert [run["raw_sha256"] for run in moved["runs"]] == [
        run["raw_sha256"] for run in result["runs"]
    ]
    assert [run["review"]["counts"] for run in moved["runs"]] == [
        run["review"]["counts"] for run in result["runs"]
    ]
    assert paired_report._inventory(original) == before
    assert result["runs"][0]["review"]["counts"]["registered_occurrences"] == 1


def test_corrupted_raw_or_receipt_is_refused(tmp_path: Path) -> None:
    root, roots = experiment(tmp_path)
    raw = Path(next(iter(roots.values())))
    (raw / "menu.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CrabError, match="changed after sealing"):
        paired_report.report(root, roots)
    receipt = root / (next(iter(roots)) + ".json")
    receipt.write_text("{}", encoding="utf-8")
    with pytest.raises(CrabError, match="stale or corrupt"):
        paired_report.report(root, roots)


def test_measurement_helper_does_not_invent_model_usage() -> None:
    value, costs = paired_report.measure(lambda: "authored fixture only")
    assert value == "authored fixture only"
    assert costs["wall_seconds"] >= 0 and costs["cpu_self_seconds"] >= 0
    assert costs["cost_usd"] is None and costs["model_tokens_in"] is None
    assert len(costs["installed_source"]["source_sha256"]) == 64


def test_missing_manifest_fields_are_unknown_and_wrong_old_name_is_not_zero() -> None:
    assert paired_report.token_estimates({"md_tokens_est": 123}) == {
        "markdown_tokens_est": None,
        "total_tokens_est": None,
    }
    assert (
        paired_report.token_estimates({"markdown_tokens_est": 7, "total_tokens_est": 12})[
            "markdown_tokens_est"
        ]
        == 7
    )
    with pytest.raises(CrabError, match="token estimate"):
        paired_report.token_estimates({"markdown_tokens_est": True})


@pytest.mark.parametrize("changed_input", ["depth", "budget", "api", "selected-ref"])
def test_changed_effective_inputs_cannot_be_certified_as_a_frozen_pair(
    tmp_path: Path, changed_input: str
) -> None:
    root, roots = experiment(tmp_path, changed_input=changed_input)
    with pytest.raises(CrabError, match="frozen"):
        paired_report.report(root, roots)
