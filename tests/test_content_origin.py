"""Content-origin boundaries for issue-derived nutrients (#58, #84)."""

from __future__ import annotations

from hungry_crab.licensing import ContentOrigin, Mode, cap_mode_for_origin
from hungry_crab.nutrients import Candidate, Evidence, merge_notes


SENTINEL = "COMMENTER_SENTINEL_DO_NOT_COPY"


def test_commenter_origin_caps_a_permissive_repository_mode() -> None:
    capped = cap_mode_for_origin(Mode.COPY, ContentOrigin.COMMENTERS)
    assert capped.mode is Mode.IDEAS_ONLY
    assert "carry the need and link" in capped.reason


def test_unknown_origin_fails_closed() -> None:
    card = Candidate(
        category="ci",
        key="unknown-origin",
        title="safe",
        what="safe",
        origin="future-origin-that-this-crab-does-not-know",
    )
    card.license_mode = Mode.COPY.value
    card.trace = {"prey": "fixture"}

    assert card.origin == ContentOrigin.UNKNOWN.value
    assert card.license_mode == Mode.HUMAN.value
    assert "fails closed" in card.license_reason
    assert card.trace["content_origin"] == ContentOrigin.UNKNOWN.value
    assert "fails closed" in card.trace["origin_license_reason"]


def test_issue_lesson_never_carries_commenter_prose() -> None:
    card = Candidate(
        category="issue-lesson",
        key="prey.top-7",
        title=f"Popular request: {SENTINEL}",
        what=f"issue #7 asks for {SENTINEL}",
        evidence=[Evidence(path="issues.json", url="https://example.invalid/issues/7")],
    )
    card.license_mode = Mode.COPY.value
    card.trace = {"prey": "fixture"}

    assert card.origin == ContentOrigin.COMMENTERS.value
    assert card.license_mode == Mode.IDEAS_ONLY.value
    assert SENTINEL not in card.title
    assert SENTINEL not in card.what
    assert card.evidence[0].url == "https://example.invalid/issues/7"
    assert card.trace["content_origin"] == ContentOrigin.COMMENTERS.value
    assert "IDEAS_ONLY" in card.trace["origin_license_reason"]


def test_model_notes_cannot_reintroduce_commenter_text() -> None:
    card = Candidate(
        category="issue-lesson",
        key="prey.cluster-1",
        title="unsafe source title",
        what="unsafe source text",
    )

    merge_notes(card, {"title": SENTINEL, "what": SENTINEL})

    assert SENTINEL not in card.title
    assert SENTINEL not in card.what


def test_licensed_card_keeps_repository_mode_and_prose() -> None:
    card = Candidate(category="ci", key="workflow", title="CI", what="workflow")
    card.license_mode = Mode.COPY.value
    card.trace = {"prey": "fixture"}

    assert card.origin == ContentOrigin.LICENSED.value
    assert card.license_mode == Mode.COPY.value
    assert card.license_reason == ""
    assert card.trace["content_origin"] == ContentOrigin.LICENSED.value
    assert "origin_license_reason" not in card.trace
