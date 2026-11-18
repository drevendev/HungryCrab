"""Downloaded public symbols cannot authorize attribution with the wrong binding key."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

classify_identity = cast(
    Callable[[str, str], dict[str, object]],
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/relay_public_symbols.py"))[
        "classify_identity"
    ],
)


@pytest.mark.parametrize(
    ("guid", "returned_age"),
    [
        ("44294CBD-55E8-CD67-B3D5-6B689F03152D", 4),
        ("9DC73321-ED08-8930-07DF-02E641F44B87", 3),
    ],
)
def test_actual_downloaded_guid_matches_but_age_mismatch_is_held(
    guid: str, returned_age: int
) -> None:
    expected = guid.replace("-", "") + "1"
    result = classify_identity(f"  GUID: {{{guid}}}\n  Age: {returned_age}\n", expected)
    assert result["identity_status"] == "IDENTITY_MISMATCH_HELD"
    assert result["actual_age"] == returned_age
    assert result["expected_age"] == 1


def test_only_complete_matching_guid_and_age_qualify() -> None:
    guid = "44294CBD-55E8-CD67-B3D5-6B689F03152D"
    expected = guid.replace("-", "") + "1"
    assert (
        classify_identity(f"GUID: {{{guid}}}\nAge: 1\n", expected)["identity_status"] == "MATCHED"
    )
    assert classify_identity(f"GUID: {{{guid}}}\n", expected)["identity_status"] == (
        "IDENTITY_UNAVAILABLE_HELD"
    )
    assert classify_identity("Age: 1\n", expected)["identity_status"] == "IDENTITY_UNAVAILABLE_HELD"
    other = "00000000-0000-0000-0000-000000000000"
    assert classify_identity(f"GUID: {{{other}}}\nAge: 1\n", expected)["identity_status"] == (
        "IDENTITY_MISMATCH_HELD"
    )
