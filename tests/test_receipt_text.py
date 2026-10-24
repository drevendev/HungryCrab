"""Provider action syntax and raw Crab markers are independent publication boundaries."""

from __future__ import annotations

import html

import pytest
from helpers import unfenced_text

from hungry_crab.errors import CrabError
from hungry_crab.nutrients import require_nutrient_id
from hungry_crab.receipt_text import quote_receipt_text
from hungry_crab.safety import safe_name
from hungry_crab.serve import parse_markers


@pytest.mark.parametrize(
    "keyword",
    ["close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"],
)
@pytest.mark.parametrize(
    "target",
    [
        "#12",
        "owner/repo#12",
        "https://github.com/owner/repo/issues/12",
        "https://github.com/owner/repo/pull/12",
    ],
)
def test_metadata_cannot_supply_a_closing_directive(keyword: str, target: str) -> None:
    for value in (f"{keyword} {target}", f"{keyword.upper()}: {target}"):
        assert safe_name(value) == "[name omitted: instruction-like]"


@pytest.mark.parametrize(
    "text",
    [
        "Closes https://github.com/owner/repo/issues/12\n@someone",
        "Closes #12\n![pixel](https://tracker.example)",
        "```\nCloses #12\n```\n````\n<img src=x>",
        "<!-- crab:ci:other -->\nCloses #12",
    ],
)
def test_receipt_data_cannot_leave_its_fence_or_reconcile_another_card(text: str) -> None:
    body = "<!-- crab:ci:owned -->\n\n" + quote_receipt_text(text)
    assert unfenced_text(body).strip() == "<!-- crab:ci:owned -->"
    assert set(parse_markers([{"number": 1, "body": body}])) == {"crab:ci:owned"}


def test_a_structural_id_cannot_smuggle_a_url_closing_directive() -> None:
    with pytest.raises(CrabError):
        require_nutrient_id("crab:docs:fix:https://github.com/owner/repo/issues/12")


@pytest.mark.parametrize("value", ["Hello @someone", "@types/node", "name@example.org"])
@pytest.mark.parametrize("layers", [0, 1, 2, 3, 4, 8])
def test_metadata_cannot_reconstitute_a_mention(value: str, layers: int) -> None:
    encoded = value.replace("@", "&#64;") if layers else value
    for _ in range(max(0, layers - 1)):
        encoded = html.escape(encoded)
    shown = safe_name(encoded)
    for _ in range(12):
        shown = html.unescape(shown)
    assert "@" not in shown
    assert (
        "someone" in shown
        or "(at) types" in shown
        or "name (at) example" in shown
        or "omitted" in shown
    )
