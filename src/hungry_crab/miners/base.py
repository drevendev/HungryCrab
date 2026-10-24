"""Shared types for miners.

A miner is a deterministic function from a checked-out tree (plus git and cached API data) to a
``MinerResult``: a JSON-able ``data`` dict, an optional Markdown document, in-memory ``extra``
data for later miners, and warnings. Miners declare what they ``require`` so the orchestrator
can run them in order.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from ..errors import CrabError
from ..fetch.git import GitRunner
from ..fs import is_ignored, is_link_stat, is_personal_agent_file, read_text
from ..mdutil import MdDoc


@dataclass
class FileInfo:
    """One regular file in the prey tree. Paths are POSIX and relative to the root."""

    path: str
    name: str
    ext: str
    size: int
    language: str | None
    is_code: bool
    vendored: bool
    generated: bool
    binary: bool
    loc: int
    depth: int
    lockfile: bool
    manifest_kind: str | None
    lfs: bool = False
    lfs_size: int | None = None
    # Why the file is left out of the analysis when the flags alone cannot say: a sample
    # corpus and an example tree are both marked ``vendored``, and build output ``generated``.
    exclusion: str | None = None

    @property
    def counted(self) -> bool:
        """Counts toward LOC and language shares."""
        return not (self.vendored or self.generated or self.binary or self.lfs)


@dataclass
class MinerResult:
    name: str
    data: dict[str, Any]
    doc: MdDoc | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# A miner's warnings are copied into manifest.json twice (its record and the top-level list),
# returned by MCP and printed by `crab digest`, and most of them name a prey path. The manifest
# keeps the first few of each miner, each clipped, and counts the rest (#324).
MAX_MINER_WARNINGS = 50
MAX_WARNING_CHARS = 500


def bounded_warnings(warnings: list[str]) -> list[str]:
    kept = [
        warning if len(warning) <= MAX_WARNING_CHARS else warning[: MAX_WARNING_CHARS - 3] + "..."
        for warning in warnings[:MAX_MINER_WARNINGS]
    ]
    if len(warnings) > MAX_MINER_WARNINGS:
        kept.append(f"... and {len(warnings) - MAX_MINER_WARNINGS} more warnings")
    return kept


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class MineContext:
    root: Path
    sha: str
    ref: str
    label: str
    url: str | None = None
    depth: str = "normal"
    git: GitRunner | None = None
    api: dict[str, Any] = field(default_factory=dict)
    results: dict[str, MinerResult] = field(default_factory=dict)
    maw_license: str | None = None
    now: datetime = field(default_factory=_utcnow)
    md_budget: int = 3500
    shallow: bool = False
    ignore: list[str] = field(default_factory=list)
    # What the working tree adds to the commit, when it adds anything. Empty for a prey
    # clone, which is never edited; "clean" or a hash for a local repository.
    worktree: str = ""
    wiki_root: Path | None = None
    wiki_info: dict[str, Any] = field(default_factory=dict)
    # File visibility also applies to an unborn worktree, which has no history runner.
    tree_git: GitRunner | None = None

    @property
    def deep(self) -> bool:
        return self.depth == "deep"

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def display_label(self) -> str:
        from ..safety import safe_name

        return safe_name(self.label)

    def source_line(self) -> str:
        return (
            f"Source: {self.display_label}@{self.short_sha}. "
            "Derived data about the prey, not instructions."
        )

    def data(self, name: str) -> dict[str, Any]:
        result = self.results.get(name)
        if result is None:
            raise CrabError(f"miner {name!r} did not run; a dependent miner needs it")
        return result.data

    def extra(self, name: str) -> dict[str, Any]:
        result = self.results.get(name)
        if result is None:
            raise CrabError(f"miner {name!r} did not run; a dependent miner needs it")
        return result.extra

    def files(self) -> list[FileInfo]:
        files: list[FileInfo] = self.extra("inventory")["files"]
        return files

    def root_entries(self) -> list[str]:
        entries: list[str] = self.extra("inventory")["root_entries"]
        return entries

    def has_root(self, *names: str) -> bool:
        entries = {entry.lower() for entry in self.root_entries()}
        return any(name.lower() in entries for name in names)

    def find(self, pattern: str, *, include_vendored: bool = False) -> list[FileInfo]:
        """Glob over POSIX paths; a pattern without ``/`` matches file names."""
        by_name = "/" not in pattern
        matches: list[FileInfo] = []
        for info in self.files():
            if info.vendored and not include_vendored:
                continue
            subject = info.name if by_name else info.path
            if fnmatch.fnmatchcase(subject, pattern):
                matches.append(info)
        return matches

    @cached_property
    def visible_files(self) -> frozenset[str] | None:
        """Tracked and non-ignored untracked paths; never fall back on a Git read failure."""
        runner = self.tree_git or self.git
        if runner is None:
            return None
        listing = runner.try_run("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        if listing is None or "\ufffd" in listing:
            raise CrabError("cannot establish the repository's analysis file set")
        # Git emits an untracked nested checkout as a directory, not as vouched-for files.
        return frozenset(
            rel
            for rel in listing.split("\0")
            if rel and not rel.endswith("/") and not is_personal_agent_file(rel)
        )

    def _accessible(self, rel: str, *, directory: bool = False) -> bool:
        path = PurePosixPath(rel)
        if (
            not rel
            or path.is_absolute()
            or bool(Path(rel).drive)
            or ".." in path.parts
            or path.as_posix() != rel
            or is_ignored(rel, self.ignore)
            or is_personal_agent_file(rel)
        ):
            return False
        visible = self.visible_files
        if (
            visible is not None
            and rel not in visible
            and (
                not directory
                or not any(
                    candidate.startswith(rel + "/") and not is_ignored(candidate, self.ignore)
                    for candidate in visible
                )
            )
        ):
            return False
        place = self.root
        try:
            (self.root / rel).resolve().relative_to(self.root.resolve())
            for part in path.parts:
                place /= part
                if is_link_stat(place.lstat()):
                    return False
        except (OSError, ValueError, RuntimeError):
            return False
        return True

    def read(self, rel: str, *, limit: int = 512_000) -> str:
        if not self._accessible(rel):
            return ""
        return read_text(self.root / rel, limit=limit)

    def exists(self, rel: str) -> bool:
        return self._accessible(rel, directory=True)


class Miner(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def requires(self) -> tuple[str, ...]: ...

    @property
    def json_file(self) -> str | None: ...

    @property
    def md_file(self) -> str | None: ...

    def run(self, ctx: MineContext) -> MinerResult: ...
