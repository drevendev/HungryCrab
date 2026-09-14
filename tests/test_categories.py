"""Every declared nutrient category is either produced by something or deferred by name.

`code` was declared in `CATEGORIES`, weighted in `scoring.yml` and offered as a hunger knob in
every generated `.crab.yml` for two milestones while no candidate builder could emit it, so the
setting could not affect a single meal. The producer is owed by 0.4 and that was decided — in a
private backlog, where a deferred category looks exactly like a forgotten one. This test makes
the distinction public: a category with no producer must name the milestone that owes it, and a
category that gains a producer must leave the deferred list.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from hungry_crab.compare import candidates
from hungry_crab.compare.rules import TRAIT_RULES
from hungry_crab.maw import DEFAULT_CONFIG_TEXT, DEFAULT_HUNGER
from hungry_crab.nutrients import CATEGORIES, DEFERRED_CATEGORIES

ROOT = Path(__file__).resolve().parents[1]


def produced_categories() -> set[str]:
    """The categories the deterministic pipeline can emit, read from the builders themselves.

    Trait rules carry their category as data; the other builders write it as a literal keyword
    on the `Candidate(...)` call. Running the builders on the fixtures would only show the
    categories those four repositories happen to trigger, which is the wrong question.
    """
    found = {rule.category for rule in TRAIT_RULES}
    source = Path(candidates.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Candidate"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "category":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                found.add(keyword.value.value)
    return found


def test_every_declared_category_is_produced_or_deferred_by_name() -> None:
    produced = produced_categories()
    declared = set(CATEGORIES)
    assert produced <= declared, f"a builder emits an undeclared category: {produced - declared}"
    dead = declared - produced - set(DEFERRED_CATEGORIES)
    assert not dead, (
        f"{sorted(dead)} is declared in CATEGORIES, and no candidate builder emits it. Either add "
        "a producer with a fixture, or list it in DEFERRED_CATEGORIES with the milestone that "
        "owes the producer."
    )
    stale = produced & set(DEFERRED_CATEGORIES)
    assert not stale, f"{sorted(stale)} has a producer now; remove it from DEFERRED_CATEGORIES"
    assert set(DEFERRED_CATEGORIES) <= declared
    for category, owner in DEFERRED_CATEGORIES.items():
        assert owner.startswith(("0.", "1.")), (
            f"{category!r} must name the milestone that owes its producer, not {owner!r}"
        )


def test_code_is_the_one_deferred_category_today() -> None:
    assert set(DEFERRED_CATEGORIES) == {"code"}
    assert DEFERRED_CATEGORIES["code"].startswith("0.4")


def test_the_config_and_the_weights_know_every_category_and_nothing_else() -> None:
    """The three surfaces a category is declared on must agree with `CATEGORIES`."""
    scoring = yaml.safe_load((ROOT / "src" / "hungry_crab" / "data" / "scoring.yml").read_text())
    assert set(scoring["categories"]) == set(CATEGORIES)
    assert set(DEFAULT_HUNGER) == set(CATEGORIES)
    generated = yaml.safe_load(DEFAULT_CONFIG_TEXT)
    assert set(generated["hunger"]) == set(CATEGORIES)
    # A deferred category is offered as a knob only with a comment that says it is inert, so
    # that the generated file does not present a dead setting as a live one.
    for category in DEFERRED_CATEGORIES:
        line = next(
            line for line in DEFAULT_CONFIG_TEXT.splitlines() if line.strip().startswith(category)
        )
        assert "#" in line and "0.4" in line, f"{line!r} should say the setting is deferred"
