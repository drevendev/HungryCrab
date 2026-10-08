"""Fail on configuration typos and wrong shapes before applying maw policy."""

from __future__ import annotations

import difflib
import math
from typing import Any

from .errors import UsageError


def section(value: object, name: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise UsageError(f"{name} must be a mapping")
    unknown = sorted(set(value) - allowed)
    if unknown:
        suggestions = [
            match
            for key in unknown
            for match in difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.5)
        ]
        raise UsageError(
            f"unknown {name} keys: {', '.join(unknown)}",
            hint="did you mean: " + ", ".join(suggestions)
            if suggestions
            else "check the documented configuration keys",
        )
    return value


def validate(data: dict[str, Any]) -> None:
    section(
        data,
        ".crab.yml",
        {
            "license",
            "mode",
            "profile",
            "memory",
            "hunt",
            "hunger",
            "ignore",
            "budget",
            "serve",
            "trust",
            "attribution_file",
            "ledger",
            "scoring",
            "loop",
        },
    )
    from .profiles import BASE_HUNGER

    section(data.get("hunger", {}), "hunger", set(BASE_HUNGER))
    section(data.get("budget", {}), "budget", {"policy"})
    serve = section(
        data.get("serve", {}),
        "serve",
        {"issues", "prs", "max_prs_per_run", "labels", "assignees", "token_env"},
    )
    trust = section(data.get("trust", {}), "trust", {"same_owner", "owners", "bypass_license"})
    max_prs = serve.get("max_prs_per_run", 3)
    if isinstance(max_prs, bool) or not isinstance(max_prs, int) or max_prs < 0:
        raise UsageError("serve.max_prs_per_run must be a non-negative integer")
    for group, names in (
        (data, ("ignore",)),
        (serve, ("labels", "assignees")),
        (trust, ("owners",)),
    ):
        for name in names:
            if name in group and (
                not isinstance(group[name], list)
                or any(not isinstance(item, str) for item in group[name])
            ):
                raise UsageError(f"{name} must be a list of strings")
    for name in ("same_owner", "bypass_license"):
        if name in trust and not isinstance(trust[name], bool):
            raise UsageError(f"trust.{name} must be a boolean")
    scoring = section(
        data.get("scoring", {}),
        "scoring",
        {"categories", "traits", "modes", "effort", "risk", "uptake"},
    )
    for name, values in scoring.items():
        if not isinstance(values, dict) or any(
            not isinstance(key, str)
            or isinstance(weight, bool)
            or not isinstance(weight, int | float)
            or not math.isfinite(weight)
            or not 0 <= weight <= 1
            for key, weight in values.items()
        ):
            raise UsageError(
                f"scoring.{name} must map string keys to finite weights between 0 and 1"
            )
