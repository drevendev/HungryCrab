"""Bounded session-local analysis snapshots; no persistent shallow-cache policy changes."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .analysis_context import fingerprint, ledger_identity
from .artifact_read import read_bytes, read_object
from .cache import Target
from .compare import CompareResult
from .digest import DigestOptions, DigestResult, incomplete_miners, prepare_context
from .digest_integrity import digest_integrity_errors
from .errors import CrabError
from .exploration import _digest_identity, _tool_identity
from .ledger import Ledger
from .maw import MawConfig
from .miners.base import MineContext
from .miners.inventory import describe_coverage
from .typeutil import as_dict

Analysis = CompareResult | DigestResult


def _source(ctx: MineContext) -> dict[str, Any] | None:
    if ctx.worktree == "unknown" or ctx.git is None:
        return None
    refs = ctx.git.try_run("for-each-ref", "--format=%(refname) %(objectname)")
    shallow_path = ctx.git.try_run("rev-parse", "--git-path", "shallow")
    if refs is None or shallow_path is None:
        return None
    boundary = ctx.root / shallow_path.strip()
    return {
        "root": str(ctx.root.resolve()),
        "sha": ctx.sha,
        "ref": ctx.ref,
        "selected_ref": ctx.git.current_branch(),
        "worktree": ctx.worktree,
        "shallow": ctx.shallow,
        "boundary": fingerprint(read_bytes(boundary, 1024 * 1024).hex())
        if boundary.exists()
        else None,
        "refs": fingerprint(refs),
        "api": fingerprint(ctx.api),
        "wiki": ctx.wiki_info,
        "ignore": ctx.ignore,
        "depth": ctx.depth,
    }


def input_identity(target: Target, options: DigestOptions, maw: Path | None = None) -> str | None:
    """Recheck live inputs without running a miner or silently refreshing a cached clone."""
    prey_ctx, _ = prepare_context(target, options)
    source = _source(prey_ctx)
    if source is None:
        return None
    inputs: dict[str, Any] = {
        "prey": source,
        "tool": _tool_identity(),
        "as_of_day": datetime.now(UTC).date().isoformat(),
    }
    if maw is not None:
        config = MawConfig.load(maw)
        ctx, _ = prepare_context(
            Target(path=maw), replace(options, ignore=config.ignore, maw_license=config.license)
        )
        receiving = _source(ctx)
        if receiving is None:
            return None
        ledger = Ledger.load(config.ledger_path(options.cache_root)).to_dict()
        inputs.update(maw=receiving, policy=config.raw, ledger=ledger_identity(ledger))
    return fingerprint(inputs)


def evidence_identity(result: Analysis) -> str:
    directories = (
        [
            ("prey", result.prey_dir, result.prey.manifest),
            ("maw", result.maw_dir, result.maw.manifest),
        ]
        if isinstance(result, CompareResult)
        else [("prey", result.out_dir, result.manifest)]
    )
    identities = {}
    for side, directory, expected in directories:
        if directory is None:
            raise CrabError(f"MCP {side} evidence has no digest directory")
        manifest = read_object(directory / "manifest.json")
        if manifest != expected:
            raise CrabError(f"MCP {side} digest manifest changed after analysis")
        failed = incomplete_miners(manifest)
        errors = digest_integrity_errors(directory, manifest)
        coverage = as_dict(manifest.get("coverage"))
        if failed or errors or coverage.get("healthy") is not True:
            detail = "; ".join([*failed, *errors, describe_coverage(coverage)])
            raise CrabError(
                f"MCP refuses {side} digest evidence: {detail}",
                hint="repair that side; visibility loss may require a separate deep digest",
            )
        identities[side] = _digest_identity(directory, manifest)
    return fingerprint(identities)


@dataclass
class Snapshot:
    inputs: str
    evidence: str
    created: float
    value: Analysis


class SnapshotStore:
    """A server owns this store under its existing request lock; clients receive copies."""

    def __init__(self, *, capacity: int = 8, ttl: float = 60) -> None:
        self.capacity = capacity
        self.ttl = ttl
        self.entries: OrderedDict[tuple[str, ...], Snapshot] = OrderedDict()
        self.counters = {"analyses": 0, "hits": 0, "validations": 0}

    def get(
        self,
        key: tuple[str, ...],
        inputs: Callable[[], str | None],
        factory: Callable[[], Analysis],
        *,
        continuation: bool,
    ) -> tuple[Analysis, str, bool]:
        identity = inputs()
        saved = self.entries.get(key)
        healthy = False
        if (
            saved is not None
            and saved.inputs == identity
            and time.monotonic() - saved.created < self.ttl
        ):
            self.counters["validations"] += 1
            try:
                healthy = (
                    evidence_identity(saved.value) == saved.evidence
                    and inputs() == saved.inputs
                    and time.monotonic() - saved.created < self.ttl
                )
            except CrabError:
                healthy = False
        if healthy:
            assert saved is not None
            self.entries.move_to_end(key)
            self.counters["hits"] += 1
            return saved.value, fingerprint([saved.inputs, saved.evidence]), True
        if continuation:
            raise CrabError(
                "MCP analysis snapshot changed, expired or is unavailable",
                hint="restart the menu at offset 0 or list digest pages again without page",
            )
        self.entries.pop(key, None)
        value = factory()
        self.counters["analyses"] += 1
        evidence = evidence_identity(value)
        if inputs() != identity:
            raise CrabError("MCP inputs changed during analysis", hint="retry from the first page")
        if (
            isinstance(value, CompareResult)
            and len(json.dumps(value.menu).encode()) > 8 * 1024 * 1024
        ):
            raise CrabError("MCP menu exceeds its snapshot byte budget")
        if identity is not None:
            self.entries[key] = Snapshot(identity, evidence, time.monotonic(), value)
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
        return value, fingerprint([identity, evidence]), False
