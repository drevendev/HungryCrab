from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import copy_repo

from hungry_crab.cache import Target, maw_paths
from hungry_crab.cli import main
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.feeder import EatOptions, eat
from hungry_crab.ledger import Ledger
from hungry_crab.miners import inventory


def test_eat_exports_complete_bundle_and_preserves_maw_and_ledger(
    npm_app: Path,
    pyproject_cli: Path,
    tmp_path: Path,
) -> None:
    maw = copy_repo(pyproject_cli, tmp_path / "maw")
    (maw / ".crab.yml").write_text("license: MIT\nmode: strict\nledger: repo\n", encoding="utf-8")
    ledger_path = maw / ".crab" / "ledger.json"
    ledger = Ledger.load(ledger_path, maw=maw.name)
    ledger.save()
    before = {p.relative_to(maw): p.read_bytes() for p in maw.rglob("*") if p.is_file()}
    result = eat(
        Target(path=npm_app),
        maw,
        EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache", wiki=False),
    )
    after = {p.relative_to(maw): p.read_bytes() for p in maw.rglob("*") if p.is_file()}
    assert before == after
    assert result.manifest["side_effects"] == {"issues_created": 0, "ledger_written": False}
    assert result.manifest["mode"] == "strict"
    assert {
        "menu.md",
        "gap.md",
        "menu.json",
        "meal.json",
        "feeder.json",
        "prey-digest",
        "maw-digest",
    } == {p.name for p in result.out_dir.iterdir()}
    for side in ("prey", "maw"):
        manifest = json.loads((result.out_dir / f"{side}-digest" / "manifest.json").read_text())
        assert len(manifest["miners"]) == 15 and all(m["ok"] for m in manifest["miners"])
    meal = json.loads((result.out_dir / "meal.json").read_text())
    assert meal["prey_digest"] == "prey-digest" and meal["maw_digest"] == "maw-digest"


def test_feeder_cli_prints_json_and_uses_bounded_defaults(
    npm_app: Path,
    pyproject_cli: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "--cache-dir",
            str(tmp_path / "cache"),
            "eat",
            str(npm_app),
            "--deterministic",
            "--maw",
            str(pyproject_cli),
            "--out",
            str(tmp_path / "bundle"),
            "--no-wiki",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "hungry-crab.feeder/1"
    assert EatOptions().shallow and EatOptions().since == "90d"
    assert not (maw_paths(pyproject_cli, tmp_path / "cache").ledger_file).exists()


def test_feeder_refuses_visibility_loss_unless_explicitly_allowed(
    npm_app: Path,
    pyproject_cli: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "MAX_FILES", {"normal": 2, "deep": 2})
    opts = EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache", wiki=False)
    with pytest.raises(CrabError, match="whole tree"):
        eat(Target(path=npm_app), pyproject_cli, opts)
    assert not opts.out.exists()  # type: ignore[union-attr]
    opts.allow_loss = True
    result = eat(Target(path=npm_app), pyproject_cli, opts)
    assert result.out_dir.is_dir()


def test_feeder_refuses_existing_artifact_before_doing_work(npm_app: Path, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "sentinel").write_text("keep", encoding="utf-8")
    with pytest.raises(UsageError, match="not empty"):
        eat(Target(path=npm_app), npm_app, EatOptions(out=out))
    assert (out / "sentinel").read_text() == "keep"
