"""Owner-owned recommendation exclusions; dependency facts remain untouched."""

from __future__ import annotations

import re
from typing import Any

from .errors import UsageError

ECOSYSTEMS = {"python", "npm", "rust", "go", "ruby", "jvm", "dotnet", "php"}
SCOPE = re.compile(
    r"(?:runtime|dev|build|peer|optional|central|indirect|(?:group|optional):[A-Za-z0-9_.-]{1,80})"
)


def exclusions(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 32:
        raise UsageError("dependency_scope_exclusions must be a list of at most 32 exact scopes")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {"ecosystem", "scope", "reason"}:
            raise UsageError("dependency scope exclusion requires exactly ecosystem, scope, reason")
        if not all(isinstance(v, str) for v in row.values()):
            raise UsageError("dependency scope exclusion fields must be strings")
        eco, scope, reason = row["ecosystem"], row["scope"], row["reason"]
        if eco not in ECOSYSTEMS or SCOPE.fullmatch(scope) is None:
            raise UsageError("unknown dependency ecosystem or scope; wildcards are unsupported")
        if not reason.strip() or len(reason) > 500 or any(ord(c) < 32 for c in reason):
            raise UsageError("dependency scope exclusion requires a bounded explanation")
        if (eco, scope) in seen:
            raise UsageError("duplicate dependency scope exclusion")
        seen.add((eco, scope))
        result.append(dict(row))
    return result


def partition(
    rows: list[dict[str, Any]], rules: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    indexed = {(row["ecosystem"], row["scope"]): row["reason"] for row in exclusions(rules)}
    eligible, hidden = [], []
    for row in rows:
        reason = indexed.get((str(row.get("ecosystem")), str(row.get("kind"))))
        if reason:
            hidden.append({"dependency": row, "reason": reason, "effect": "recommendation-only"})
        else:
            eligible.append(row)
    return eligible, hidden
