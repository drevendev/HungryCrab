from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.compare import CompareOptions, compare_digests, menu_candidates
from hungry_crab.digest import DigestResult
from hungry_crab.errors import CrabError
from hungry_crab.ledger import Ledger
from hungry_crab.licensing.policy import apply_maw_policy, nutrient_material
from hungry_crab.maw import MawConfig
from hungry_crab.nutrients import Candidate, Evidence
from hungry_crab.serve import ServeOptions, render_issue, serve


@pytest.mark.parametrize("mode", ["HUMAN", "IDEAS_ONLY", "REIMPLEMENT", "COPY_FILE"])
def test_strict_never_widens_a_mode(mode: str) -> None:
    assert apply_maw_policy(mode, policy="strict", material="code") == (mode, "")


def test_strict_policy_retains_configs_and_templates() -> None:
    for material in ("configuration", "template"):
        assert apply_maw_policy("COPY", policy="strict", material=material) == ("COPY", "")
    assert nutrient_material("ci", [".github/workflows/ci.yml"]) == "configuration"
    assert nutrient_material("tests", ["tests/test_core.py"]) == "code"
    assert nutrient_material("tests", ["vitest.config.ts"]) == "configuration"
    assert nutrient_material("future-category", []) == "code"


def test_same_prey_strict_and_normal_modes_reach_menu_and_issue(
    npm_digest: DigestResult,
    py_digest: DigestResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def candidates(*args: object) -> tuple[list[Candidate], dict[str, object]]:
        return [
            Candidate(
                "code",
                "code.example",
                "Example",
                "Example code",
                origin="licensed",
                evidence=[Evidence("src/core.ts")],
            ),
            Candidate(
                "ci",
                "ci.example",
                "CI",
                "Example CI",
                origin="licensed",
                evidence=[Evidence(".github/workflows/ci.yml")],
            ),
        ], {}

    monkeypatch.setattr("hungry_crab.compare.build_candidates", candidates)
    normal = compare_digests(
        npm_digest.out_dir, py_digest.out_dir, options=CompareOptions(maw_license="MIT")
    )
    strict = compare_digests(
        npm_digest.out_dir,
        py_digest.out_dir,
        options=CompareOptions(maw_license="MIT", mode="strict"),
    )
    assert [c.license_mode for c in normal.candidates] == ["COPY", "COPY"]
    assert {c.category: c.license_mode for c in strict.candidates} == {
        "ci": "COPY",
        "code": "REIMPLEMENT",
    }
    code = next(c for c in menu_candidates(strict.menu) if c.category == "code")
    assert "strict maw policy" in code.trace["maw_policy_reason"]
    assert code.material == "code" and strict.menu["mode"] == "strict"
    _, body = render_issue(code, strict.menu)
    assert "REIMPLEMENT" in body and "strict maw policy" in body


def test_serve_refuses_a_menu_made_before_strict_was_enabled(tmp_path: Path) -> None:
    meal = tmp_path / "meal"
    meal.mkdir()
    (meal / "menu.json").write_text('{"mode":"normal","candidates":[]}', encoding="utf-8")
    config = MawConfig(root=tmp_path, mode="strict")
    ledger = Ledger.load(None, maw="maw")
    with pytest.raises(CrabError, match="strict policy"):
        serve(meal, tmp_path, ServeOptions(), config=config, ledger=ledger)
