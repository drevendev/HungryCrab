"""Plain model output is inert until the host validates its full contract."""

from __future__ import annotations

import pytest
from research_host import strict
from semantic_json import parse_answer


@pytest.mark.parametrize(
    "invalid",
    [
        '{"items":[true]}',
        '{"items":[1,2,3]}',
        '{"items":[-1]}',
        '{"items":[1],"extra":1}',
        '{"items":[],"items":[1]}',
        '{"items":[NaN]}',
        "{}",
        '{"items":[1]} trailing',
    ],
)
def test_invalid_output_cannot_cross_the_host_boundary(invalid: str) -> None:
    schema = strict(
        {"items": {"type": "array", "maxItems": 2, "items": {"type": "integer", "minimum": 0}}}
    )
    with pytest.raises(ValueError):
        parse_answer(invalid, schema)


def test_valid_bounded_answer_is_preserved() -> None:
    schema = strict(
        {"items": {"type": "array", "maxItems": 2, "items": {"type": "integer", "minimum": 0}}}
    )
    assert parse_answer('{"items":[0,2]}', schema) == {"items": [0, 2]}
