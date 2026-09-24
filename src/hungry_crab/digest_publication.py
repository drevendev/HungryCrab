"""Writer-side primitives for immutable canonical digest generations.

Canonical readers resolve a small ref to one physical generation. Writers build into a fresh
generation and only switch that ref after the generation is complete. These helpers own
allocation and the atomic visibility boundary; deciding whether a digest is complete remains
the digest orchestrator's responsibility.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .digest_location import REF_SCHEMA, DigestLocation
from .errors import CrabError

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class DigestGeneration:
    """A fresh physical generation that has not necessarily been published yet."""

    sha: str
    name: str
    path: Path


def _require_safe_component(value: str, *, label: str) -> None:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise CrabError(f"invalid canonical digest {label} {value!r}")


def allocate_digest_generation(digests_dir: Path, sha: str) -> DigestGeneration:
    """Allocate a never-reused physical generation directory for one logical digest."""
    _require_safe_component(sha, label="sha")
    parent = digests_dir / ".generations" / sha
    parent.mkdir(parents=True, exist_ok=True)

    # UUID collisions are already vanishingly unlikely, but mkdir(exist_ok=False) is the
    # ownership primitive: a previously used generation path is never reopened for mutation.
    for _ in range(8):
        name = uuid4().hex
        path = parent / name
        try:
            path.mkdir()
        except FileExistsError:
            continue
        except OSError as exc:
            raise CrabError(
                f"could not allocate digest generation for {sha}",
                hint=str(exc),
            ) from exc
        return DigestGeneration(sha=sha, name=name, path=path)

    raise CrabError(f"could not allocate a unique digest generation for {sha}")


def publish_digest_generation(digests_dir: Path, generation: DigestGeneration) -> DigestLocation:
    """Atomically make an already-complete generation visible to future readers.

    This function deliberately does not remove the previously published generation. A reader
    may have resolved the old path immediately before the ref switch and is allowed to keep that
    snapshot for the rest of its operation.

    The ref replacement is an atomic visibility boundary, not a claim of power-loss durability.
    The caller must validate completeness before calling this function.
    """
    _require_safe_component(generation.sha, label="sha")
    _require_safe_component(generation.name, label="generation")
    expected = digests_dir / ".generations" / generation.sha / generation.name
    if generation.path != expected:
        raise CrabError("digest generation path does not match its canonical identity")
    if not generation.path.is_dir():
        raise CrabError(
            f"cannot publish missing digest generation {generation.name!r}",
            hint="build and validate the generation before publishing it",
        )

    refs_dir = digests_dir / ".refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    ref_path = refs_dir / f"{generation.sha}.json"
    temp_path = refs_dir / f".{generation.sha}.{uuid4().hex}.tmp"
    payload = {"schema": REF_SCHEMA, "generation": generation.name}

    try:
        temp_path.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temp_path.replace(ref_path)
    except OSError as exc:
        raise CrabError(
            f"could not publish digest generation {generation.name!r}",
            hint=str(exc),
        ) from exc
    finally:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)

    return DigestLocation(sha=generation.sha, path=generation.path)
