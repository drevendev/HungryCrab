"""Complete grant forms only; formatting never hides changed operative text."""

from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.licensing.detect import detect_from_text

ROOT = Path(__file__).parents[1]
MIT = (ROOT / "LICENSE").read_text(encoding="utf-8")
# Standard license text from SPDX license-list-data/text/BSL-1.0.txt.
BOOST = (ROOT / "tests/data/licenses/BSL-1.0.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("title", ["MIT Licence", "(The MIT Licence)", "MIT License"])
@pytest.mark.parametrize("quotes", [False, True])
@pytest.mark.parametrize("hyphen", [False, True])
def test_complete_mit_typographic_forms_keep_all_terms(
    title: str, quotes: bool, hyphen: bool
) -> None:
    body = MIT.replace("MIT License", title)
    if quotes:
        body = body.replace('"Software"', "'Software'").replace('"AS IS"', "'AS IS'")
    if hyphen:
        body = body.replace("NONINFRINGEMENT", "NON-INFRINGEMENT")
    assert detect_from_text(body) == ("MIT", 0.99)
    assert detect_from_text(body + "\nCommercial use requires permission.\n") == (None, 0.0)
    assert detect_from_text(body.replace("without restriction", "for research only")) == (None, 0.0)


@pytest.mark.parametrize(
    "credit",
    [
        "The Maple Project Developers",
        "The SampleKit project contributors",
        "The Maple Developers",
    ],
)
def test_bounded_collective_project_attribution(credit: str) -> None:
    body = MIT.replace("2026 Dreven", "2026 " + credit)
    assert detect_from_text(body) == ("MIT", 0.99)


@pytest.mark.parametrize(
    "credit",
    [
        "The NoCommercialUse Project Developers",
        "The PermissionRequired Project Contributors",
        "The NoSharing Developers",
        "The ResearchOnly Project Developers",
    ],
)
def test_collective_project_shape_cannot_hide_policy(credit: str) -> None:
    assert detect_from_text(MIT.replace("2026 Dreven", "2026 " + credit)) == (None, 0.0)


def test_partial_quote_or_truncated_complete_mit_stays_held() -> None:
    assert detect_from_text(MIT.replace('"Software"', "'Software\"")) == (None, 0.0)
    assert detect_from_text(MIT[: MIT.index("THE SOFTWARE IS PROVIDED")]) == (None, 0.0)


def test_complete_boost_grant_is_not_mit_or_a_heading_only_match() -> None:
    assert detect_from_text(BOOST) == ("BSL-1.0", 0.99)
    assert detect_from_text("  ".join(BOOST.lower().split())) == ("BSL-1.0", 0.99)
    assert detect_from_text("SPDX-License-Identifier: BSL-1.0\n" + BOOST) == ("BSL-1.0", 0.99)
    for changed in (
        BOOST.splitlines()[0],
        BOOST[: BOOST.index("THE SOFTWARE IS PROVIDED")],
        BOOST.replace("use, reproduce", "only read, reproduce"),
        BOOST + "\nCommercial use prohibited.\n",
        "SPDX-License-Identifier: MIT\n" + BOOST,
    ):
        assert detect_from_text(changed) == (None, 0.0)
