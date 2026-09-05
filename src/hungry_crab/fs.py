"""Read-only filesystem helpers shared by the miners."""

from __future__ import annotations

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


def count_lines(text: str) -> int:
    if not text:
        return 0
    lines = text.count("\n")
    if not text.endswith("\n"):
        lines += 1
    return lines
