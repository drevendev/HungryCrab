"""Integrity checks for cached digest producer artifacts.

The digest manifest already records which files each successful miner wrote and the byte
size/producer for every digest file. Treat that metadata as an integrity contract before a
cached digest is reused or compared: absent, truncated, corrupt, or mis-owned evidence must not
silently become an empty fact.

This is intentionally not tamper evidence. A local actor that coherently rewrites an artifact
and its manifest can still evade it; adding cryptographic hashes would be a separate schema
contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _safe_artifact_path(out_dir: Path, name: str) -> Path | None:
    """Resolve only the flat artifact names that digest miners themselves can produce."""
    relative = Path(name)
    if relative.is_absolute() or relative.name != name or "/" in name or "\\" in name:
        return None
    return out_dir / relative


def digest_integrity_errors(out_dir: Path, manifest: dict[str, Any]) -> list[str]:
    """Return inconsistencies between successful producers and their declared artifacts.

    Failed/blocked miners are deliberately skipped: their partial-evidence semantics are owned
    by the miner-health contract. A successful producer, however, has claimed that every file
    in ``miners[*].files`` exists and is represented by ``manifest.files``.
    """
    entries = {
        str(entry.get("name")): entry
        for entry in _dicts(manifest.get("files"))
        if isinstance(entry.get("name"), str) and entry.get("name")
    }
    errors: list[str] = []

    for record in _dicts(manifest.get("miners")):
        if record.get("ok") is not True:
            continue
        miner = str(record.get("name") or "?")
        for name in _strings(record.get("files")):
            entry = entries.get(name)
            if entry is None:
                errors.append(f"{miner}: missing manifest file entry: {name}")
                continue
            if entry.get("miner") != miner:
                errors.append(f"{miner}: producer ownership mismatch: {name}")
                continue

            path = _safe_artifact_path(out_dir, name)
            if path is None:
                errors.append(f"{miner}: invalid producer artifact path: {name}")
                continue
            if path.is_symlink():
                errors.append(f"{miner}: producer artifact is a symlink: {name}")
                continue
            if not path.is_file():
                errors.append(f"{miner}: missing producer artifact: {name}")
                continue

            # JSON miners write object-shaped payloads. Parse before checking size so a corrupt
            # replacement is reported as corruption rather than only as a byte-count mismatch.
            if path.suffix == ".json":
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, ValueError):
                    errors.append(f"{miner}: invalid producer JSON: {name}")
                    continue
                if not isinstance(loaded, dict):
                    errors.append(f"{miner}: invalid producer JSON object: {name}")
                    continue

            expected = entry.get("bytes")
            try:
                actual = path.stat().st_size
            except OSError:
                errors.append(f"{miner}: unreadable producer artifact: {name}")
                continue
            if not isinstance(expected, int):
                errors.append(f"{miner}: missing producer byte count: {name}")
            elif actual != expected:
                errors.append(
                    f"{miner}: producer artifact size mismatch: {name} "
                    f"(expected {expected}, got {actual})"
                )

    return errors
