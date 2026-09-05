"""Narrowing helpers for values parsed from untyped JSON, YAML and TOML."""

from __future__ import annotations

from typing import Any


def as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
