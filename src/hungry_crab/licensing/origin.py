"""Content-origin ceilings for nutrient licence modes.

A repository licence only governs material licensed by that repository. Nutrients that carry
third-party prose need an additional ceiling before they can be served. Unknown origins fail
closed instead of inheriting the repository verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .matrix import Mode


class ContentOrigin(StrEnum):
    """Who controls the copyright of prose carried by a nutrient."""

    LICENSED = "licensed"
    COMMENTERS = "commenters"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OriginVerdict:
    """A repository mode after applying the content-origin ceiling."""

    mode: Mode
    origin: ContentOrigin
    reason: str = ""


_MODE_RANK: tuple[Mode, ...] = (
    Mode.COPY,
    Mode.COPY_FILE,
    Mode.REIMPLEMENT,
    Mode.IDEAS_ONLY,
    Mode.HUMAN,
)
_ORIGIN_CEILING: dict[ContentOrigin, Mode] = {
    ContentOrigin.COMMENTERS: Mode.IDEAS_ONLY,
    ContentOrigin.UNKNOWN: Mode.HUMAN,
}


def normalize_origin(origin: object) -> ContentOrigin:
    """Return a known origin; malformed or future values fail closed to ``UNKNOWN``."""
    try:
        return ContentOrigin(str(origin))
    except ValueError:
        return ContentOrigin.UNKNOWN


def cap_mode_for_origin(mode: object, origin: object) -> OriginVerdict:
    """Narrow ``mode`` to the ceiling allowed by ``origin``; never widen it.

    Invalid modes are treated as ``HUMAN`` as a final defence-in-depth boundary. The normal
    caller receives modes from the deterministic licence matrix.
    """
    normalized_origin = normalize_origin(origin)
    try:
        repository_mode = Mode(str(mode))
    except ValueError:
        repository_mode = Mode.HUMAN

    ceiling = _ORIGIN_CEILING.get(normalized_origin)
    if ceiling is None:
        return OriginVerdict(repository_mode, normalized_origin)

    capped = max((repository_mode, ceiling), key=_MODE_RANK.index)
    if normalized_origin is ContentOrigin.COMMENTERS:
        reason = (
            "commenter-origin text is capped at IDEAS_ONLY; carry the need and link, not the text"
        )
    else:
        reason = "unrecognised content origin fails closed at HUMAN"
    return OriginVerdict(capped, normalized_origin, reason)
