"""Read-only filesystem helpers shared across the package."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import Path

DEFAULT_TEXT_LIMIT = 2 * 1024 * 1024
_BINARY_SNIFF = 8192

BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tif", ".tiff", ".psd",
        ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
        ".exe", ".dll", ".so", ".dylib", ".a", ".lib", ".o", ".obj", ".pdb", ".nupkg",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".avi", ".mov", ".mkv", ".webm",
        ".pyc", ".pyo", ".class", ".wasm", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
        ".lockb", ".snk", ".pfx", ".p12", ".der", ".jks", ".keystore",
    }
)  # fmt: skip


def is_reparse_point(info: os.stat_result) -> bool:
    """Windows junctions can report S_IFDIR; their reparse metadata gives them away."""
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


# Bit 29 of a reparse tag marks a name surrogate: an entry that names another place in the
# namespace (a symlink, a directory junction). Cloud placeholders, deduplicated and compressed
# entries are reparse points too, but they hold their own content.
_NAME_SURROGATE_BIT = 0x20000000


def is_link_stat(info: os.stat_result) -> bool:
    """Does an ``os.lstat`` result describe a link that points elsewhere?

    True for a symlink on every platform and for a Windows name surrogate such as a directory
    junction, which ``os.path.islink`` and ``os.walk(followlinks=False)`` treat as a plain
    directory. False for a OneDrive placeholder folder, which a walk must still enter.
    """
    if stat.S_ISLNK(info.st_mode):
        return True
    if not is_reparse_point(info):
        return False
    return bool(getattr(info, "st_reparse_tag", 0) & _NAME_SURROGATE_BIT)


def read_text(path: Path, *, limit: int = DEFAULT_TEXT_LIMIT) -> str:
    """Read a text file leniently: UTF-8 with replacement, truncated at ``limit`` bytes."""
    try:
        with path.open("rb") as handle:
            data = handle.read(limit)
    except OSError:
        return ""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:_BINARY_SNIFF]


def is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with path.open("rb") as handle:
            chunk = handle.read(_BINARY_SNIFF)
    except OSError:
        return False
    return looks_binary(chunk)


def is_ignored(path: str, patterns: Sequence[str]) -> bool:
    """Does a POSIX-relative path match one of the ignore globs?

    Both ``tests/fixtures`` and ``tests/fixtures/**`` cover everything under that directory:
    a pattern also matches any path below it, which is what people mean when they write one.

    The match is case-sensitive on every platform. ``fnmatch.fnmatch`` runs both sides through
    ``os.path.normcase``, which lowercases on Windows and does nothing on Linux, so one
    ``.crab.yml`` ignored different files on the two platforms and nothing warned: each digest
    was consistent with itself. The paths come from git, and git paths are case-sensitive.
    """
    for raw in patterns:
        pattern = raw.strip().rstrip("/")
        if not pattern:
            continue
        if fnmatchcase(path, pattern) or fnmatchcase(path, f"{pattern}/*"):
            return True
        trimmed = pattern.removesuffix("/**").removesuffix("/*")
        if trimmed != pattern and (fnmatchcase(path, trimmed) or fnmatchcase(path, f"{trimmed}/*")):
            return True
    return False


def is_personal_agent_file(path: str) -> bool:
    """Local agent overrides describe a person, even if accidentally committed."""
    parts = path.lower().split("/")
    return parts[-1] == "claude.local.md" or parts[-2:] == [".claude", "settings.local.json"]


def count_lines(text: str) -> int:
    if not text:
        return 0
    lines = text.count("\n")
    if not text.endswith("\n"):
        lines += 1
    return lines
