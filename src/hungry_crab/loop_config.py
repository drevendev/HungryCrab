"""Strict, maw-owned policy for the scheduled loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import UsageError

AUTONOMIES = ("read", "serve", "work")
PROTECTED = (".github/**", ".git/**", ".crab.yml", "LICENSE*", "LICENCE*", "COPYING*")


def positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise UsageError(f"{name} must be a positive integer")
    return value


def mapping(value: object, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise UsageError(f"{name} must be a mapping")
    unknown = set(value) - keys
    if unknown:
        raise UsageError(f"unknown {name} keys: {', '.join(sorted(unknown))}")
    return value


def strings(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise UsageError(f"{name} must be a list of non-empty strings")
    return value


@dataclass
class LoopSettings:
    cadence: str = "daily"
    autonomy: str = "serve"
    work_authorized: bool = False
    prey: list[str] = field(default_factory=list)
    discovery: bool = False
    phases_per_day: int = 4
    prey_per_round: int = 2
    open_issues_max: int = 10
    open_prs_max: int = 2
    max_attempts: int = 3
    lease_minutes: int = 60
    protected: list[str] = field(default_factory=lambda: list(PROTECTED))
    release_files: list[str] = field(default_factory=lambda: ["CHANGELOG.md"])

    @classmethod
    def load(cls, value: object) -> LoopSettings:
        data = mapping(
            value,
            "loop",
            {
                "cadence",
                "autonomy",
                "work_authorized",
                "prey",
                "discovery",
                "budget",
                "max_attempts",
                "lease_minutes",
                "protected",
                "release_files",
            },
        )
        result = cls()
        for key, allowed in (("cadence", ("daily", "weekly")), ("autonomy", AUTONOMIES)):
            option = data.get(key, getattr(result, key))
            if option not in allowed:
                raise UsageError(f"loop.{key} must be one of: {', '.join(allowed)}")
            setattr(result, key, option)
        authorization = data.get("work_authorized", False)
        if not isinstance(authorization, bool):
            raise UsageError("loop.work_authorized must be a boolean")
        result.work_authorized = authorization
        discovery = data.get("discovery", False)
        if not isinstance(discovery, bool):
            raise UsageError("loop.discovery must be a boolean")
        result.discovery = discovery
        result.prey = strings(data.get("prey", []), "loop.prey")
        result.protected += strings(data.get("protected", []), "loop.protected")
        result.release_files = strings(
            data.get("release_files", ["CHANGELOG.md"]), "loop.release_files"
        )
        for key in ("max_attempts", "lease_minutes"):
            setattr(result, key, positive(data.get(key, getattr(result, key)), f"loop.{key}"))
        budget_keys = {"phases_per_day", "prey_per_round", "open_issues_max", "open_prs_max"}
        budget = mapping(data.get("budget", {}), "loop.budget", budget_keys)
        for key in budget_keys:
            setattr(
                result, key, positive(budget.get(key, getattr(result, key)), f"loop.budget.{key}")
            )
        if result.prey_per_round > 3:
            raise UsageError("loop.budget.prey_per_round must not exceed 3")
        return result
