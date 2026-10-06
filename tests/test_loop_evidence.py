from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_loop import NOW, SHA, Loop, force_phase
from test_loop import loop as _loop_fixture

from hungry_crab.digest import DigestResult
from hungry_crab.errors import CrabError
from hungry_crab.loop import atomic_json

loop = _loop_fixture


def evidence(loop: Loop, directory: Path, digest: Path | None = None) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    if digest is None:
        digest = directory / "digest"
        digest.mkdir()
        data = {
            "schema": "hungry-crab.digest/1",
            "prey": {"sha": SHA},
            "coverage": {"healthy": True},
            "files": [],
            "miners": [{"name": "inventory", "ok": True, "files": []}],
        }
        atomic_json(digest / "manifest.json", data)
    else:
        data = json.loads((digest / "manifest.json").read_text(encoding="utf-8"))
    sha = data["prey"]["sha"]
    menu = {
        "schema": "hungry-crab.menu/1",
        "generated_at": NOW.isoformat(),
        "prey": {"label": "example/prey", "sha": sha},
        "maw": {"root": str(loop.maw), "sha": sha},
        "candidates": [{"id": "crab:tests:tests.unit"}],
    }
    meal = {
        "schema": "hungry-crab.meal/1",
        "generated_at": menu["generated_at"],
        "prey": menu["prey"],
        "maw": menu["maw"],
        "prey_digest": str(digest),
        "maw_digest": str(digest),
    }
    atomic_json(directory / "meal.json", meal)
    atomic_json(directory / "menu.json", menu)
    atomic_json(
        directory / "notes.json",
        {
            "crab:tests:tests.unit": {
                "why": "Catch regressions",
                "how": "Add tests next to existing tests",
            }
        },
    )
    return {"meal": str(directory / "meal.json"), "notes": str(directory / "notes.json")}


def test_eat_accepts_real_digest_artifacts_and_advances_one_prey_per_wake(
    loop: Loop, tmp_path: Path, py_digest: DigestResult
) -> None:
    force_phase(loop, "eat", prey=["example/prey", "example/prey-two"])
    receipt = evidence(loop, tmp_path / "meal", py_digest.out_dir)
    ready = loop.next()
    result = loop.record(ready["active"]["token"], "eat", "ok", receipt=receipt)
    assert result["phase"] == "eat" and result["current_prey"] == "example/prey-two"
    assert len(result["meals"]) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "stale",
        "wrong-maw",
        "wrong-sha",
        "bad-artifact",
        "loss",
        "miner-failed",
        "unknown-id",
        "no-how",
        "mismatched-menu",
    ],
)
def test_eat_refuses_incomplete_or_unrelated_evidence_without_advancing(
    loop: Loop, tmp_path: Path, damage: str
) -> None:
    force_phase(loop, "eat", prey=["example/prey"])
    directory = tmp_path / "meal"
    receipt = evidence(loop, directory)
    meal = json.loads((directory / "meal.json").read_text(encoding="utf-8"))
    menu = json.loads((directory / "menu.json").read_text(encoding="utf-8"))
    manifest = json.loads((directory / "digest" / "manifest.json").read_text(encoding="utf-8"))
    notes = json.loads((directory / "notes.json").read_text(encoding="utf-8"))
    if damage == "stale":
        meal["generated_at"] = "2025-01-01T00:00:00+00:00"
    elif damage == "wrong-maw":
        meal["maw"]["root"] = str(tmp_path)
    elif damage == "wrong-sha":
        manifest["prey"]["sha"] = "b" * 40
    elif damage == "bad-artifact":
        manifest["miners"][0]["files"] = ["absent.json"]
    elif damage == "loss":
        manifest["coverage"]["healthy"] = False
    elif damage == "miner-failed":
        manifest["miners"][0]["ok"] = False
    elif damage == "unknown-id":
        notes = {"crab:tests:unknown": {"why": "Unknown", "how": "Unknown"}}
    elif damage == "no-how":
        notes["crab:tests:tests.unit"].pop("how")
    else:
        menu["prey"]["sha"] = "b" * 40
    atomic_json(directory / "meal.json", meal)
    atomic_json(directory / "menu.json", menu)
    atomic_json(directory / "digest" / "manifest.json", manifest)
    atomic_json(directory / "notes.json", notes)
    ready = loop.next()
    before = loop.path.read_bytes()
    with pytest.raises(CrabError):
        loop.record(ready["active"]["token"], "eat", "ok", receipt=receipt)
    assert loop.path.read_bytes() == before


def test_empty_menu_is_a_valid_unproductive_meal(loop: Loop, tmp_path: Path) -> None:
    force_phase(loop, "eat", prey=["example/prey"])
    receipt = evidence(loop, tmp_path / "meal")
    menu_path = tmp_path / "meal" / "menu.json"
    menu = json.loads(menu_path.read_text(encoding="utf-8"))
    menu["candidates"] = []
    atomic_json(menu_path, menu)
    atomic_json(Path(receipt["notes"]), {})
    ready = loop.next()
    assert loop.record(ready["active"]["token"], "eat", "ok", receipt=receipt)["phase"] == "serve"
