"""Aggregate Markdown budget policy and current-run digest artifact reconciliation.

Per-file paging is lossless. This module decides what the whole digest does when the
sum of those pages exceeds its aggregate reading budget: warn, enforce, or ignore the
ceiling. Before budgeting, it also removes digest artifacts that no current-run producer
emitted, so selective and failed reruns cannot leave stale evidence behind.

Both steps stop at what the crab owns. A digest directory is not always the crab's alone:
``--out`` can name a directory the caller already uses, or the repository itself, and a cache
written by an older crab can still hold meal files. A file is the crab's when a registered
miner declares its name — its JSON artifact, or its Markdown page family — and every other
file in the directory is neither counted, nor budgeted, nor deleted, nor listed as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fs import read_text
from .miners import ALL_MINERS
from .tokens import estimate_tokens

BUDGET_POLICIES = ("warn", "enforce", "off")
MANIFEST_NAME = "manifest.json"

_JSON_OWNERS: dict[str, str] = {
    miner.json_file: miner.name for miner in ALL_MINERS if miner.json_file
}
_MARKDOWN_OWNERS: dict[str, str] = {
    miner.md_file: miner.name for miner in ALL_MINERS if miner.md_file
}


@dataclass(frozen=True)
class BudgetResult:
    before_tokens: int
    after_tokens: int
    over_by_tokens: int
    dropped_pages: list[dict[str, Any]]


def page_name(base_name: str, page_number: int) -> str:
    """``history.md`` is page 1 of its family, ``history.2.md`` page 2, and so on."""
    if page_number <= 1:
        return base_name
    base = Path(base_name)
    return f"{base.stem}.{page_number}{base.suffix}"


def page_number(base_name: str, candidate: str) -> int | None:
    """Which page of the ``base_name`` family ``candidate`` is, or None when it is not one."""
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


def artifact_owner(name: str) -> str | None:
    """The registered miner whose artifact ``name`` is, or None for a file the crab does not own.

    Ownership is declared by the registry, not inferred from an extension: ``notes.md`` next to
    ``history.md`` is a stranger, and so is ``history.notes.md``, because only ``history.md``
    and its numbered pages belong to the history miner.
    """
    owner = _JSON_OWNERS.get(name)
    if owner is not None:
        return owner
    for base_name, miner_name in _MARKDOWN_OWNERS.items():
        if page_number(base_name, name) is not None:
            return miner_name
    return None


def is_digest_artifact(name: str) -> bool:
    """Whether ``name`` belongs to a digest: the manifest, or a registered miner's artifact."""
    return name == MANIFEST_NAME or artifact_owner(name) is not None


def _markdown_paths(out_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(out_dir.iterdir())
        if path.is_file() and path.suffix == ".md" and artifact_owner(path.name) is not None
    ]


def _markdown_tokens(out_dir: Path) -> int:
    return sum(
        estimate_tokens(read_text(path, limit=50_000_000)) for path in _markdown_paths(out_dir)
    )


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
                (number, path)
                for path in out_dir.iterdir()
                if path.is_file() and (number := page_number(base_name, path.name)) is not None
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


def _reconcile_current_outputs(records: list[dict[str, Any]], out_dir: Path) -> None:
    """Remove the crab's own artifacts that no producer emitted during this recomputation.

    A digest directory is reused for the same commit, so selective reruns and producer
    failures can otherwise leave a previous run's files on disk. Those files are not current
    evidence. Only a registered miner's artifacts are removable: ``manifest.json`` is replaced
    after reconciliation, meal files belong to a maw, and a file whose owner nobody can name
    is the caller's, not stale evidence — ``--out`` may point at a directory full of them.
    """
    current = {
        str(name) for record in records for name in record.get("files", []) if isinstance(name, str)
    }
    for path in out_dir.iterdir():
        if not path.is_file() or path.name in current or path.name == MANIFEST_NAME:
            continue
        if artifact_owner(path.name) is not None:
            path.unlink()


def apply_markdown_policy(
    records: list[dict[str, Any]],
    out_dir: Path,
    *,
    total_budget: int,
    policy: str,
) -> BudgetResult:
    """Reconcile current outputs, then apply the whole-digest Markdown policy.

    Page priority is explicit producer metadata. Higher numbers are less important and
    are removed first; filename is only a deterministic tie-break between equal-priority
    pages. After each removal the remaining pager chain is repaired so a surviving page
    never points at a page that enforcement removed. Files the crab does not own are
    invisible to both steps.
    """
    _reconcile_current_outputs(records, out_dir)
    before = _markdown_tokens(out_dir)
    over_by = max(0, before - total_budget) if policy != "off" else 0
    if policy != "enforce" or before <= total_budget:
        return BudgetResult(before, before, over_by, [])

    owners, priorities, base_names = _record_maps(records)
    dropped: list[dict[str, Any]] = []
    while _markdown_tokens(out_dir) > total_budget:
        candidates = _markdown_paths(out_dir)
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

    after = _markdown_tokens(out_dir)
    return BudgetResult(before, after, over_by, dropped)
