from __future__ import annotations

import json

import pytest

from hungry_crab.errors import CrabError, UsageError
from hungry_crab.serve import load_cleanroom_receipts

TRACE = "implemented from a specification, without access to the prey source"


def _receipt(nutrient_id: str, path: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "nutrient_id": nutrient_id,
            "changed_paths": [path],
            "summary": TRACE,
            "checks": ["pytest -q"],
        }
    )


def test_receipt_stream_binds_multiple_documents_by_nutrient_id() -> None:
    first = _receipt("crab:ci:ci.cache", ".github/workflows/ci.yml")
    second = _receipt("crab:tests:tests.smoke", "tests/test_smoke.py")

    loaded = load_cleanroom_receipts(f"\n{first}\n\n{second}\n")

    assert set(loaded) == {"crab:ci:ci.cache", "crab:tests:tests.smoke"}
    assert json.loads(loaded["crab:ci:ci.cache"])["changed_paths"] == [".github/workflows/ci.yml"]


def test_receipt_stream_rejects_duplicate_nutrient_receipts() -> None:
    receipt = _receipt("crab:ci:ci.cache", ".github/workflows/ci.yml")

    with pytest.raises(UsageError, match="duplicate clean-room receipt"):
        load_cleanroom_receipts(f"{receipt}\n{receipt}")


def test_receipt_stream_preserves_strict_receipt_validation() -> None:
    ambiguous = (
        '{"version":1,"nutrient_id":"crab:ci:ci.cache",'
        '"nutrient_id":"crab:ci:other","changed_paths":["ci.yml"],'
        f'"summary":{json.dumps(TRACE)},"checks":["pytest -q"]}}'
    )

    with pytest.raises(CrabError, match="invalid clean-room implementation receipt"):
        load_cleanroom_receipts(ambiguous)


def test_empty_receipt_stream_fails_closed() -> None:
    with pytest.raises(CrabError, match=r"milestone 0\.3"):
        load_cleanroom_receipts(" \n\t")
