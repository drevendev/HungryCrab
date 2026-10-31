"""Local-only meal lookup: reviewing evidence never acquires or refreshes a donor."""

from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Any

from .artifact_read import read_object
from .cache import Target, maw_paths
from .errors import CrabError
from .fs import is_link_stat


def existing_meal(target: Target, maw: Path, cache: Path | None = None) -> Path:
    directory = maw_paths(maw, cache).meals
    if not directory.is_dir() or is_link_stat(directory.lstat()):
        raise CrabError(f"no menu for {target.label} yet", hint="run crab compare first")
    prefix = maw_paths(maw, cache).meal(target.label, "").name.split("@", 1)[0]
    matches: list[tuple[str, str, Path]] = []
    for index, item in enumerate(islice(directory.iterdir(), 2001)):
        if index >= 2000:
            raise CrabError("meal lookup limit exceeded", hint="select an explicit meal directory")
        if not item.is_dir() or is_link_stat(item.lstat()) or item.name.split("@", 1)[0] != prefix:
            continue
        try:
            menu: dict[str, Any] = read_object(item / "menu.json")
        except CrabError:
            continue
        prey = menu.get("prey")
        receiving = menu.get("receiving_context")
        if not isinstance(prey, dict) or prey.get("label") != target.label:
            continue
        if target.slug is not None and prey.get("url") != target.slug.url:
            continue
        if isinstance(receiving, dict) and receiving.get("root") != str(maw.resolve()):
            continue
        timestamp = menu.get("compared_at", menu.get("generated_at", ""))
        if isinstance(timestamp, str):
            matches.append((timestamp, str(prey.get("sha", "")), item))
    if not matches:
        raise CrabError(f"no menu for {target.label} yet", hint="run crab compare first")
    matches.sort(reverse=True)
    if len(matches) > 1 and matches[0][:2] == matches[1][:2]:
        raise CrabError("ambiguous local meals", hint="select an explicit meal directory")
    return matches[0][2]
