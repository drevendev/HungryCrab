"""Regression witnesses for a GPL maw's SPDX OR branch crossing publication boundaries.

The verdict, attribution obligation, and carried source files must refer to the
same recipient-compatible branch. These are synthetic source files, not executed prey.
See HungryCrab issue #219. This is a deliberately red test scaffold until fixed.
"""

from __future__ import annotations

import pytest

from hungry_crab.attribution import carried_texts, obligation_for
from hungry_crab.licensing import Mode, decide

SHA = "1234567890abcdef1234567890abcdef12345678"
OR_CHOICES = ("EPL-2.0 OR GPL-3.0-only", "GPL-3.0-only OR EPL-2.0")


class _SyntheticPrey:
    """Only the source-reader operations exercised by carried_texts(paths=())."""

    def __init__(self) -> None:
        self.files = {
            "LICENSES/EPL-2.0.txt": "SPDX-License-Identifier: EPL-2.0\n",
            "LICENSES/GPL-3.0-only.txt": "SPDX-License-Identifier: GPL-3.0-only\n",
        }

    def exists(self, sha: str, path: str) -> bool:
        return sha == SHA and path in self.files

    def read(self, sha: str, path: str) -> str | None:
        return self.files.get(path) if sha == SHA else None

    def entries(self, sha: str, directory: str = "") -> dict[str, str] | None:
        if sha != SHA:
            return None
        if not directory:
            return {}
        if directory == "LICENSES":
            return {path.removeprefix("LICENSES/"): "100644" for path in self.files}
        return None


@pytest.mark.parametrize("prey_spdx", OR_CHOICES)
def test_gpl_maw_or_verdict_and_attribution_choose_the_same_branch(prey_spdx: str) -> None:
    verdict = decide(prey_spdx, "GPL-3.0-only")
    assert verdict.mode is Mode.COPY
    assert verdict.selected_licenses == ("GPL-3.0-only",)

    # The selected GPL grant, not the globally lower-ranked EPL alternative,
    # determines the exact corresponding attribution obligation.
    expected = obligation_for("GPL-3.0-only", verdict.to_dict())
    assert obligation_for(prey_spdx, verdict.to_dict()) == expected


@pytest.mark.parametrize("prey_spdx", OR_CHOICES)
def test_gpl_maw_or_carries_the_selected_reuse_licence_only(prey_spdx: str) -> None:
    verdict = decide(prey_spdx, "GPL-3.0-only")
    assert verdict.mode is Mode.COPY
    assert verdict.selected_licenses == ("GPL-3.0-only",)

    carried = carried_texts(
        SHA, _SyntheticPrey(), prey_spdx, selected_licenses=verdict.selected_licenses
    )
    assert [text.path for text in carried] == ["LICENSES/GPL-3.0-only.txt"]


def test_and_duties_remain_combined_not_a_free_alternative() -> None:
    combined = obligation_for("MIT AND Apache-2.0", {})
    assert combined.kind == "combined"
    assert "MIT:" in combined.text
    assert "Apache-2.0:" in combined.text
