"""A representative GPL-3 mode must not claim compatibility with every GPL maw."""

from __future__ import annotations

import pytest
from test_sniff import NOW, SLUG, _repo

from hungry_crab.licensing import decide, modes_by_maw_class
from hungry_crab.licensing.matrix import mode_maw_samples
from hungry_crab.sniff import build_report, format_report


@pytest.mark.parametrize("prey", ["Apache-2.0", "GPL-3.0-only", "LGPL-3.0-only"])
def test_sample_summary_identifies_the_actual_gpl_maw(prey: str) -> None:
    report = build_report(
        SLUG, _repo(license={"spdx_id": prey}), {}, maw_license="GPL-2.0-only", now=NOW
    )
    assert report.mode == decide(prey, "GPL-2.0-only").mode.value == "IDEAS_ONLY"
    assert report.modes_by_maw_class["gpl"] == decide(prey, "GPL-3.0-only").mode.value == "COPY"
    assert report.to_dict()["mode_maw_samples"]["gpl"] == "GPL-3.0-only"
    assert "gpl maw (GPL-3.0-only sample): COPY" in format_report(report)
    assert modes_by_maw_class(prey)["gpl"] == "COPY"


def test_unknown_license_still_requires_human_review_in_both_summary_and_selected_mode() -> None:
    report = build_report(
        SLUG,
        _repo(license={"spdx_id": "NOASSERTION", "name": "Other"}),
        {},
        maw_license="MIT",
        now=NOW,
    )
    assert report.mode == "HUMAN" and set(report.modes_by_maw_class.values()) == {"HUMAN"}
    assert report.mode_maw_samples == mode_maw_samples()
