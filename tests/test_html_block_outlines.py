"""Original synthetic controls for the summary's no-body-prose boundary."""

from __future__ import annotations

import time

import pytest

from hungry_crab.miners.docs import readme_outline
from hungry_crab.miners.wiki import markdown_headings


@pytest.mark.parametrize(
    "opening",
    ["<div>", "<DIV class='sample'>", "</table>", "<div", "<section/>", "<search>"],
)
def test_type_six_body_cannot_become_outline_headings(opening: str) -> None:
    text = f"{opening}\n# BODY_CANARY\n</div>\n## STILL_BODY\n\n# Visible\n"
    assert markdown_headings(text) == [{"level": 1, "text": "Visible"}]
    assert [row["title"] for row in readme_outline(text)["headings"]] == ["Visible"]


@pytest.mark.parametrize(
    "opening",
    ["<widget>", "</widget>", '<widget data-note="a > b">', "<widget hidden key=value />"],
)
def test_complete_custom_tag_body_ends_at_a_blank(opening: str) -> None:
    text = f"{opening}\n# BODY_CANARY\n~~~\n## STILL_BODY\n\n# Visible\n"
    assert markdown_headings(text) == [{"level": 1, "text": "Visible"}]


@pytest.mark.parametrize(
    "opening", ["<widget", "<widget x=>", "<widget x='unclosed>", "<widget> prose"]
)
def test_incomplete_or_inline_custom_tag_does_not_hide_a_real_heading(opening: str) -> None:
    assert markdown_headings(f"{opening}\n# Visible\n") == [{"level": 1, "text": "Visible"}]


def test_custom_tag_cannot_interrupt_a_paragraph_but_div_can() -> None:
    assert markdown_headings("Paragraph\n<widget>\n# Visible\n") == [
        {"level": 1, "text": "Visible"}
    ]
    assert markdown_headings("Paragraph\n<div>\n# BODY_CANARY\n") == []


def test_fences_and_indented_code_do_not_open_html_blocks() -> None:
    assert markdown_headings("```\n<div>\n```\n# Visible\n") == [{"level": 1, "text": "Visible"}]
    assert markdown_headings("    <div>\n# Visible\n") == [{"level": 1, "text": "Visible"}]


def test_long_custom_tag_attributes_have_bounded_scan_time() -> None:
    started = time.perf_counter()
    assert markdown_headings("<widget a" + " " * 200_000 + '="unclosed>\n# Visible\n') == [
        {"level": 1, "text": "Visible"}
    ]
    assert markdown_headings("<widget" + " a" * 50_000 + ">\n# BODY_CANARY\n") == []
    assert time.perf_counter() - started < 1.0
