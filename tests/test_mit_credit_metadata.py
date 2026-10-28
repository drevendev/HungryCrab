"""Synthetic MIT credits; exact operative grant/rider checks stay mandatory."""

from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.licensing import detect_from_text, detect_in_repo

MIT = (Path(__file__).parents[1] / "LICENSE").read_text(encoding="utf-8")


def notice(credit: str) -> str:
    return MIT.replace("Copyright (c) 2026 Dreven", credit)


@pytest.mark.parametrize(
    "credit",
    [
        "Copyright (c) 2026 Mira Vale and contributors",
        "Copyright Mira Vale and contributors",
        "Copyright (c) 2026 Mira Vale and others",
        "Copyright (c) 2026 Mira Vale and the samplekit contributors",
        "Copyright (c) 2026 Mira Vale, Alex Stone.",
        "Copyright (c) 2026 sampleco, LLC.",
        "Copyright (c) 2026 sample-kit dev team: Mira Vale, Alex Stone",
        "Copyright (c) 2026, Mira Vale",
        "Copyright (c) 2010-202x The samplekit developers",
        "Copyright (c) 2010-202X The samplekit developers",
    ],
)
@pytest.mark.parametrize("spdx", [False, True])
def test_conventional_credit_forms_keep_the_exact_mit_grant(credit: str, spdx: bool) -> None:
    raw = notice(credit)
    if spdx:
        raw = "SPDX-License-Identifier: MIT\n" + raw
    assert detect_from_text(raw) == ("MIT", 0.99)


@pytest.mark.parametrize(
    "holder",
    [
        "Mira Vale and contributors for research only",
        "Mira Vale and the NoExternalUse contributors",
        "Mira Vale and the nosale contributors",
        "Mira Vale and the sharealike contributors",
        "The NoSharing developers",
        "The notforsale developers",
        "nosale, LLC.",
        "sharealike, LLC.",
        "NoExternalUse dev team: Mira Vale, Alex Stone",
        "Mira Vale, Permission Required",
        "Mira Vale and contributors. No distribution",
    ],
)
@pytest.mark.parametrize("spdx", [False, True])
def test_credit_shapes_cannot_hide_restrictions(holder: str, spdx: bool) -> None:
    raw = notice("Copyright (c) 2026 " + holder)
    if spdx:
        raw = "SPDX-License-Identifier: MIT\n" + raw
    assert detect_from_text(raw) == (None, 0.0)


def test_credit_recognition_cannot_hide_changed_terms_or_appended_riders() -> None:
    raw = notice("Copyright (c) 2026 Mira Vale and contributors")
    assert detect_from_text(raw + "\nCommercial use requires permission.\n") == (None, 0.0)
    assert detect_from_text(raw.replace("without restriction", "for research purposes")) == (
        None,
        0.0,
    )


def test_both_root_mit_notices_are_resolved_without_dropping_either(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").write_text(MIT, encoding="utf-8")
    (tmp_path / "LICENSE.upstream").write_text(
        notice("Copyright (c) 2026 Mira Vale, Alex Stone."), encoding="utf-8"
    )
    result = detect_in_repo(tmp_path, [])
    assert result.spdx == "MIT"
    assert set(result.license_files) == {"LICENSE", "LICENSE.upstream"}
    assert not result.human_review
