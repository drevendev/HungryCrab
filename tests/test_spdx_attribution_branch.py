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

@pytest.mark.parametrize("prey_spdx", OR_CHOICES)
@pytest.mark.parametrize("with_gpl_text", [True, False])
def test_prepared_or_copy_uses_the_recipient_selected_grant(
    tmp_path, prey_spdx: str, with_gpl_text: bool
) -> None:
    """Exercise the whole immutable COPY receipt, not just the two helpers.

    The prey proves its original OR grant at the root, while only the selected
    GPL licence text is allowed to discharge the resulting publication.
    The negative case must fail before a PR payload can be produced.
    """
    import json

    from hungry_crab.attribution import ATTRIBUTIONS_PATH, prepare_copy_pull_request
    from hungry_crab.errors import CrabError
    from test_attribution import MENU, PREY, SHA as PREY_SHA, FakePrey, _card, _maw, _payload

    maw = _maw(tmp_path)
    (maw / ".crab.yml").write_text("license: GPL-3.0-only\n", encoding="utf-8")
    source = {**PREY, "license": prey_spdx}
    verdict = decide(prey_spdx, "GPL-3.0-only")
    files = {
        (PREY_SHA, "LICENSE"): f"SPDX-License-Identifier: {prey_spdx}\n",
        (PREY_SHA, ".github/workflows/test.yml"): "name: test\n",
        (PREY_SHA, "LICENSES/EPL-2.0.txt"): "SPDX-License-Identifier: EPL-2.0\n",
    }
    if with_gpl_text:
        files[(PREY_SHA, "LICENSES/GPL-3.0-only.txt")] = (
            "SPDX-License-Identifier: GPL-3.0-only\n"
        )
    kwargs = {
        "title": "test: carry synthetic GPL grant",
        "body": "<!-- crab:ci:ci.cache -->\n\nSynthetic receipt.\n",
        "attribution_file": "THIRD_PARTY_NOTICES.md",
        "source_reader": FakePrey(files),
    }
    menu = {**MENU, "prey": source, "verdict": verdict.to_dict()}
    if not with_gpl_text:
        with pytest.raises(CrabError, match="source grant cannot establish"):
            prepare_copy_pull_request(_card(), menu, _payload(source=source), maw, **kwargs)
        return

    prepared = prepare_copy_pull_request(_card(), menu, _payload(source=source), maw, **kwargs)
    generated = {item.path: item.content for item in prepared.files}
    record = json.loads(generated[ATTRIBUTIONS_PATH])["receipts"][0]
    expected = obligation_for("GPL-3.0-only", verdict.to_dict())
    assert record["source"]["license"] == prey_spdx
    assert record["obligation"]["kind"] == expected.kind
    assert record["obligation"]["text"] == expected.text
    assert [item["path"] for item in record["texts"]] == ["LICENSES/GPL-3.0-only.txt"]
    assert "LICENSES/GPL-3.0-only.txt" in generated["THIRD_PARTY_NOTICES.md"]
    assert "LICENSES/EPL-2.0.txt" not in generated["THIRD_PARTY_NOTICES.md"]
