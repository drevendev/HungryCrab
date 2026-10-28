"""A manual patch preserves owner YAML bytes and refuses ambiguous edits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hungry_crab.cli import main
from hungry_crab.errors import UsageError
from hungry_crab.maw import MawConfig
from hungry_crab.tune import Suggestion, TuneReport, manual_patch


@pytest.mark.parametrize(
    "source",
    [
        "# owner\nhunger: &shared {code: ideas-only}\nscoring: {categories: {docs: 0.3}}\n",
        "{hunger: &shared {code: ideas-only}, scoring: {categories: {docs: 0.3}}} # owner\n",
        "scoring: &weights {categories: {docs: 0.3}}\n",
    ],
)
def test_manual_patch_leaves_anchored_and_flow_yaml_untouched(tmp_path: Path, source: str) -> None:
    path = tmp_path / ".crab.yml"
    path.write_bytes(source.encode())
    config = MawConfig.load(tmp_path)
    report = TuneReport(5, 3, [Suggestion("category", "docs", 0.3, 0.7, "synthetic", 5, 1.0)])
    first = manual_patch(report, config)
    assert first == manual_patch(report, config)
    assert first["config_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert first["operation"]["value"] == {"categories": {"docs": 0.7}}
    assert first["write_performed"] is False and first["changes_required"] is True
    assert path.read_bytes() == source.encode() and config.scoring == {"categories": {"docs": 0.3}}
    with pytest.raises(UsageError):
        config.write_scoring(first["operation"]["value"])
    assert path.read_bytes() == source.encode()


def test_manual_patch_refuses_duplicate_keys_and_changed_config(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text("scoring: {categories: {docs: 0.3, docs: 0.4}}\n")
    with pytest.raises(UsageError, match="duplicate"):
        manual_patch(TuneReport(0, 3), MawConfig.load(tmp_path))
    path.write_text("scoring: {}\n")
    config = MawConfig.load(tmp_path)
    path.write_text("scoring: {categories: {docs: 0.4}}\n")
    with pytest.raises(UsageError, match="changed"):
        manual_patch(TuneReport(0, 3), config)


def test_cli_patch_is_json_without_a_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "{ledger: none, hunger: {code: ideas-only}} # keep\n"
    path = tmp_path / ".crab.yml"
    path.write_text(source)
    assert main(["tune", "--maw", str(tmp_path), "--patch"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["written"] is None
    assert result["manual_patch"]["schema"] == "hungry-crab.scoring-patch/1"
    assert result["manual_patch"]["operation"]["op"] == "add"
    assert path.read_text() == source


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("hunt: {request_budget: 4}\n", "hunt: {request_budget: 4.0}\n"),
        ("hunt: {include_seen: false}\n", "hunt: {include_seen: 0}\n"),
        ("scoring: {categories: {docs: 0}}\n", "scoring: {categories: {docs: false}}\n"),
        ("scoring: {categories: {docs: 1}}\n", "scoring: {categories: {docs: 1.0}}\n"),
    ],
)
def test_patch_refuses_typed_equal_concurrent_edits(
    tmp_path: Path, before: str, after: str
) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text(before)
    config = MawConfig.load(tmp_path)
    path.write_text(after)
    with pytest.raises(UsageError):
        manual_patch(TuneReport(0, 3), config)
    assert path.read_text() == after


@pytest.mark.parametrize("source", ["false\n", "0\n", "[]\n", "null\n"])
def test_patch_refuses_scalar_replacement_of_empty_config(tmp_path: Path, source: str) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text("{}\n")
    config = MawConfig.load(tmp_path)
    path.write_text(source)
    with pytest.raises(UsageError, match="mapping"):
        manual_patch(TuneReport(0, 3), config)
    assert path.read_text() == source


def test_patch_refuses_config_created_after_load(tmp_path: Path) -> None:
    config = MawConfig.load(tmp_path)
    path = tmp_path / ".crab.yml"
    path.write_text("{}\n")
    with pytest.raises(UsageError, match="changed"):
        manual_patch(TuneReport(0, 3), config)
    assert path.read_text() == "{}\n"
