"""Validate the bounded JSON-schema subset owned by the research host."""

from __future__ import annotations

import json
import math
from typing import Any


def parse_answer(content: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Never turn malformed or contract-invalid output into an approved answer."""
    if len(content.encode("utf-8")) > 96000:
        raise ValueError("answer byte limit")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate answer member")
            result[key] = value
        return result

    try:
        value = json.loads(content, object_pairs_hook=pairs)
    except RecursionError as error:
        raise ValueError("answer parser depth limit") from error
    remaining = 100000

    def check(item: Any, contract: dict[str, Any], depth: int = 0) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 100:
            raise ValueError("answer structure limit")
        allowed = {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "items",
            "enum",
            "const",
            "minItems",
            "maxItems",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
        }
        if set(contract) - allowed:
            raise ValueError("unsupported host contract keyword")
        kind = contract.get("type")
        # JSON integers are arbitrary precision; math.isfinite(large_int)
        # raises OverflowError while converting to float. Only floats need it.
        finite_number = type(item) is int or (
            type(item) is float and math.isfinite(item)
        )
        types = {
            "object": isinstance(item, dict),
            "array": isinstance(item, list),
            "string": isinstance(item, str),
            "integer": type(item) is int,
            "number": finite_number,
            "boolean": type(item) is bool,
            "null": item is None,
        }
        if kind not in types or not types[kind]:
            raise ValueError("answer type differs from contract")
        if "const" in contract and (
            type(item) is not type(contract["const"]) or item != contract["const"]
        ):
            raise ValueError("answer constant differs from contract")
        if "enum" in contract and not any(
            type(item) is type(v) and item == v for v in contract["enum"]
        ):
            raise ValueError("answer enum differs from contract")
        if isinstance(item, dict):
            props = contract.get("properties", {})
            if set(contract.get("required", [])) - set(item):
                raise ValueError("missing required answer member")
            if contract.get("additionalProperties") is False and set(item) - set(props):
                raise ValueError("unexpected answer member")
            for key, child in item.items():
                if key in props:
                    check(child, props[key], depth + 1)
        elif isinstance(item, list):
            if not contract.get("minItems", 0) <= len(item) <= contract.get("maxItems", 100000):
                raise ValueError("answer array bound")
            for child in item:
                check(child, contract["items"], depth + 1)
        elif isinstance(item, str):
            if not contract.get("minLength", 0) <= len(item) <= contract.get("maxLength", 96000):
                raise ValueError("answer string bound")
        elif type(item) in (int, float):
            if not finite_number or not contract.get(
                "minimum", -math.inf
            ) <= item <= contract.get("maximum", math.inf):
                raise ValueError("answer numeric bound")

    check(value, schema)
    if not isinstance(value, dict):
        raise ValueError("answer must be an object")
    return value
