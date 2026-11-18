"""Exact receiving-context allowances for immutable prepared publication effects."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from .analysis_context import ledger_identity, receiving_context
from .attribution import GitSourceReader
from .digest import _has_occupied_gitlink
from .errors import CrabError
from .fetch.git import GitRunner
from .ledger import Ledger
from .maw import MawConfig
from .pr_publication import PreparedPullRequest
from .pr_serve import read_maw_text

_REPO_RECEIPTS = frozenset({".crab/ledger.json", ".crab/ledger.lock"})


def worktree_projection(root: Path, excluded: frozenset[str] = frozenset()) -> dict[str, str]:
    """Retain bounded per-path diff identities, excluding only proven owned inputs."""
    reader = GitSourceReader(root, deadline=time.monotonic() + 60)
    index = reader._bounded_read("ls-files", "--stage", "-z", stdout_limit=2 * 1024 * 1024)
    names = reader._bounded_read(
        "diff", "--no-renames", "--name-only", "-z", "HEAD", "--", stdout_limit=65_536
    )
    untracked = reader._bounded_read(
        "ls-files", "--others", "--exclude-standard", "-z", stdout_limit=65_536
    )
    if (
        index is None
        or names is None
        or untracked is None
        or _has_occupied_gitlink(index, root)
        or any(path and path not in excluded for path in untracked.split("\0"))
    ):
        raise CrabError("receiving worktree projection is unavailable or changed")
    paths = sorted(set(names.split("\0")) - {"", *excluded})
    if len(paths) > 128:
        raise CrabError("receiving worktree projection path budget exceeded")
    result = {}
    remaining = 512 * 1024
    for path in paths:
        diff = reader._bounded_read(
            "diff",
            "--no-renames",
            "HEAD",
            "--",
            ":(literal)" + path,
            stdout_limit=remaining,
        )
        if diff is None:
            raise CrabError("receiving worktree projection diff budget exceeded")
        remaining -= len(diff.encode("utf-8"))
        if remaining < 0:
            raise CrabError("receiving worktree projection diff budget exceeded")
        result[path] = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    return result


class PublicationContext:
    """One invocation's exact prepared inputs and Ledger.load/save byte receipts."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.ledger_baseline = ledger_identity(ledger.to_dict())
        self.inputs: dict[str, dict[str, tuple[str, int]]] = {}

    def authorize(
        self, nutrient_id: str, prepared: PreparedPullRequest, input_paths: list[str]
    ) -> None:
        files = {item.path: item.content for item in prepared.files}
        if any(path not in files for path in input_paths):
            raise CrabError("prepared publication input is not in the immutable payload")
        self.inputs[nutrient_id] = {
            path: (hashlib.sha256(files[path].encode("utf-8")).hexdigest(), len(files[path]))
            for path in input_paths
        }

    def _prepared_paths(self, root: Path) -> frozenset[str]:
        paths = set()
        for inputs in self.inputs.values():
            for path, (expected, length) in inputs.items():
                actual = read_maw_text(root, path, max_chars=length)
                if hashlib.sha256(actual.encode("utf-8")).hexdigest() != expected:
                    raise CrabError("prepared publication input changed; publication held")
                paths.add(path)
        return frozenset(paths)

    def require(self, data: dict[str, Any]) -> None:
        """Check the untouched baseline separately from exact in-run preparation."""
        from .discovery_session import _same_context, live_context
        from .multiserve import _ledger_matches_loaded_snapshot, _repo_ledger_is_own_receipt

        root = Path(data["maw_root"])
        expected = data["receiving_context"]
        baseline = data.get("novelty_scope", {}).get("worktree_projection")
        if not isinstance(baseline, dict):
            # Old snapshots remain readable, without retroactive allowance authority.
            if not _same_context(live_context(root), expected):
                raise CrabError("receiving context changed; reconcile novelty before publication")
            return
        config = MawConfig.load(root)
        git = GitRunner(root)
        if not git.is_repo_root():
            raise CrabError("receiving root changed; publication held")
        disk = Ledger.load(config.ledger_path(), maw=root.name)
        current = receiving_context(
            root, {"prey": {"sha": git.head_sha()}}, config.raw, disk.to_dict()
        )
        if any(
            current.get(key) != expected.get(key)
            for key in ("root", "sha", "selected_ref", "policy")
        ):
            raise CrabError("receiving context changed; reconcile novelty before publication")
        excluded = self._prepared_paths(root)
        configured_path = config.ledger_path()
        owned_ledger = (
            configured_path is not None
            and self.ledger.path is not None
            and configured_path.absolute() == self.ledger.path.absolute()
        )
        if owned_ledger:
            if self.ledger_baseline != expected.get(
                "ledger"
            ) or not _ledger_matches_loaded_snapshot(self.ledger):
                raise CrabError("receiving ledger changed; publication held")
            if _repo_ledger_is_own_receipt(self.ledger, root):
                lock = root / ".crab/ledger.lock"
                if lock.exists() and (
                    lock.is_symlink() or not lock.is_file() or lock.read_bytes() != b"0"
                ):
                    raise CrabError("receiving ledger lock changed; publication held")
                excluded |= _REPO_RECEIPTS
        elif current.get("ledger") != expected.get("ledger"):
            raise CrabError("receiving ledger changed; publication held")
        observed = worktree_projection(root, excluded)
        retained = {path: digest for path, digest in baseline.items() if path not in excluded}
        if observed != retained:
            raise CrabError("receiving worktree changed outside prepared inputs; publication held")
