from __future__ import annotations


def count_pools(text: str, *, unique: bool = False) -> int:
    """Count non-empty lines; with ``unique`` count distinct, case-folded names."""
    names = [line.strip() for line in text.splitlines() if line.strip()]
    if unique:
        return len({name.casefold() for name in names})
    return len(names)
