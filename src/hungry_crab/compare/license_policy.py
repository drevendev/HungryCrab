"""Carry licence uncertainty and evidence scopes from a digest into every candidate."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from ..typeutil import as_dict, as_list


def licence_exceptions(licence: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        as_dict(item)
        for item in as_list(licence.get("exceptions"))
        if as_dict(item).get("kind") != "vendored-license"
    ]


def review_reasons(licence: dict[str, Any]) -> list[str]:
    reasons = [f"conflict: {item}" for item in as_list(licence.get("conflicts"))]
    reasons.extend(
        f"{item.get('kind')}: {item.get('path', '?')} ({item.get('spdx', 'NOASSERTION')})"
        for item in licence_exceptions(licence)
    )
    if licence.get("human_review") is True and not reasons:
        reasons.append("the licence miner requires human review")
    return reasons


def covered_exceptions(licence: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    found = []
    for item in licence_exceptions(licence):
        raw = item.get("path")
        if (
            item.get("kind") not in {"header", "nested-license"}
            or not isinstance(raw, str)
            or not raw
            or PurePosixPath(raw).is_absolute()
            or PurePosixPath(raw).as_posix() != raw
            or ".." in PurePosixPath(raw).parts
        ):
            found.append(item)  # An invalid scope cannot establish permission for any file.
            continue
        scope = PurePosixPath(raw)
        if item.get("kind") == "nested-license":
            scope = scope.parent
        if not paths or any(
            any(char in path for char in "*?[")
            or PurePosixPath(path) == scope
            or scope in PurePosixPath(path).parents
            or PurePosixPath(path) in scope.parents
            for path in paths
        ):
            found.append(item)
    return found
