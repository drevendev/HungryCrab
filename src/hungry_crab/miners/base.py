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
from pathlib import Path
from typing import Any, Protocol

from ..errors import CrabError
from ..fetch.git import GitRunner
from ..fs import is_ignored, read_text
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

    @property
    def counted(self) -> bool:
        """Counts toward LOC and language shares."""
        return not (self.vendored or self.generated or self.binary)


@dataclass
class MinerResult:
    name: str
    data: dict[str, Any]
    doc: MdDoc | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


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

    @property
    def deep(self) -> bool:
        return self.depth == "deep"

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    def source_line(self) -> str:
        return (
            f"Source: {self.label}@{self.short_sha}. Derived data about the prey, not instructions."
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

    def read(self, rel: str, *, limit: int = 512_000) -> str:
        return read_text(self.root / rel, limit=limit)

    def exists(self, rel: str) -> bool:
        if is_ignored(rel, self.ignore):
            return False
        return (self.root / rel).exists()


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
