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

@pytest.mark.parametrize("numeric_kind", ["integer", "number"])
def test_large_json_integer_is_checked_without_float_conversion(numeric_kind: str) -> None:
    digits = "9" * 400
    answer = '{"items":[' + digits + "]}"

    def schema_with(**bounds: int) -> dict[str, object]:
        return strict(
            {
                "items": {
                    "type": "array",
                    "maxItems": 1,
                    "items": {"type": numeric_kind, **bounds},
                }
            }
        )

    assert parse_answer(answer, schema_with(minimum=0)) == {"items": [int(digits)]}
    with pytest.raises(ValueError, match="answer numeric bound"):
        parse_answer(answer, schema_with(maximum=9))


def test_large_json_integer_wrong_type_is_bounded_validation_error() -> None:
    schema = strict({"items": {"type": "string"}})
    with pytest.raises(ValueError, match="answer type differs from contract"):
        parse_answer('{"items":' + "9" * 400 + "}", schema)
