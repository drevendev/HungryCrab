"""Owner configuration and conservative learning regressions for the 0.6.7 slices."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hungry_crab.cli import main
from hungry_crab.compare.scoring import SECTIONS, Scoring
from hungry_crab.config_validation import validate
from hungry_crab.errors import UsageError
from hungry_crab.learning_keys import learning_key
from hungry_crab.ledger import Ledger, LedgerEntry
from hungry_crab.maw import MawConfig, write_default_config
from hungry_crab.nutrients import Candidate, slugify
from hungry_crab.tune import analyse


@pytest.mark.parametrize("section", SECTIONS)
@pytest.mark.parametrize(
    "value", [True, "private-value", None, float("nan"), float("inf"), -0.1, 1.1]
)
def test_scoring_error_names_member_without_value(section: str, value: object) -> None:
    with pytest.raises(UsageError, match=rf"scoring\.{section}\.member") as error:
        validate({"scoring": {section: {"member": value}}})
    assert "private-value" not in str(error.value)


def test_scoring_shape_and_non_string_member_are_bounded() -> None:
    with pytest.raises(UsageError, match=r"scoring\.traits member 1"):
        validate({"scoring": {"traits": {42: 0.5}}})
    with pytest.raises(UsageError, match=r"scoring\.risk"):
        validate({"scoring": {"risk": "private-value"}})
    validate({"scoring": {"traits": {"zero": 0, "one": 1}}})


def test_scoring_rewrite_keeps_comments_by_full_member_path(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    path.write_bytes(
        b"# Owner\r\nscoring:\r\n  categories: # category header\r\n    docs: 0.3 # category\r\n"
        b"  traits:\r\n    'key#quoted': 0.4 # quoted hash\r\n    docs: 0.6 # trait\r\n"
        b"# Outside\r\nledger: none\r\n"
    )
    scoring = {"categories": {"docs": 0.7}, "traits": {"key#quoted": 0.5, "docs": 0.8}}
    config = MawConfig.load(tmp_path)
    config.write_scoring(scoring)
    result = path.read_bytes()
    assert b"categories:  # category header\r\n" in result
    assert b"docs: 0.7  # category\r\n" in result
    assert b"docs: 0.8  # trait\r\n" in result
    assert b"# quoted hash\r\n" in result
    assert result.startswith(b"# Owner\r\n") and result.endswith(b"# Outside\r\nledger: none\r\n")
    assert MawConfig.load(tmp_path).scoring == scoring
    config.write_scoring(scoring)
    assert path.read_bytes() == result


@pytest.mark.parametrize(
    "original",
    [
        "  mode: strict\n  scoring: {}\n  ledger: repo\n",
        "mode: strict\nledger: repo\n...\n",
        "hunger: &policy {code: ideas-only}\nscoring: {}\n",
        "{mode: strict, scoring: {}}\n",
    ],
)
def test_unsupported_rewrite_preserves_file_and_object(tmp_path: Path, original: str) -> None:
    path = tmp_path / ".crab.yml"
    path.write_bytes(original.encode())
    config = MawConfig.load(tmp_path)
    before = dict(config.raw)
    with pytest.raises(UsageError):
        config.write_scoring({"categories": {"docs": 0.7}})
    assert path.read_bytes() == original.encode()
    assert config.raw == before and config.scoring == before.get("scoring", {})
    assert not list(tmp_path.glob(".*.tmp"))


def test_invalid_intended_scoring_does_not_replace_file(tmp_path: Path) -> None:
    path = write_default_config(tmp_path)
    original = path.read_bytes()
    with pytest.raises(UsageError, match=r"scoring\.categories\.docs"):
        MawConfig.load(tmp_path).write_scoring({"categories": {"docs": True}})
    assert path.read_bytes() == original
    assert b"# source-linked behavior findings" in original


def test_invalid_boolean_cannot_hide_as_an_equal_zero_noop(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text("scoring: {categories: {docs: 0}}\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(UsageError, match=r"scoring\.categories\.docs"):
        MawConfig.load(tmp_path).write_scoring({"categories": {"docs": False}})
    assert path.read_bytes() == before


def test_rewrite_preserves_safe_yaml_scalar_types_without_json_conversion(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text("license: 2026-10-27\nscoring: {}\n", encoding="utf-8")
    config = MawConfig.load(tmp_path)
    original = config.raw["license"]
    config.write_scoring({"categories": {"docs": 0.6}})
    assert MawConfig.load(tmp_path).raw["license"] == original
    assert path.read_text().startswith("license: 2026-10-27\n")


def family_ledger(count: int, status: str = "rejected") -> Ledger:
    ledger = Ledger(None)
    for index in range(count):
        prey = f"example/prey-{index}"
        key = f"history-lesson.{slugify(prey)}.fix-prone"
        entry = LedgerEntry(
            f"crab:history-lesson:{key}",
            "history-lesson",
            key,
            "Synthetic",
            status=status,
            prey=prey,
        )
        ledger.entries[entry.id] = entry
    return ledger


@pytest.mark.parametrize("status", ["accepted", "rejected"])
def test_trait_minimum_and_asymmetry(status: str) -> None:
    assert not any(
        s.kind == "trait"
        for s in analyse(family_ledger(2, status), Scoring.default(), min_decisions=5).suggestions
    )
    suggestions = analyse(family_ledger(2, status), Scoring.default(), min_decisions=2).suggestions
    assert any(s.kind == "trait" for s in suggestions) == (status == "rejected")
    assert any(
        s.kind == "trait"
        for s in analyse(family_ledger(5, status), Scoring.default(), min_decisions=5).suggestions
    )


def test_cli_minimum_precedence_controls_actual_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text("memory: {min_decisions: 5}\n", encoding="utf-8")
    ledger = family_ledger(2)
    ledger.path = tmp_path / ".crab" / "ledger.json"
    ledger.save()
    args = ["tune", "--maw", str(tmp_path), "--json", "--write"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["min_decisions"] == 5
    assert "scoring" not in MawConfig.load(tmp_path).raw
    assert main([*args, "--min-decisions", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["min_decisions"] == 2
    assert MawConfig.load(tmp_path).scoring["traits"]["history-lesson.*.fix-prone"] == 0.2


@pytest.mark.parametrize("suffix", ["top-42", "cluster-1", "new-unclassified-kind"])
def test_prey_local_keys_never_train_cross_prey(suffix: str) -> None:
    ledger = Ledger(None)
    for prey in ("a/one", "b/two"):
        key = f"issue-lesson.{slugify(prey)}.{suffix}"
        assert learning_key("issue-lesson", key, prey) == key
        entry = LedgerEntry(
            f"crab:issue-lesson:{key}",
            "issue-lesson",
            key,
            "Synthetic",
            status="rejected",
            prey=prey,
        )
        ledger.entries[entry.id] = entry
    scoring = Scoring.default().merged({"traits": {f"issue-lesson.*.{suffix}": 0.2}})
    report = analyse(ledger, scoring, min_decisions=2)
    assert report.diagnostics == [f"issue-lesson.*.{suffix}"]
    assert not any(s.kind == "trait" for s in report.suggestions)
    card = Candidate(
        "issue-lesson",
        f"issue-lesson.c-three.{suffix}",
        "Synthetic",
        "x",
        value=0.5,
        trace={"prey": "c/three"},
    )
    assert scoring.value_for(card) == 0.5
