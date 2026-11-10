"""Keep documentation share-alike notices distinct from source-file copyleft.

These tests check the public obligation description, not real-world compliance
with the full terms of CC-BY-SA or the GFDL. See issue #219.
"""

from __future__ import annotations

import pytest

from hungry_crab.attribution import obligation_for


@pytest.mark.parametrize(
    ("spdx", "required"),
    [
        (
            "CC-BY-SA-4.0",
            ("creator", "source", "copyright", "licence", "changes", "sharealike"),
        ),
        (
            "GFDL-1.3-only",
            ("full licence text", "author", "invariant", "history", "cover", "modifications"),
        ),
    ],
)
def test_document_share_alike_does_not_claim_a_file_header_is_sufficient(
    spdx: str, required: tuple[str, ...]
) -> None:
    obligation = obligation_for(spdx, {"relationship": "foreign"})
    assert obligation.kind == "file-license"
    text = obligation.text.casefold()
    assert all(fragment in text for fragment in required)
    assert "each copied file keeps its own licence header" not in text


def test_document_and_permissive_duties_remain_distinct() -> None:
    obligation = obligation_for("MIT AND CC-BY-SA-4.0", {"relationship": "foreign"})
    assert obligation.kind == "combined"
    assert "MIT:" in obligation.text
    assert "CC-BY-SA-4.0:" in obligation.text
    assert "ShareAlike" in obligation.text


def test_source_file_copyleft_retains_existing_file_notice_rule() -> None:
    obligation = obligation_for("MPL-2.0", {"relationship": "foreign"})
    assert obligation.kind == "file-license"
    assert "each copied file keeps its own licence header" in obligation.text


def test_owner_relationship_still_avoids_foreign_duties() -> None:
    obligation = obligation_for("CC-BY-SA-4.0", {"relationship": "own"})
    assert obligation.kind == "none"
