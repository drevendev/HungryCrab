"""Fetch a repository's issues (never pull requests) into the cache as JSONL.

Two views are merged: the newest issues by update time and the most reacted-to issues from the
search API. Bodies are kept only as short excerpts; issue text is untrusted and its content is
``IDEAS_ONLY`` by design.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..cache import Slug
from ..errors import CrabError
from ..typeutil import as_dict, as_list
from .github import GitHubClient

EXCERPT_CHARS = 600
PER_PAGE = 100
MAX_PAGES = 30


def _noop(_: str) -> None:
    return None


def slim_issue(raw: dict[str, Any], *, via: str = "list") -> dict[str, Any]:
    reactions = as_dict(raw.get("reactions"))
    milestone = as_dict(raw.get("milestone"))
    body = raw.get("body")
    labels = [
        str(as_dict(label).get("name") or label)
        for label in as_list(raw.get("labels"))
        if isinstance(label, dict | str)
    ]
    return {
        "number": raw.get("number"),
        "title": str(raw.get("title") or ""),
        "state": str(raw.get("state") or "").lower(),
        "labels": labels,
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "closed_at": raw.get("closed_at"),
        "comments": raw.get("comments") if isinstance(raw.get("comments"), int) else 0,
        "reactions": reactions.get("total_count")
        if isinstance(reactions.get("total_count"), int)
        else 0,
        "author_association": raw.get("author_association"),
        "milestone": milestone.get("title"),
        "locked": bool(raw.get("locked")),
        "url": raw.get("html_url"),
        "body_excerpt": body[:EXCERPT_CHARS] if isinstance(body, str) else "",
        "via": via,
    }


def fetch_issues(
    client: GitHubClient,
    slug: Slug,
    *,
    limit: int = 300,
    top_reactions: int = 50,
    log: Callable[[str], None] = _noop,
) -> list[dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    page = 1
    while len(items) < limit and page <= MAX_PAGES:
        batch = client.get(
            f"repos/{slug}/issues?state=all&per_page={PER_PAGE}&page={page}&sort=updated&direction=desc"
        )
        entries = as_list(batch)
        for raw in entries:
            data = as_dict(raw)
            if "pull_request" in data or not isinstance(data.get("number"), int):
                continue
            items.setdefault(data["number"], slim_issue(data))
            if len(items) >= limit:
                break
        if len(entries) < PER_PAGE:
            break
        page += 1
    log(f"fetched {len(items)} issues of {slug}")
    if top_reactions > 0:
        query = f"repo:{slug}+is:issue+sort:reactions-%2B1-desc"
        try:
            result = as_dict(client.get(f"search/issues?q={query}&per_page={top_reactions}"))
        except CrabError as exc:
            log(f"warning: could not fetch top issues by reactions: {exc.message}")
            result = {}
        added = 0
        for raw in as_list(result.get("items")):
            data = as_dict(raw)
            number = data.get("number")
            if isinstance(number, int) and number not in items and "pull_request" not in data:
                items[number] = slim_issue(data, via="search")
                added += 1
        if added:
            log(f"added {added} top issues by reactions")
    return [items[number] for number in sorted(items, reverse=True)]


def write_issues(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def read_issues(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
            except ValueError:
                continue
            if isinstance(loaded, dict):
                items.append(loaded)
    return items
