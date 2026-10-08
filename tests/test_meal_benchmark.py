from __future__ import annotations

import json
from pathlib import Path

import meal_benchmark as b2
import pytest
from fixture_builder import git as fixture_git


def prepare(tmp_path: Path, prey: Path) -> Path:
    (tmp_path / "prompt.md").write_text("Frozen fixture prompt; output nutrient cards.\n")
    (tmp_path / "rubric.md").write_text("Frozen fixture rubric.\n")
    sha = fixture_git(prey, "rev-parse", "HEAD").strip()
    spec = {
        "maw": {"sha": sha},
        "prey": [
            {
                "id": "fixture",
                "sha": sha,
                "license_mode": "IDEAS_ONLY",
                "must": ["crab:ci:ci.schedule"],
            }
        ],
        "rubric": "rubric.md",
        "repeats": 2,
        "arms": [
            {
                "id": arm,
                "model": f"fixture-model-{arm}",
                "harness": {"name": "fixture", "token_ceiling": 1000},
                "crab_sha": sha if arm == "A2" else None,
                "prompt": "prompt.md",
            }
            for arm in ("A0", "A2")
        ],
    }
    b2.write(tmp_path / "spec.json", spec)
    sweep = tmp_path / "sweep"
    setup = b2.freeze(tmp_path / "spec.json", sweep)
    cards = [
        {
            "id": "crab:ci:ci.schedule",
            "category": "ci",
            "title": "Schedule CI",
            "what": "Fixture proposal",
            "why": "Fixture need",
            "how": "Fixture step",
            "evidence": [{"path": "src/pycli/core.py", "url": None}],
            "license_mode": "IDEAS_ONLY",
            "effort": "S",
            "risk": "low",
            "trace": {"arm": "A2", "model": "must disappear"},
        }
    ]
    b2.write(tmp_path / "cards.json", cards)
    b2.write(
        tmp_path / "usage.json",
        {"wall_seconds": 2, "tokens_in": 80, "tokens_out": 20, "cost_usd": 0.01},
    )
    (tmp_path / "transcript.log").write_text(
        "Synthetic harness transcript for infrastructure tests only.\n"
    )
    for run in setup["runs"]:
        b2.record(
            sweep,
            run["id"],
            tmp_path / "cards.json",
            tmp_path / "usage.json",
            tmp_path / "transcript.log",
        )
    return sweep


def grade(sweep: Path, private: dict[str, object]) -> None:
    value = [
        {
            "id": identity,
            "useful": True,
            "garbage": False,
            "quality": 3,
            "reason": "Fixture judgment",
        }
        for identity in private
    ]
    b2.write(sweep / "judged/value.json", value)
    b2.write(sweep / "judged/value-repeat.json", value)
    b2.write(
        sweep / "judged/facts.json",
        [{"id": identity, "evidence_ok": True, "license_ok": True} for identity in private],
    )
    audit_ids = b2.read(sweep / "blind/audit-ids.json")
    b2.write(sweep / "judged/audit.json", [v for v in value if v["id"] in audit_ids])


def test_complete_b2_pipeline_with_synthetic_harness_data(
    tmp_path: Path, pyproject_cli: Path
) -> None:
    sweep = prepare(tmp_path, pyproject_cli)
    private = b2.pool(sweep)
    blind = b2.read(sweep / "blind/fixture.value.json")
    assert len(blind) == 4
    assert {c["id"] for c in blind} == set(private)
    assert all(not ({"license_mode", "trace", "arm", "model"} & c.keys()) for c in blind)
    assert all(c["evidence"] == [{"path": "src/pycli/core.py"}] for c in blind)
    assert "crab:ci:ci.schedule" not in json.dumps(blind)
    grade(sweep, private)
    result = b2.report(sweep, {"fixture": str(pyproject_cli)})
    assert result["valid"] and result["judge_self_agreement"] == 1
    assert all(a["repeats"] == 2 for a in result["aggregates"])
    assert result["aggregates"][0]["metrics"]["useful_per_100k"]["median"] == 1000
    assert (sweep / "report.md").is_file()


def test_b2_rejects_mutated_runs_and_frozen_prompts(tmp_path: Path, pyproject_cli: Path) -> None:
    sweep = prepare(tmp_path, pyproject_cli)
    (sweep / "runs/A0.fixture.1/nutrients.json").write_text("[]")
    with pytest.raises(ValueError, match="recorded run changed"):
        b2.pool(sweep)
    (sweep / "frozen/prompt-A2.md").write_text("Changed prompt")
    with pytest.raises(ValueError, match="prompt changed"):
        b2.manifest(sweep)


def test_b2_requires_full_judgments_and_flags_audit_disagreement(
    tmp_path: Path, pyproject_cli: Path
) -> None:
    sweep = prepare(tmp_path, pyproject_cli)
    private = b2.pool(sweep)
    grade(sweep, private)
    b2.write(sweep / "judged/value-repeat.json", [])
    with pytest.raises(ValueError, match="every expected id"):
        b2.report(sweep, {"fixture": str(pyproject_cli)})
    grade(sweep, private)
    audit = b2.read(sweep / "judged/audit.json")
    audit[0].update(useful=False, garbage=True)
    b2.write(sweep / "judged/audit.json", audit)
    result = b2.report(sweep, {"fixture": str(pyproject_cli)})
    assert result["rubric_suspect"] and not result["valid"]


def test_b2_checks_git_evidence_and_license_ceiling(tmp_path: Path, pyproject_cli: Path) -> None:
    sweep = prepare(tmp_path, pyproject_cli)
    path = sweep / "runs/A0.fixture.1/nutrients.json"
    cards = b2.read(path)
    cards[0]["evidence"][0]["path"] = "invented.py"
    cards[0]["license_mode"] = "COPY"
    b2.write(path, cards)
    receipt_path = path.parent / "receipt.json"
    receipt = b2.read(receipt_path)
    receipt["nutrients.json"] = b2.digest(path.read_bytes())
    b2.write(receipt_path, receipt)
    private = b2.pool(sweep)
    grade(sweep, private)
    result = b2.report(sweep, {"fixture": str(pyproject_cli)})
    assert not result["valid"]
    row = next(r for r in result["runs"] if r["run"] == "A0.fixture.1")
    assert row["n_fabricated"] == 1 and row["n_license_errors"] == 1
    sha = b2.manifest(sweep)["prey"][0]["sha"]
    assert not b2.verify_path(pyproject_cli, sha, "../outside")


def test_b2_rejects_changed_blind_identity_map(tmp_path: Path, pyproject_cli: Path) -> None:
    sweep = prepare(tmp_path, pyproject_cli)
    private = b2.pool(sweep)
    grade(sweep, private)
    alias = next(iter(private))
    private[alias]["run"] = "invented-arm.fixture.1"
    b2.write(sweep / "private/batch-map.json", private)
    with pytest.raises(ValueError, match="identity map changed"):
        b2.report(sweep, {"fixture": str(pyproject_cli)})


@pytest.mark.parametrize(
    "field,value", [("quality", 4), ("useful", "yes"), ("category", "invented")]
)
def test_b2_rejects_malformed_cards_or_grades(tmp_path: Path, field: str, value: object) -> None:
    if field == "category":
        with pytest.raises(ValueError):
            b2.normalize([{"id": "x", "category": value}])
    else:
        entry = {
            "id": "x",
            "useful": True,
            "garbage": False,
            "quality": 3,
            "reason": "test",
            field: value,
        }
        b2.write(tmp_path / "judgment.json", [entry])
        with pytest.raises(ValueError):
            b2.judgments(tmp_path / "judgment.json", {"x"}, value=True)
