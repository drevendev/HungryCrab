"""Resolve one logical digest identity to a stable physical generation.

Canonical digests are addressed by prey commit SHA.  A future publisher may keep immutable
physical generations and atomically switch a small ref instead of replacing a populated
directory.  Readers resolve that ref once, then keep the returned path for the rest of the
operation so one read cannot mix two generations.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .errors import CrabError
from .fs import is_reparse_point

REF_SCHEMA = "hungry-crab.digest-ref/1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REF_RETRY_ATTEMPTS = 10
_REF_RETRY_DELAY_SECONDS = 0.05
_IS_WINDOWS = os.name == "nt"
_R = TypeVar("_R")


def _retry_windows_ref_io(operation: Callable[[], _R]) -> _R:
    """Retry bounded Windows sharing violations, but not other I/O errors or Unix denials."""
    attempts = _REF_RETRY_ATTEMPTS if _IS_WINDOWS else 1
    for attempt in range(attempts):
        try:
            return operation()
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(_REF_RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable ref retry state")


@dataclass(frozen=True)
class DigestLocation:
    """Logical digest identity plus the physical directory selected for this read."""

    sha: str
    path: Path


def _require_real_directory(path: Path, *, label: str) -> None:
    """Require a real directory, refusing symlinks and Windows reparse points."""
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise CrabError(f"missing {label} {path}") from exc
    except OSError as exc:
        raise CrabError(f"cannot inspect {label} {path}", hint=str(exc)) from exc
    if not stat.S_ISDIR(info.st_mode) or is_reparse_point(info):
        raise CrabError(f"invalid {label} {path}: expected a real directory")


def _load_ref(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_retry_windows_ref_io(lambda: path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise CrabError(f"cannot read digest ref {path}") from exc
    if not isinstance(value, dict):
        raise CrabError(f"invalid digest ref {path}: expected an object")
    return value


def resolve_canonical_digest(digests_dir: Path, sha: str) -> DigestLocation:
    """Resolve ``sha`` once, preferring an active immutable generation when declared.

    Old caches have no ref and remain readable at ``digests/<sha>/``.  Once the refs root exists
    it is cache-owned and must itself be a real directory.  Once a ref exists it is authoritative:
    malformed refs or missing generations fail closed rather than silently falling back to a
    potentially stale legacy directory.
    """
    if not _SAFE_NAME.fullmatch(sha):
        raise CrabError(f"invalid digest identity {sha!r}")

    legacy = digests_dir / sha
    refs_dir = digests_dir / ".refs"
    try:
        _require_real_directory(refs_dir, label="digest refs root")
    except CrabError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return DigestLocation(sha=sha, path=legacy)
        raise

    ref_path = refs_dir / f"{sha}.json"
    try:
        ref_info = _retry_windows_ref_io(ref_path.lstat)
    except FileNotFoundError:
        return DigestLocation(sha=sha, path=legacy)
    except OSError as exc:
        raise CrabError(f"cannot inspect digest ref {ref_path}") from exc
    if not stat.S_ISREG(ref_info.st_mode) or is_reparse_point(ref_info):
        raise CrabError(f"invalid digest ref {ref_path}: expected a regular file")

    ref = _load_ref(ref_path)
    if ref.get("schema") != REF_SCHEMA:
        raise CrabError(f"invalid digest ref {ref_path}: unsupported schema")
    generation = ref.get("generation")
    if not isinstance(generation, str) or not _SAFE_NAME.fullmatch(generation):
        raise CrabError(f"invalid digest ref {ref_path}: invalid generation")

    generations_dir = digests_dir / ".generations"
    sha_dir = generations_dir / sha
    generation_dir = sha_dir / generation
    try:
        _require_real_directory(generations_dir, label="digest generations root")
        _require_real_directory(sha_dir, label="digest SHA generation root")
        _require_real_directory(generation_dir, label="digest generation")
    except CrabError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise CrabError(
                f"digest ref {ref_path} points to missing generation {generation!r}",
                hint="re-run the digest to publish a complete canonical generation",
            ) from exc
        raise CrabError(
            f"digest ref {ref_path} does not resolve to a safe generation {generation!r}",
            hint="canonical generation path components must be real directories, not symlinks",
        ) from exc
    return DigestLocation(sha=sha, path=generation_dir)
