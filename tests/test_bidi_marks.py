"""Bidi marks are ordinary in right-to-left text and suspicious only where nothing is right-to-left.

LRM, RLM and the Arabic letter mark give a neutral character a direction; Hebrew and Arabic
READMEs use them routinely, and redacting those headings would blind the digest to every
right-to-left project. The embeddings, overrides and isolates, which reorder what a reader
sees, stay flagged whatever surrounds them (tests/test_safety.py).
"""

from __future__ import annotations

import pytest

from hungry_crab.safety import is_suspicious, safe_name

LRM, RLM, ALM, RLO = chr(0x200E), chr(0x200F), chr(0x061C), chr(0x202E)
HEBREW = "התקנה"  # "installation"
ARABIC = "التثبيت"  # "installation"


@pytest.mark.parametrize(
    "text",
    [
        f"{HEBREW} {LRM}v2.0{LRM}",
        f"{HEBREW} (C#){RLM}",
        f"{ARABIC} {ALM}2024",
        f"{ARABIC}{RLM}: pip install crab",
        f"docs/{HEBREW}{LRM}.md",
    ],
)
def test_marks_inside_right_to_left_text_are_not_flagged(text: str) -> None:
    assert not is_suspicious(text)
    assert safe_name(text, "path") == text


@pytest.mark.parametrize(
    "text",
    [
        f"Install{LRM}ation",
        f"Safe heading{RLM}",
        f"v2{ALM}.0",
    ],
)
def test_marks_with_nothing_right_to_left_are_flagged(text: str) -> None:
    assert is_suspicious(text)


def test_an_override_is_flagged_even_inside_right_to_left_text() -> None:
    assert is_suspicious(f"{HEBREW} {RLO}dm.EMDAER")
