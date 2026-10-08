"""Learning families do not rename stable nutrient identities."""

from __future__ import annotations

from .nutrients import slugify


def learning_key(category: str, key: str, prey: str) -> str:
    for prefix in (category, "symbols", "signals"):
        scoped = prefix + "." + slugify(prey) + "."
        if prey and key.startswith(scoped):
            return prefix + ".*." + key[len(scoped) :]
    return key
