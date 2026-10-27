"""Additive identities for receiving contexts; stable nutrient IDs remain unchanged."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__
from .fetch.git import GitRunner
from .typeutil import as_dict


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def ledger_identity(value: object) -> str:
    """A read timestamp is not a changed vote or hidden decision."""
    data = dict(as_dict(value))
    data.pop("updated_at", None)
    return fingerprint(data if value is not None else None)


def installed_source_identity(root: Path | None = None) -> dict[str, str]:
    """Identify installed implementation and shipped policy data, not just __init__.py."""
    package = root or Path(__file__).parent
    sources = {
        path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".yml", ".css"}
    }
    return {"version": __version__, "source_sha256": fingerprint(sources)}


def receiving_context(
    root: Path, manifest: dict[str, Any], policy: dict[str, Any], ledger: object
) -> dict[str, Any]:
    prey = as_dict(manifest.get("prey"))
    git = GitRunner(root)
    selected_ref = (
        git.try_run("symbolic-ref", "--quiet", "--short", "HEAD")
        if GitRunner.available() and git.is_repo_root()
        else None
    )
    return {
        "root": str(root.resolve()),
        "sha": prey.get("sha"),
        "selected_ref": selected_ref.strip() if selected_ref else None,
        "worktree": prey.get("worktree", "unknown"),
        "policy": fingerprint(policy),
        "ledger": ledger_identity(ledger),
        "depth": manifest.get("depth"),
        "analysis_identity": manifest.get("analysis_identity"),
        "ignore": manifest.get("ignore"),
        "wiki": manifest.get("wiki"),
    }
