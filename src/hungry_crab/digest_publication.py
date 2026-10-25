"""Writer-side primitives for immutable canonical digest generations.

Canonical readers resolve a small ref to one physical generation. Writers build into a fresh
generation and only switch that ref after the generation is complete. These helpers own
allocation and the atomic visibility boundary, including the final producer-health check that
prevents an incomplete generation from becoming authoritative.
"""

from __future__ import annotations

import json
import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .digest_location import (
    REF_SCHEMA,
    DigestLocation,
    _require_real_directory,
    _retry_windows_ref_io,
)
from .errors import CrabError
from .fs import is_reparse_point
from .miners import MINER_NAMES

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


def check_digest_ref_publishable(digests_dir: Path, sha: str) -> None:
    """Reject unsafe ref entries before expensive mining without deleting unowned data."""
    _require_safe_component(sha, label="sha")
    refs_dir = digests_dir / ".refs"
    try:
        root_info = refs_dir.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CrabError(
            f"cannot inspect digest refs root {refs_dir}",
            hint="inspect the cache entry and repair it manually before retrying",
        ) from exc
    if not stat.S_ISDIR(root_info.st_mode) or is_reparse_point(root_info):
        raise CrabError(
            f"unsafe digest refs root {refs_dir}: expected a real directory",
            hint="move the unexpected .refs entry aside manually; --force cannot replace it",
        )

    ref_path = refs_dir / f"{sha}.json"
    try:
        ref_info = _retry_windows_ref_io(ref_path.lstat)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CrabError(
            f"cannot inspect digest ref {ref_path}",
            hint="inspect cache permissions before retrying",
        ) from exc
    if not stat.S_ISREG(ref_info.st_mode) or is_reparse_point(ref_info):
        raise CrabError(
            f"unsafe digest ref entry {ref_path}: expected a regular file",
            hint="move the unexpected ref entry aside manually; do not delete its contents",
        )


def _require_complete_manifest(generation: DigestGeneration) -> None:
    """Fail closed unless every registered producer completed successfully."""
    manifest_path = generation.path / "manifest.json"
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CrabError(
            "cannot publish digest generation without a readable manifest",
            hint=str(exc),
        ) from exc
    if not isinstance(loaded, dict):
        raise CrabError("cannot publish digest generation with an invalid manifest")

    records = loaded.get("miners")
    if not isinstance(records, list):
        raise CrabError(
            "cannot publish incomplete digest generation",
            hint="manifest has no miners list",
        )

    by_name = {
        str(record.get("name")): record
        for record in records
        if isinstance(record, dict) and record.get("name") is not None
    }
    missing = [name for name in MINER_NAMES if name not in by_name]
    unhealthy = [
        name
        for name in MINER_NAMES
        if name in by_name
        and (by_name[name].get("ok") is not True or by_name[name].get("status", "ok") != "ok")
    ]
    if missing or unhealthy:
        details: list[str] = []
        if missing:
            details.append(f"missing producers: {', '.join(missing)}")
        if unhealthy:
            details.append(f"incomplete producers: {', '.join(unhealthy)}")
        raise CrabError(
            "cannot publish incomplete digest generation",
            hint="; ".join(details),
        )


def _sync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; Windows cannot use this fd-based primitive."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(f"not a directory for digest sync: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_generation_files(generation: DigestGeneration, digests_dir: Path) -> None:
    """Sync complete flat generation files and their directory ancestry before publishing."""
    # Windows _commit uses FlushFileBuffers and needs a writable file handle.
    flags = (os.O_RDWR if os.name == "nt" else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for entry in sorted(generation.path.iterdir()):
        observed = entry.lstat()
        if not stat.S_ISREG(observed.st_mode) or is_reparse_point(observed):
            raise CrabError(f"unsafe digest generation entry {entry}: expected a real file")
        fd = os.open(entry, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                observed.st_dev,
                observed.st_ino,
            ):
                raise CrabError(f"digest generation entry changed during sync: {entry}")
            os.fsync(fd)
        finally:
            os.close(fd)
    for directory in (
        generation.path,
        generation.path.parent,
        generation.path.parent.parent,
        digests_dir,
    ):
        _sync_directory(directory)


def allocate_digest_generation(digests_dir: Path, sha: str) -> DigestGeneration:
    """Allocate a never-reused physical generation directory for one logical digest."""
    _require_safe_component(sha, label="sha")
    generations_dir = digests_dir / ".generations"
    parent = generations_dir / sha
    try:
        # ``digests_dir`` is the configured trusted root. Every cache-owned component below it
        # is inspected with lstat before we descend into the next component.
        digests_dir.mkdir(parents=True, exist_ok=True)
        generations_dir.mkdir(exist_ok=True)
        _require_real_directory(generations_dir, label="digest generations root")
        parent.mkdir(exist_ok=True)
        _require_real_directory(parent, label="digest SHA generation root")
    except OSError as exc:
        raise CrabError(
            f"could not prepare digest generation root for {sha}", hint=str(exc)
        ) from exc

    # UUID collisions are already vanishingly unlikely, but mkdir(exist_ok=False) is the
    # ownership primitive: a previously used generation path is never reopened for mutation.
    for _ in range(8):
        name = uuid4().hex
        path = parent / name
        try:
            path.mkdir()
            _require_real_directory(path, label="digest generation")
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
    """Atomically make one complete generation visible to future readers.

    This function deliberately does not remove the previously published generation. A reader
    may have resolved the old path immediately before the ref switch and is allowed to keep that
    snapshot for the rest of its operation.

    The ref replacement is an atomic visibility boundary. POSIX file/directory fsync
    barriers reduce crash-loss risk, but unsupported filesystems and Windows directory
    durability remain unproven; do not claim crash consistency without fault testing.
    A generation must also contain a readable manifest proving every registered producer
    completed successfully before the ref may advance.
    """
    _require_safe_component(generation.sha, label="sha")
    _require_safe_component(generation.name, label="generation")
    generations_dir = digests_dir / ".generations"
    sha_dir = generations_dir / generation.sha
    expected = sha_dir / generation.name
    if generation.path != expected:
        raise CrabError("digest generation path does not match its canonical identity")
    try:
        _require_real_directory(generations_dir, label="digest generations root")
        _require_real_directory(sha_dir, label="digest SHA generation root")
        _require_real_directory(generation.path, label="digest generation")
    except CrabError as exc:
        raise CrabError(
            f"cannot publish unsafe digest generation {generation.name!r}",
            hint="build and validate a real generation directory before publishing it",
        ) from exc

    _require_complete_manifest(generation)
    try:
        _sync_generation_files(generation, digests_dir)
    except OSError as exc:
        raise CrabError(
            "could not sync complete digest generation before ref switch", hint=str(exc)
        ) from exc

    check_digest_ref_publishable(digests_dir, generation.sha)
    refs_dir = digests_dir / ".refs"
    try:
        refs_dir.mkdir(exist_ok=True)
        _require_real_directory(refs_dir, label="digest refs root")
        _sync_directory(digests_dir)
    except OSError as exc:
        raise CrabError("could not prepare digest refs root", hint=str(exc)) from exc

    ref_path = refs_dir / f"{generation.sha}.json"
    temp_path = refs_dir / f".{generation.sha}.{uuid4().hex}.tmp"
    payload = {"schema": REF_SCHEMA, "generation": generation.name}

    ref_switched = False
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _retry_windows_ref_io(lambda: temp_path.replace(ref_path))
        ref_switched = True
        _sync_directory(refs_dir)
    except OSError as exc:
        if ref_switched:
            raise CrabError(
                f"digest ref may already point to generation {generation.name!r}, "
                "but directory sync failed",
                hint="inspect the canonical ref before retrying; do not replay blindly",
            ) from exc
        raise CrabError(
            f"could not publish digest generation {generation.name!r}",
            hint=str(exc),
        ) from exc
    finally:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)

    return DigestLocation(sha=generation.sha, path=generation.path)
