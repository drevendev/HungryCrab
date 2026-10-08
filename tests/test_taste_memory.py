from __future__ import annotations

import json
from pathlib import Path

import pytest

from hungry_crab.compare.scoring import Scoring
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.ledger import Ledger, LedgerEntry
from hungry_crab.maw import MawConfig, write_default_config
from hungry_crab.memory import MemorySettings, learn
from hungry_crab.nutrients import Candidate, slugify
from hungry_crab.profiles import PROFILES, hunger_for, infer_profile
from hungry_crab.tune import analyse, apply


def decided(status: str, category: str = "docs", count: int = 3) -> Ledger:
    ledger = Ledger(None)
    for index in range(count):
        key = f"{category}.{index}"
        ledger.entries[key] = LedgerEntry(key, category, key, "Subject", status=status)
    return ledger


def test_learning_is_bounded_and_recomputed_from_confirmed_decisions() -> None:
    scoring = Scoring.default()
    ledger = decided("accepted")
    learned, report = learn(ledger, scoring, MemorySettings())
    assert learned.categories["docs"] > scoring.categories["docs"]
    assert learned.categories["docs"] <= scoring.categories["docs"] * 1.3
    for entry in ledger.entries.values():
        entry.sightings += 100
    repeated, same = learn(ledger, scoring, MemorySettings())
    assert repeated == learned and same == report
    for entry in ledger.entries.values():
        entry.status = "rejected"
    reversed_scoring, reversed_report = learn(ledger, scoring, MemorySettings())
    assert reversed_scoring.categories["docs"] < scoring.categories["docs"]
    assert reversed_report.fingerprint != report.fingerprint
    assert learn(ledger, scoring, MemorySettings(enabled=False))[0] == scoring


def test_confirmed_category_preference_changes_held_out_ranking_without_widening_modes() -> None:
    baseline = Scoring.default()
    learned, _ = learn(decided("accepted", "docs"), baseline, MemorySettings())
    docs = Candidate(
        "docs",
        "docs.held-out",
        "Documentation",
        "x",
        value=0.7,
        effort="S",
        license_mode="COPY",
        origin="licensed",
    )
    ci = Candidate(
        "ci",
        "ci.held-out",
        "CI",
        "x",
        value=0.5,
        effort="S",
        license_mode="COPY",
        origin="licensed",
    )
    assert baseline.score(docs) < baseline.score(ci)
    assert learned.score(docs) > learned.score(ci)
    assert docs.license_mode == ci.license_mode == "COPY"


@pytest.mark.parametrize("status", ["proposed", "served", "ignored"])
def test_unconfirmed_statuses_are_not_training_labels(status: str) -> None:
    scoring = Scoring.default()
    result, report = learn(decided(status), scoring, MemorySettings())
    assert result == scoring and report.decisions == 0
    assert analyse(decided(status), scoring).decisions == 0


def test_small_samples_and_manual_overrides_are_neutral() -> None:
    scoring = Scoring.default()
    assert learn(decided("rejected", count=2), scoring, MemorySettings())[0] == scoring
    manual = {"categories": {"docs": 0.95}}
    current = scoring.merged(manual)
    result, report = learn(decided("rejected"), current, MemorySettings(), overrides=manual)
    assert result == current and report.categories["docs"]["owner_override"]


def test_serving_preserves_an_explicit_acceptance_without_creating_one() -> None:
    ledger = decided("proposed")
    for key in ledger.entries:
        ledger.mark(key, "accepted")
    baseline = Scoring.default()
    before = learn(ledger, baseline, MemorySettings())
    for key in ledger.entries:
        ledger.mark(key, "served")
    assert learn(ledger, baseline, MemorySettings()) == before
    for key in ledger.entries:
        ledger.mark(key, "ignored")
    assert learn(ledger, baseline, MemorySettings())[1].decisions == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": "yes"},
        {"strength": float("nan")},
        {"strength": 0.6},
        {"min_decisions": 0},
        {"strength": True},
        {"enabeld": False},
        [],
    ],
)
def test_invalid_memory_settings_fail(settings: object) -> None:
    with pytest.raises(UsageError):
        MemorySettings.load(settings)


def test_tuning_same_decisions_twice_preserves_weights_and_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    original = (
        "# Owner policy\r\nlicense: MIT # keep\r\nhunger: {code: ideas-only}\r\n"
        "scoring: {} # tuning weights\r\n# Schedule\r\nloop: {prey: [example/prey]}\r\n"
    )
    path.write_bytes(original.encode())
    ledger = decided("rejected", "deps", 5)
    config = MawConfig.load(tmp_path)
    apply(analyse(ledger, Scoring.default()), config)
    first = path.read_bytes()
    assert first.startswith(
        b"# Owner policy\r\nlicense: MIT # keep\r\nhunger: {code: ideas-only}\r\n"
    )
    assert first.endswith(b"# Schedule\r\nloop: {prey: [example/prey]}\r\n")
    assert b"# tuning weights" in first
    config = MawConfig.load(tmp_path)
    report = analyse(ledger, Scoring.default().merged(config.scoring), hunger={"deps": False})
    assert all(item.kind not in {"category", "trait", "hunger"} for item in report.suggestions)
    apply(report, config)
    assert path.read_bytes() == first


def test_trait_families_train_across_distinct_preys_without_renaming_ids() -> None:
    ledger = Ledger(None)
    ids = []
    for index in range(3):
        prey = f"example/prey-{index}"
        card = Candidate(
            "history-lesson",
            f"history-lesson.{slugify(prey)}.fix-prone",
            "Fix-prone",
            "x",
            trace={"prey": prey},
        )
        ledger.ensure(card)
        ledger.mark(card.id, "accepted")
        ids.append(card.id)
    report = analyse(ledger, Scoring.default())
    assert any(
        item.target == "history-lesson.*.fix-prone" and item.suggested == 1.0
        for item in report.suggestions
    )
    assert set(ledger.entries) == set(ids)
    candidate = Candidate(
        "history-lesson",
        "history-lesson.example-new.fix-prone",
        "x",
        "x",
        trace={"prey": "example/new"},
    )
    scoring = Scoring.default().merged({"traits": {"history-lesson.*.fix-prone": 0.2}})
    assert scoring.value_for(candidate) == 0.2


def test_same_schema_unknown_fields_survive_and_future_schema_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = decided("accepted")
    data = ledger.to_dict()
    data["future_metadata"] = {"notes": [1, 2]}
    data["entries"][0]["evidence_v2"] = ["pinned"]
    data["meals"] = [{"prey": "p", "future_meal": [2], "extra": {"unknown": 1}}]
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = Ledger.load(path)
    loaded.save()
    saved = json.loads(path.read_text())
    assert saved["future_metadata"] == data["future_metadata"]
    assert saved["entries"][0]["evidence_v2"] == ["pinned"]
    assert saved["meals"][0]["future_meal"] == [2]
    assert saved["meals"][0]["extra"] == {"unknown": 1}
    data["schema"] = "hungry-crab.ledger/2"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(CrabError, match="unsupported ledger schema"):
        Ledger.load(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["duplicates", "status", "shape"])
def test_corrupt_ledger_cannot_silently_be_rewritten(tmp_path: Path, mutation: str) -> None:
    data = decided("accepted").to_dict()
    if mutation == "duplicates":
        data["entries"].append(data["entries"][0])
    elif mutation == "status":
        data["entries"][0]["status"] = "accpeted"
    else:
        data["entries"] = {}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CrabError):
        Ledger.load(path)


def test_profiles_are_safe_reviewed_defaults_with_explicit_owner_overrides(tmp_path: Path) -> None:
    for profile in PROFILES:
        assert hunger_for(profile)["code"] == "ideas-only"
        assert hunger_for(profile)["architecture"] == "issues-only"
    write_default_config(tmp_path, profile="library")
    assert MawConfig.load(tmp_path).profile == "library"
    (tmp_path / ".crab.yml").write_text("profile: cli\nhunger: {deps: false}\n", encoding="utf-8")
    assert MawConfig.load(tmp_path).hunger["deps"] is False
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "1"}}', encoding="utf-8")
    assert infer_profile(tmp_path) == "frontend"
    with pytest.raises(UsageError):
        hunger_for("unknown")


def test_anchored_configuration_refuses_write_without_losing_policy(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text("hunger: &policy {code: ideas-only}\nscoring: {}\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(UsageError, match="anchors"):
        MawConfig.load(tmp_path).write_scoring({"categories": {"docs": 0.6}})
    assert path.read_bytes() == before


def test_stale_ledger_writer_cannot_overwrite_newer_owner_decisions(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    initial = decided("proposed")
    initial.path = path
    initial.save()
    first, stale = Ledger.load(path), Ledger.load(path)
    keys = list(first.entries)
    first.mark(keys[0], "accepted")
    first.save()
    before = path.read_bytes()
    stale.mark(keys[1], "rejected")
    with pytest.raises(CrabError, match="changed since"):
        stale.save()
    assert path.read_bytes() == before
    fresh = Ledger.load(path)
    fresh.mark(keys[1], "rejected")
    fresh.save()
    fresh.save()
    assert Ledger.load(path).entries[keys[0]].feedback == "accepted"
    assert Ledger.load(path).entries[keys[1]].feedback == "rejected"


def test_new_writer_refuses_a_file_created_since_initialization(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    first, second = Ledger(path), Ledger(path)
    first.save()
    with pytest.raises(CrabError, match="changed since"):
        second.save()


def test_scoring_edit_preserves_trailing_blanks_and_header_comments(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    suffix = "\n\n# Owner schedule\nloop: {prey: [example/prey]}\n"
    path.write_text(
        "scoring: # explain these weights\n  categories:\n    docs: 0.5\n" + suffix,
        encoding="utf-8",
    )
    MawConfig.load(tmp_path).write_scoring({"categories": {"docs": 0.6}})
    assert path.read_text().endswith(suffix)
    assert "#explain these weights" in path.read_text()
    path.write_text("# Empty owner policy\n", encoding="utf-8")
    MawConfig.load(tmp_path).write_scoring({"categories": {"docs": 0.6}})
    assert path.read_text().startswith("# Empty owner policy\n")
