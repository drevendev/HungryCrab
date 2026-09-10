"""A repository licence answers for the repository, and for nothing else (HungryCrab#58).

The text of an issue belongs to whoever wrote the issue. An MIT badge on the repository does not
license it, and until now the crab stamped one on every card alike — the rule lived in
`skills/license/SKILL.md` as a sentence for the model to remember.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import copy_repo

from hungry_crab.compare import compare_digests
from hungry_crab.compare.candidates import Side, issue_candidates
from hungry_crab.digest import DigestResult
from hungry_crab.licensing import ContentOrigin, Mode, Verdict, cap_for_origin

ISSUES = {
    "available": True,
    "clusters": [
        {
            "size": 12,
            "reactions": 3,
            "terms": ["install", "windows"],
            "sample_titles": ["install fails on windows", "another"],
        }
    ],
    "top_by_reactions": [
        {"number": 7, "title": "please support tabs", "reactions": 40, "state": "open"}
    ],
}


def test_a_repository_verdict_is_a_ceiling_not_an_answer() -> None:
    copy = Verdict(Mode.COPY, reason="permissive license: keep the copyright notice")
    capped = cap_for_origin(copy, ContentOrigin.COMMENTERS)
    assert capped.mode is Mode.IDEAS_ONLY
    assert "commenters" in capped.reason
    assert "permissive license" in capped.reason, "the repository's reason survives the cap"

    assert cap_for_origin(copy, ContentOrigin.LICENSED) == copy
    assert cap_for_origin(copy, "something-nobody-defined") == copy


@pytest.mark.parametrize(
    "mode",
    [Mode.IDEAS_ONLY, Mode.HUMAN],
)
def test_the_cap_only_ever_narrows(mode: Mode) -> None:
    """A prey nobody may copy from does not become copyable because a cap was applied."""
    verdict = Verdict(mode, reason="whatever the engine decided")
    assert cap_for_origin(verdict, ContentOrigin.COMMENTERS) == verdict


def test_a_reimplement_verdict_is_narrowed_rather_than_widened() -> None:
    verdict = Verdict(Mode.REIMPLEMENT, reason="strong copyleft")
    assert cap_for_origin(verdict, ContentOrigin.COMMENTERS).mode is Mode.IDEAS_ONLY


def test_issue_lesson_cards_declare_where_their_text_came_from(npm_digest: DigestResult) -> None:
    prey = Side.load(npm_digest.out_dir)
    prey.issues = ISSUES
    cards = issue_candidates(prey, Side.load(npm_digest.out_dir))
    assert cards, "the fixture should produce both a cluster and a top-by-reactions card"
    assert {card.origin for card in cards} == {ContentOrigin.COMMENTERS.value}


def test_the_menu_caps_commenter_text_and_leaves_the_rest_alone(
    npm_digest: DigestResult, py_digest: DigestResult, tmp_path: Path
) -> None:
    """End to end: an MIT prey is COPY, and its issue lessons are not."""
    prey_dir = copy_repo(npm_digest.out_dir, tmp_path / "prey")
    (prey_dir / "issues.json").write_text(json.dumps(ISSUES), encoding="utf-8")

    result = compare_digests(prey_dir, py_digest.out_dir)
    assert result.menu["verdict"]["mode"] == "COPY", "the repository itself is copyable"

    by_category: dict[str, set[str]] = {}
    for card in result.candidates:
        by_category.setdefault(card.category, set()).add(card.license_mode)
    assert by_category["issue-lesson"] == {"IDEAS_ONLY"}
    others = {
        mode for name, modes in by_category.items() if name != "issue-lesson" for mode in modes
    }
    assert others == {"COPY"}

    lesson = next(card for card in result.candidates if card.category == "issue-lesson")
    assert "commenters" in lesson.license_reason
    assert next(c for c in result.candidates if c.category != "issue-lesson").license_reason == ""
