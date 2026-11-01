"""Bounded document structure; grant matching stays in the detector.

Only an explicit project/third-party boundary is recognized. Unconsumed text is
never discarded as a notice, and a component's terms do not become project terms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LicenseSection:
    role: str
    label: str
    text: str
    start: int
    end: int


def composite_sections(text: str) -> list[LicenseSection] | None:
    """Parse the explicit, fully delimited project + quoted component form."""
    if len(text.encode("utf-8")) > 200_000:
        return None
    lines = text.splitlines()
    boundaries = [
        i for i, line in enumerate(lines) if line.strip().lower() == "end of terms and conditions"
    ]
    if len(boundaries) != 1 or boundaries[0] > 100:
        return None
    boundary = boundaries[0]
    cursor = boundary + 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    heading: list[str] = []
    while cursor < len(lines) and lines[cursor].strip():
        heading.append(lines[cursor].strip())
        cursor += 1
    if " ".join(heading).lower() != (
        "the externally maintained libraries from which parts of the software is derived are:"
    ):
        return None
    result = [LicenseSection("project-grant", "project", "\n".join(lines[:boundary]), 1, boundary)]
    while cursor < len(lines):
        if not lines[cursor].strip():
            cursor += 1
            continue
        match = re.fullmatch(
            r"- ([A-Za-z0-9][A-Za-z0-9./_-]{0,100}), licensed "
            r"(?:as follows|under the [A-Za-z0-9 .-]{1,80} license):",
            lines[cursor],
        )
        if match is None or len(result) >= 65:
            return None
        label = match.group(1)
        cursor += 1
        if cursor >= len(lines) or lines[cursor].strip() != '"""':
            return None
        cursor += 1
        start = cursor
        while cursor < len(lines) and lines[cursor].strip() != '"""':
            if lines[cursor].strip() and not lines[cursor].startswith("    "):
                return None
            cursor += 1
        if cursor == len(lines) or start == cursor:
            return None
        content = "\n".join(
            line[4:] if line.startswith("    ") else line for line in lines[start:cursor]
        )
        result.append(LicenseSection("component-notice", label, content, start + 1, cursor))
        cursor += 1
    return result if len(result) > 1 else None


def attribution_reference(text: str) -> str | None:
    """An entire short holder notice followed by an explicit same-tree reference.

    The caller verifies holder metadata and the referenced grant separately.
    """
    if len(text) > 1500:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines) > 8:
        return None
    match = re.fullmatch(
        r"See ([A-Za-z0-9._/-]+) for the (?:full )?licen[cs]e text\.", lines[-1], re.I
    )
    return match.group(1) if match else None
