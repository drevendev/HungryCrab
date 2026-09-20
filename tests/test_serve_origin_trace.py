from __future__ import annotations

from hungry_crab.nutrients import Candidate, Evidence
from hungry_crab.serve import render_issue


def test_issue_lesson_served_trace_exposes_origin_cap_without_commenter_text() -> None:
    sentinel = "COPY THIS COMMENTER TITLE VERBATIM"
    card = Candidate(
        "issue-lesson",
        "issues.popular-request",
        sentinel,
        sentinel,
        evidence=[Evidence("issues.json", "https://github.com/example/prey/issues/7")],
        license_mode="COPY",
    )

    title, body = render_issue(
        card,
        {"prey": {"label": "example/prey", "license": "MIT"}},
    )

    assert title == "Issue-derived demand signal"
    assert sentinel not in body
    assert "## License trace" in body
    assert "- content origin: `commenters`" in body
    assert "- license mode: `IDEAS_ONLY`" in body
    assert card.license_reason
    assert f"- origin cap: {card.license_reason}" in body


def test_licensed_nutrient_served_trace_does_not_invent_an_origin_cap() -> None:
    card = Candidate(
        "ci",
        "ci.cache",
        "Cache dependencies in CI",
        "The prey caches dependencies",
        license_mode="COPY",
    )

    _, body = render_issue(
        card,
        {"prey": {"label": "example/prey", "license": "MIT"}},
    )

    assert "## License trace" in body
    assert "- content origin: `licensed`" in body
    assert "- license mode: `COPY`" in body
    assert "- origin cap:" not in body
