"""Content-origin boundaries for issue-derived nutrients (#58, #84, #137)."""

from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW

from hungry_crab.cache import Target
from hungry_crab.compare import CompareOptions, run_compare
from hungry_crab.digest import DigestOptions
from hungry_crab.licensing import ContentOrigin, Mode, cap_mode_for_origin
from hungry_crab.nutrients import Candidate, Evidence, merge_notes

SENTINEL = "COMMENTER_SENTINEL_DO_NOT_COPY"


def test_a_card_that_declares_no_origin_fails_closed() -> None:
    """A builder that forgets its origin yields HUMAN, not the repository's COPY (#137)."""
    card = Candidate(category="history-lesson", key="k", title="t", what="w")
    assert card.origin == ContentOrigin.UNKNOWN.value
    assert card.license_mode == Mode.HUMAN.value

    card.license_mode = Mode.COPY.value
    assert card.license_mode == Mode.HUMAN.value
    assert "fails closed" in card.license_reason


def test_reassigning_the_origin_recaps_the_mode_and_refreshes_the_trace() -> None:
    card = Candidate(category="ci", key="k", title="t", what="w", origin="licensed")
    card.license_mode = Mode.COPY.value
    card.trace = {"prey": "fixture"}
    assert card.license_mode == Mode.COPY.value

    card.origin = ContentOrigin.COMMENTERS.value
    assert card.license_mode == Mode.IDEAS_ONLY.value
    assert "IDEAS_ONLY" in card.license_reason
    assert card.trace["content_origin"] == ContentOrigin.COMMENTERS.value
    assert "IDEAS_ONLY" in card.trace["origin_license_reason"]

    # widening the origin again never widens the mode back
    card.origin = ContentOrigin.LICENSED.value
    assert card.license_mode == Mode.IDEAS_ONLY.value
    assert card.trace["content_origin"] == ContentOrigin.LICENSED.value


def test_every_builder_declares_its_origin(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    """The default is `unknown`, so a menu with an unknown origin means a builder forgot."""
    result, _, _ = run_compare(
        Target(path=npm_app),
        pyproject_cli,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache"),
        options=CompareOptions(now=FIXED_NOW),
    )
    assert result.candidates
    for card in result.candidates:
        expected = (
            ContentOrigin.COMMENTERS.value
            if card.category == "issue-lesson"
            else ContentOrigin.LICENSED.value
        )
        assert card.origin == expected, card.id
        assert card.license_mode != Mode.HUMAN.value, card.id


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
    card = Candidate(category="ci", key="workflow", title="CI", what="workflow", origin="licensed")
    card.license_mode = Mode.COPY.value
    card.trace = {"prey": "fixture"}

    assert card.origin == ContentOrigin.LICENSED.value
    assert card.license_mode == Mode.COPY.value
    assert card.license_reason == ""
    assert card.trace["content_origin"] == ContentOrigin.LICENSED.value
    assert "origin_license_reason" not in card.trace
