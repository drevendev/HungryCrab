from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXED_NOW

from hungry_crab.cache import Target
from hungry_crab.compare.candidates import Side
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.miners.deps import DepsMiner


def _emitted_files(manifest: dict[str, object]) -> dict[str, str]:
    emitted: dict[str, str] = {}
    records = manifest.get("miners")
    assert isinstance(records, list)
    for record in records:
        assert isinstance(record, dict)
        if record.get("status") != "ok":
            continue
        owner = str(record.get("name") or "")
        files = record.get("files")
        assert isinstance(files, list)
        for name in files:
            emitted[str(name)] = owner
    return emitted


def test_selective_rerun_removes_unselected_outputs_but_preserves_meal_files(
    npm_app: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    target = Target(path=npm_app)
    run_digest(target, DigestOptions(out=out_dir, now=FIXED_NOW))
    assert (out_dir / "deps.json").is_file()
    assert (out_dir / "traits.json").is_file()

    meal = out_dir / "menu.json"
    meal.write_text('{"meal": true}\n', encoding="utf-8", newline="\n")

    selective = run_digest(
        target,
        DigestOptions(out=out_dir, now=FIXED_NOW, miners=["license"]),
    )

    assert not (out_dir / "deps.json").exists()
    assert not (out_dir / "traits.json").exists()
    assert meal.read_text(encoding="utf-8") == '{"meal": true}\n'
    assert Side.load(out_dir).deps == {}

    emitted = _emitted_files(selective.manifest)
    for entry in selective.manifest["files"]:
        assert entry["name"] in emitted
        assert entry["miner"] == emitted[entry["name"]]


def test_forced_rerun_failure_cannot_reuse_previous_successful_json(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "out"
    target = Target(path=npm_app)
    run_digest(target, DigestOptions(out=out_dir, now=FIXED_NOW))
    assert (out_dir / "deps.json").is_file()

    def fail_deps(self: object, _ctx: object) -> object:
        raise RuntimeError("forced deps failure")

    monkeypatch.setattr(DepsMiner, "run", fail_deps)
    failed = run_digest(
        target,
        DigestOptions(out=out_dir, now=FIXED_NOW, force=True, miners=["deps"]),
    )

    deps_record = next(record for record in failed.manifest["miners"] if record["name"] == "deps")
    assert deps_record["status"] == "failed"
    assert not (out_dir / "deps.json").exists()
    assert Side.load(out_dir).deps == {}
    assert "deps.json" not in {entry["name"] for entry in failed.manifest["files"]}

    emitted = _emitted_files(failed.manifest)
    for entry in failed.manifest["files"]:
        assert entry["name"] in emitted
        assert entry["miner"] == emitted[entry["name"]]
