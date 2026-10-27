"""Learning families do not rename stable nutrient identities."""

from __future__ import annotations

from collections.abc import Mapping

from .nutrients import slugify

# Only suffixes that describe the same behavior across prey may share feedback.
FAMILIES = {
    "history-lesson": frozenset({"fix-prone", "history"}),
    "issue-lesson": frozenset({"raw", "discussions", "reviews", "rerun-recovery"}),
    "signals": frozenset(
        {"fix-prone", "history", "raw", "discussions", "reviews", "rerun-recovery"}
    ),
}


def learning_key(category: str, key: str, prey: str) -> str:
    for prefix in (category, "signals"):
        scoped = prefix + "." + slugify(prey) + "."
        if prey and key.startswith(scoped):
            suffix = key[len(scoped) :]
            if suffix in FAMILIES.get(prefix, frozenset()):
                return prefix + ".*." + suffix
    return key


def ambiguous_overrides(traits: Mapping[str, object]) -> list[str]:
    """Retain old owner keys, but identify wildcard policies that no longer train/apply."""
    return sorted(
        key
        for key in traits
        if ".*." in key
        and key.split(".*.", 1)[1] not in FAMILIES.get(key.split(".*.", 1)[0], frozenset())
    )
