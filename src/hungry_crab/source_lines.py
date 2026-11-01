"""Physical source lines: CR/LF boundaries, preserving Unicode source characters."""

from __future__ import annotations

import io


def physical_lines(text: str, *, keepends: bool = False) -> list[str]:
    """Match parser line coordinates without treating Unicode text as newlines.

    Universal newline recognition accepts LF, CRLF and bare CR. Keeping the
    original terminators also permits exact decoded declaration-byte hashes.
    NEL, vertical tabs, form feeds and Unicode separators stay source characters.
    """
    lines = io.StringIO(text, newline="").readlines()
    return lines if keepends else [line.rstrip("\r\n") for line in lines]
