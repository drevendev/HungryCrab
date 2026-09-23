"""Resolve one logical digest identity to a stable physical generation.

Canonical digests are addressed by prey commit SHA.  A future publisher may keep immutable
physical generations and atomically switch a small ref instead of replacing a populated
directory.  Readers resolve that ref once, then keep the returned path for the rest of the
operation so one read cannot mix two generations.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CrabError

REF_SCHEMA = "hungry-crab.digest-ref/1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class DigestLocation:
    """Logical digest identity plus the physical directory selected for this read."""

    sha: str
    path: Path


def _load_ref(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CrabError(f"cannot read digest ref {path}") from exc
    if not isinstance(value, dict):
        raise CrabError(f"invalid digest ref {path}: expected an object")
    return value


def resolve_canonical_digest(digests_dir: Path, sha: str) -> DigestLocation:
    """Resolve ``sha`` once, preferring an active immutable generation when declared.

    Old caches have no ref and remain readable at ``digests/<sha>/``.  Once a ref exists it is
    authoritative: malformed refs or missing generations fail closed rather than silently
    falling back to a potentially stale legacy directory.
    """
    if not _SAFE_NAME.fullmatch(sha):
        raise CrabError(f"invalid digest identity {sha!r}")

    legacy = digests_dir / sha
    ref_path = digests_dir / ".refs" / f"{sha}.json"
    try:
        ref_mode = ref_path.lstat().st_mode
    except FileNotFoundError:
        return DigestLocation(sha=sha, path=legacy)
    except OSError as exc:
        raise CrabError(f"cannot inspect digest ref {ref_path}") from exc
    if not stat.S_ISREG(ref_mode):
        raise CrabError(f"invalid digest ref {ref_path}: expected a regular file")

    ref = _load_ref(ref_path)
    if ref.get("schema") != REF_SCHEMA:
        raise CrabError(f"invalid digest ref {ref_path}: unsupported schema")
    generation = ref.get("generation")
    if not isinstance(generation, str) or not _SAFE_NAME.fullmatch(generation):
        raise CrabError(f"invalid digest ref {ref_path}: invalid generation")

    generation_dir = digests_dir / ".generations" / sha / generation
    if not generation_dir.is_dir():
        raise CrabError(
            f"digest ref {ref_path} points to missing generation {generation!r}",
            hint="re-run the digest to publish a complete canonical generation",
        )
    return DigestLocation(sha=sha, path=generation_dir)
