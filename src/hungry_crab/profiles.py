"""Reviewed repository-type hunger defaults; only the maw selects a profile."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from .errors import UsageError

BASE_HUNGER: dict[str, Any] = {
    "security": True,
    "ci": True,
    "tests": True,
    "tooling": True,
    "ai-config": True,
    "hygiene": True,
    "docs": True,
    "deps": True,
    "history-lesson": True,
    "issue-lesson": True,
    "architecture": "issues-only",
    "code": "ideas-only",
}
PROFILES: dict[str, dict[str, Any]] = {
    "balanced": {},
    "library": {"issue-lesson": "ideas-only", "architecture": "issues-only"},
    "cli": {"deps": "issues-only", "issue-lesson": "ideas-only"},
    "service": {"history-lesson": "issues-only", "deps": "issues-only"},
    "frontend": {"history-lesson": "ideas-only", "deps": "issues-only"},
}
DESCRIPTIONS = {
    "balanced": "All categories; architecture needs review and code stays ideas-only.",
    "library": "Reusable APIs, tests and documentation; demand signals stay ideas-only.",
    "cli": "Command-line tools; dependency changes need review.",
    "service": "Services; dependency and history-derived changes need review.",
    "frontend": "Frontend applications; history stays ideas-only and dependencies need review.",
}


def hunger_for(profile: str) -> dict[str, Any]:
    if profile not in PROFILES:
        raise UsageError(f"unknown hunger profile {profile!r}", hint=", ".join(PROFILES))
    return {**BASE_HUNGER, **PROFILES[profile]}


def infer_profile(root: Path) -> str:
    """Read maw manifests as data; never import modules or run a package manager."""
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        if isinstance(package, dict):
            deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
            if set(deps) & {"react", "vue", "svelte", "vite", "next", "@angular/core"}:
                return "frontend"
            if package.get("bin"):
                return "cli"
    except (OSError, ValueError, TypeError):
        pass
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = data.get("project", {})
        if project.get("scripts"):
            return "cli"
        if project:
            return "library"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if any((root / name).is_file() for name in ("Dockerfile", "compose.yml", "docker-compose.yml")):
        return "service"
    return "balanced"
