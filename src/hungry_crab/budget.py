"""Aggregate Markdown budget policy for digest page families.

Per-file paging is lossless. This module decides what the whole digest does when the
sum of those pages exceeds its aggregate reading budget: warn, enforce, or ignore the
ceiling. Enforcement removes complete pages only and records every removal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fs import read_text
from .tokens import estimate_tokens

BUDGET_POLICIES = ("warn", "enforce", "off")


@dataclass(frozen=True)
class BudgetResult:
    before_tokens: int
    after_tokens: int
    over_by_tokens: int
    dropped_pages: list[dict[str, Any]]


def _markdown_paths(out_dir: Path, exclude_names: set[str]) -> list[Path]:
    return [
        path
        for path in sorted(out_dir.iterdir())
        if path.is_file()
        and path.suffix == ".md"
        and path.name != "manifest.json"
        and path.name not in exclude_names
    ]


def _markdown_tokens(out_dir: Path, exclude_names: set[str]) -> int:
    return sum(
        estimate_tokens(read_text(path, limit=50_000_000))
        for path in _markdown_paths(out_dir, exclude_names)
    )


def _page_number(base_name: str, candidate: str) -> int | None:
    if candidate == base_name:
        return 1
    base = Path(base_name)
    prefix = f"{base.stem}."
    if not candidate.startswith(prefix) or not candidate.endswith(base.suffix):
        return None
    value = candidate[len(prefix) : -len(base.suffix)]
    if not value.isdigit() or int(value) < 2:
        return None
    return int(value)


def _rewrite_next(path: Path, next_name: str | None) -> None:
    """Repair the pager-owned trailing Next link after aggregate enforcement."""
    lines = read_text(path, limit=50_000_000).rstrip("\n").split("\n")
    if lines and lines[-1].startswith("> Next: `") and lines[-1].endswith("`"):
        lines.pop()
        while lines and lines[-1] == "":
            lines.pop()
    if next_name:
        if lines:
            lines.append("")
        lines.append(f"> Next: `{next_name}`")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def _repair_page_links(out_dir: Path, base_names: list[str]) -> None:
    for base_name in base_names:
        present = sorted(
            (
                (page_number, path)
                for path in out_dir.iterdir()
                if path.is_file()
                and (page_number := _page_number(base_name, path.name)) is not None
            ),
            key=lambda item: item[0],
        )
        for index, (_, path) in enumerate(present):
            next_name = present[index + 1][1].name if index + 1 < len(present) else None
            _rewrite_next(path, next_name)


def _record_maps(
    records: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, int], list[str]]:
    owners: dict[str, str] = {}
    priorities: dict[str, int] = {}
    base_names: list[str] = []
    for record in records:
        owner = str(record.get("name") or "")
        for name in record.get("files", []):
            owners[str(name)] = owner
        raw_priorities = record.get("page_priorities")
        if not isinstance(raw_priorities, dict) or not raw_priorities:
            continue
        names = list(raw_priorities)
        base_names.append(str(names[0]))
        for name, priority in raw_priorities.items():
            if isinstance(priority, int) and not isinstance(priority, bool):
                priorities[str(name)] = priority
    return owners, priorities, base_names


def _forget_page(records: list[dict[str, Any]], name: str) -> None:
    for record in records:
        files = record.get("files")
        if isinstance(files, list) and name in files:
            record["files"] = [item for item in files if item != name]
        priorities = record.get("page_priorities")
        if isinstance(priorities, dict):
            priorities.pop(name, None)


def apply_markdown_policy(
    records: list[dict[str, Any]],
    out_dir: Path,
    *,
    total_budget: int,
    policy: str,
    exclude_names: set[str] | None = None,
) -> BudgetResult:
    """Apply a whole-digest Markdown policy after lossless page production.

    Page priority is explicit producer metadata. Higher numbers are less important and
    are removed first; filename is only a deterministic tie-break between equal-priority
    pages. After each removal the remaining pager chain is repaired so a surviving page
    never points at a page that enforcement removed.
    """
    excluded = exclude_names or set()
    before = _markdown_tokens(out_dir, excluded)
    over_by = max(0, before - total_budget) if policy != "off" else 0
    if policy != "enforce" or before <= total_budget:
        return BudgetResult(before, before, over_by, [])

    owners, priorities, base_names = _record_maps(records)
    dropped: list[dict[str, Any]] = []
    while _markdown_tokens(out_dir, excluded) > total_budget:
        candidates = _markdown_paths(out_dir, excluded)
        if not candidates:
            break
        victim = max(candidates, key=lambda path: (priorities.get(path.name, 5), path.name))
        text = read_text(victim, limit=50_000_000)
        dropped.append(
            {
                "name": victim.name,
                "tokens_est": estimate_tokens(text),
                "priority": priorities.get(victim.name, 5),
                "miner": owners.get(victim.name),
            }
        )
        victim.unlink()
        _forget_page(records, victim.name)
        _repair_page_links(out_dir, base_names)

    after = _markdown_tokens(out_dir, excluded)
    return BudgetResult(before, after, over_by, dropped)
