from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.cache import Target
from hungry_crab.cli import main
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.feeder import EatOptions, EatResult
from hungry_crab.multifeed import eat_many, load_multi, merge_menus
from hungry_crab.nutrients import Candidate


def menu(prey: str, mode: str = "COPY", *, sha: str = "a", score: float = 0.7) -> dict[str, Any]:
    card = Candidate(
        "ci",
        "ci.cache",
        "Cache CI",
        "x",
        license_mode=mode,
        origin="licensed",
        score=score,
        trace={"prey": prey, "sha": sha},
    )
    return {
        "schema": "hungry-crab.menu/1",
        "generated_at": "2026-10-08T12:00:00+00:00",
        "mode": "normal",
        "maw": {"label": "maw", "sha": "m", "root": "/maw"},
        "prey": {"label": prey, "sha": sha, "url": None, "license": "MIT"},
        "candidates": [card.to_dict()],
        "hidden": [],
        "counts": {"total": 1, "top": 30},
    }


def test_dedup_keeps_every_pin_and_uses_strictest_source_without_score_boost() -> None:
    sources = {
        "copy": menu("x/copy", score=0.9),
        "restricted": menu("x/gpl", "REIMPLEMENT", sha="b", score=0.4),
    }
    before = copy.deepcopy(sources)
    result = merge_menus(sources)
    assert sources == before
    assert result["counts"]["total"] == 1
    card = result["candidates"][0]
    assert card["license_mode"] == "REIMPLEMENT" and card["score"] == 0.4
    assert card["trace"]["primary_source"] == "restricted"
    assert {source["prey"]["sha"] for source in card["trace"]["sources"]} == {"a", "b"}
    assert merge_menus(dict(reversed(list(sources.items())))) == result


def test_maw_relative_dedup_preserves_prey_specific_lessons_and_hidden_sources() -> None:
    sources = {"a": menu("x/a"), "b": menu("x/b")}
    for name, source in sources.items():
        card = Candidate("history-lesson", f"history-lesson.{name}.fix-prone", "Fix-prone", "x")
        source["candidates"].append(card.to_dict())
        source["hidden"] = [{"id": "hidden", "reason": "ledger: rejected"}]
    result = merge_menus(sources)
    assert result["counts"]["total"] == 3 and result["counts"]["hidden"] == 2
    sources["b"]["maw"]["sha"] = "another"
    with pytest.raises(CrabError, match="snapshot"):
        merge_menus(sources)


def fake_eat(prey: Target, maw: Path, options: EatOptions, **_: Any) -> EatResult:
    assert options.out is not None
    options.out.mkdir()
    data = menu(prey.label)
    data["maw"]["root"] = str(maw.resolve())
    (options.out / "menu.json").write_text(json.dumps(data), encoding="utf-8")
    return EatResult(options.out, {"prey": data["prey"]})


def test_atomic_bundle_integrity_and_cli_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    monkeypatch.setattr("hungry_crab.multifeed.eat", fake_eat)
    result = eat_many(
        [Target(slug=None, path=maw.parent / "a"), Target(path=maw.parent / "b")],
        maw,
        EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache"),
    )
    merged, paths, _ = load_multi(result.out_dir)
    assert len(paths) == 2 and merged["counts"]["total"] == 1
    assert result.manifest["side_effects"] == {"issues_created": 0, "ledger_written": False}
    assert not (maw / ".crab").exists()
    assert main(["menu", "--meal-dir", str(result.out_dir), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "multi-prey"
    aggregate_path = result.out_dir / "menu.json"
    original = aggregate_path.read_text()
    altered = json.loads(original)
    altered["candidates"][0]["license_mode"] = "HUMAN"
    aggregate_path.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(CrabError, match="differs"):
        load_multi(result.out_dir)
    aggregate_path.write_text(original, encoding="utf-8")
    (next(iter(paths.values())) / "menu.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CrabError, match="integrity"):
        load_multi(result.out_dir)


def test_duplicate_or_failed_sources_never_publish_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    options = EatOptions(out=tmp_path / "bundle", cache_root=tmp_path / "cache")
    target = Target(path=tmp_path / "prey")
    with pytest.raises(UsageError, match="duplicate"):
        eat_many([target, target], maw, options)
    calls = 0

    def fail_second(prey: Target, maw: Path, options: EatOptions, **kwargs: Any) -> EatResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CrabError("acquisition failed")
        return fake_eat(prey, maw, options)

    monkeypatch.setattr("hungry_crab.multifeed.eat", fail_second)
    with pytest.raises(CrabError, match="acquisition failed"):
        eat_many([target, Target(path=tmp_path / "other")], maw, options)
    assert not options.out.exists()  # type: ignore[union-attr]
    assert not list(tmp_path.glob(".bundle-*"))


def test_real_multi_feeder_retains_verified_digests_and_dry_run_serving(
    npm_app: Path,
    pyproject_cli: Path,
    dotnet_lib: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "bundle"
    cache = tmp_path / "cache"
    args = [
        "--cache-dir",
        str(cache),
        "eat",
        str(npm_app),
        str(dotnet_lib),
        "--deterministic",
        "--maw",
        str(pyproject_cli),
        "--out",
        str(out),
        "--no-wiki",
        "--json",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "hungry-crab.feeder-multi/1"
    merged, paths, _ = load_multi(out)
    assert len(paths) == 2 and merged["candidates"]
    for path in paths.values():
        assert (path / "prey-digest" / "manifest.json").is_file()
    assert (
        main(
            [
                "--cache-dir",
                str(cache),
                "serve",
                "--meal-dir",
                str(out),
                "--maw",
                str(pyproject_cli),
                "--top",
                "1",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry-run" and not report["served"]
    assert report["previews"] or report["skipped"]
