"""Bounded readers shared by local reports and the MCP adapter; never acquire prey."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from .errors import CrabError

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 128
MAX_PAGE_BYTES = 512 * 1024


def _check_json_depth(text: str) -> None:
    """Bound nesting before allocating containers, ignoring brackets inside JSON strings."""
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON artifact exceeds its nesting limit")
        elif char in "]}":
            depth -= 1


def read_bytes(path: Path, limit: int) -> bytes:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 1024:
            raise CrabError(f"artifact must be a regular file: {path.name}")
        with path.open("rb") as source:
            content = source.read(limit + 1)
        if len(content) > limit:
            raise CrabError(f"artifact exceeds its {limit}-byte read limit: {path.name}")
        return content
    except OSError as exc:
        raise CrabError(f"cannot read artifact {path.name}: {exc.strerror}") from exc


def read_object(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    try:
        text = read_bytes(path, MAX_JSON_BYTES).decode("utf-8-sig")
        _check_json_depth(text)
        data = json.loads(text, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CrabError(f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(data, dict):
        raise CrabError(f"artifact must contain a JSON object: {path.name}")
    return data


def markdown_pages(directory: Path, manifest: dict[str, Any]) -> list[str]:
    from .budget import artifact_owner

    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise CrabError("digest has no artifact table")
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise CrabError("digest has an invalid artifact record")
        name = entry["name"]
        if (
            Path(name).name != name
            or "/" in name
            or "\\" in name
            or artifact_owner(name) is None
            or artifact_owner(name) != entry.get("miner")
        ):
            raise CrabError("digest has an invalid artifact name or producer")
        if name.endswith(".md"):
            names.append(name)
    if len({name.casefold() for name in names}) != len(names):
        raise CrabError("digest contains duplicate page names")
    return names
