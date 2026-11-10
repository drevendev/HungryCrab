"""Keep documentation share-alike notices distinct from source-file copyleft.

These tests check the public obligation description, not real-world compliance
with the full terms of CC-BY-SA or the GFDL. See issue #219.
"""

from __future__ import annotations

import pytest

from hungry_crab.attribution import (
    AttributionRecord,
    CarriedText,
    SourceRef,
    TakenFile,
    add_attribution,
    dump_attributions,
    obligation_for,
    parse_attributions,
    render_notices,
)
from hungry_crab.pr_publication import nutrient_branch_name


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


def test_cc_by_sa_description_allows_link_instead_of_full_licence_copy() -> None:
    # Description only: the separate COPY publication gate remains conservative.
    obligation = obligation_for("CC-BY-SA-4.0", {"relationship": "foreign"})
    assert "licence text or a URI/hyperlink" in obligation.text
    assert "include the licence text" not in obligation.text


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


@pytest.mark.parametrize("spdx", ["CC-BY-SA-4.0", "GFDL-1.3-only"])
def test_existing_document_receipt_replay_does_not_silently_rewrite_its_notice(
    spdx: str,
) -> None:
    """A fresh description does not migrate an already-persisted source receipt.

    This is a compatibility boundary, not a repair for historical notices.
    Issue #219 owns an explicit migration/reconciliation and its regressions.
    """
    nutrient_id = "crab:docs:reviewed-guide"
    revised = AttributionRecord(
        nutrient_id=nutrient_id,
        source=SourceRef(
            "example/docs", "https://github.com/example/docs", "a" * 40, spdx
        ),
        taken=(TakenFile("docs/guide.md", "docs/guide.md", True),),
        mode="COPY_FILE",
        obligation=obligation_for(spdx, {"relationship": "foreign"}),
        summary="Carried a reviewed document",
        branch=nutrient_branch_name(nutrient_id),
        recorded_at="2026-11-10T12:00:00+00:00",
        texts=(CarriedText("LICENSE", "Synthetic licence text"),),
    )
    original = AttributionRecord.from_dict(
        {
            **revised.to_dict(),
            "obligation": {
                "kind": "file-license",
                "text": "each copied file keeps its own licence header and stays under it",
            },
        }
    )
    stored = parse_attributions(dump_attributions([original]))
    repeated = add_attribution(stored, revised)
    assert repeated == stored
    assert dump_attributions(repeated) == dump_attributions(stored)
    notice = " ".join(render_notices(repeated).split())
    assert original.obligation.text in notice
    assert revised.obligation.text not in notice
