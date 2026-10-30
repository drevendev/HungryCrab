"""Dispatch a verified aggregate selection through the existing per-source serve policy."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .analysis_context import fingerprint
from .cache import prey_paths
from .digest import _has_occupied_gitlink, worktree_fingerprint
from .errors import CrabError
from .fetch.git import GitRunner
from .ledger import Ledger
from .maw import MawConfig
from .multifeed import load_multi
from .pr_serving import PullRequestServePlan
from .serve import (
    IssueClient,
    ServeOptions,
    ServeReport,
    decode_receipt_stream,
    load_cleanroom_receipts,
    publish_serve_plans,
    select_cards,
    serve,
)


_REPO_LEDGER_RELATIVE = ".crab/ledger.json"
_REPO_LOCK_RELATIVE = ".crab/ledger.lock"


def _receipt_free_worktree(git: GitRunner, maw: Path) -> str:
    """Identity of the receiving worktree minus the one owned receipt file.

    This cannot ignore a changed source, an untracked non-receipt file or a newly
    occupied gitlink. A repo ledger is checked separately against its writer's
    exact persisted byte hash, so an external ledger edit is not whitelisted.
    """
    index = git.try_run("ls-files", "--stage", "-z")
    if index is None or _has_occupied_gitlink(index, maw):
        return "unknown"
    diff = git.try_run(
        "diff", "HEAD", "--", ".",
        f":(exclude){_REPO_LEDGER_RELATIVE}",
        f":(exclude){_REPO_LOCK_RELATIVE}",
    )
    untracked = git.try_run("ls-files", "--others", "--exclude-standard", "-z")
    if diff is None or untracked is None:
        return "unknown"
    names = {name for name in untracked.split("\0") if name}
    if names - {_REPO_LEDGER_RELATIVE, _REPO_LOCK_RELATIVE}:
        return "unknown"
    lock = maw / _REPO_LOCK_RELATIVE
    try:
        if lock.exists() and (
            lock.is_symlink() or not lock.is_file() or lock.read_bytes() != b"0"
        ):
            return "unknown"
    except OSError:
        return "unknown"
    return "clean" if not diff else hashlib.sha1(diff.encode("utf-8", "replace")).hexdigest()[:12]


def _repo_ledger_is_own_receipt(ledger: Ledger, maw: Path) -> bool:
    """Only actual Ledger.save()/load() byte receipts may change the repo ledger."""
    path = ledger.path
    expected = maw / _REPO_LEDGER_RELATIVE
    if (
        path is None
        or path.absolute() != expected.absolute()
        or path.is_symlink()
        or path.parent.is_symlink()
    ):
        return False
    try:
        if path.is_file():
            observed = hashlib.sha256(path.read_bytes()).digest()
        elif path.exists():
            return False
        else:
            observed = None
    except OSError:
        return False
    return observed == ledger._snapshot


def _require_current_receiving(
    menu: dict[str, Any],
    maw: Path,
    *,
    receipt_ledger: Ledger | None = None,
    baseline_without_receipt: str | None = None,
) -> None:
    """Recheck live receiving checkout and policy before a multi-prey publish effect.

    A recorded, internally consistent bundle is not evidence that the maw still
    has the same HEAD, selected ref, tracked bytes or effective .crab.yml.
    This check is not a cross-process transaction or a ledger vote lock.
    """
    context = menu.get("receiving_context")
    if not isinstance(context, dict) or menu.get("receiving_context_status") != "observed":
        raise CrabError("multi-prey receiving context is not verified; regenerate the bundle")
    git = GitRunner(maw)
    try:
        is_root = git.is_repo_root()
        head = git.head_sha() if is_root else None
        selected_ref = git.current_branch() if is_root else None
        worktree = worktree_fingerprint(git, maw) if is_root else None
        current_policy = fingerprint(MawConfig.load(maw).raw)
    except (CrabError, OSError) as exc:
        raise CrabError("cannot verify current receiving checkout; regenerate the bundle") from exc
    receipt_matches = (
        receipt_ledger is not None
        and _repo_ledger_is_own_receipt(receipt_ledger, maw)
    )
    same_worktree = worktree == context.get("worktree")
    if receipt_ledger is not None and baseline_without_receipt is not None:
        # A stored receipt may change only the repo ledger; it cannot silence an
        # outside decision/edit or change the receiving source/policy.
        same_worktree = (
            receipt_matches
            and _receipt_free_worktree(git, maw) == baseline_without_receipt
            and baseline_without_receipt != "unknown"
        )
    if (
        not is_root
        or not isinstance(worktree, str)
        or not worktree
        or (worktree == "unknown" and receipt_ledger is None)
        or context.get("root") != str(maw.resolve())
        or head != context.get("sha")
        or head != menu["maw"].get("sha")
        or selected_ref != context.get("selected_ref")
        or not same_worktree
        or current_policy != context.get("policy")
    ):
        raise CrabError(
            "multi-prey receiving checkout or policy changed after comparison; "
            "regenerate the bundle before publication"
        )


def serve_many(
    bundle: Path,
    maw: Path,
    options: ServeOptions,
    *,
    config: MawConfig,
    ledger: Ledger,
    client: IssueClient | None = None,
    cache_root: Path | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> ServeReport:
    menu, paths, targets = load_multi(bundle)
    if (
        options.mode in {"issue", "pr-branch"}
        and menu.get("receiving_context_status") != "observed"
    ):
        raise CrabError(
            "multi-prey publication requires verified receiving analysis context; "
            "regenerate this bundle before serving"
        )
    recorded_root = menu["maw"].get("root")
    if not isinstance(recorded_root, str) or Path(recorded_root).resolve() != maw.resolve():
        raise CrabError("multi-prey bundle belongs to a different maw")
    before_publication: Callable[[], None] | None = None
    if options.mode in {"issue", "pr-branch"}:
        _require_current_receiving(menu, maw)
        repo_receipt = (
            config.ledger == "repo"
            and ledger.path is not None
            and ledger.path.absolute() == (maw / _REPO_LEDGER_RELATIVE).absolute()
        )
        if repo_receipt:
            # A pre-existing changed ledger is NOT an in-run receipt.
            if not _repo_ledger_is_own_receipt(ledger, maw):
                raise CrabError("receiving ledger changed since load; refuse publication")
            baseline = _receipt_free_worktree(GitRunner(maw), maw)
            if baseline == "unknown":
                raise CrabError("cannot verify receiving worktree without repo receipts")
            _require_current_receiving(menu, maw)

            def recheck_with_receipt() -> None:
                _require_current_receiving(
                    menu,
                    maw,
                    receipt_ledger=ledger,
                    baseline_without_receipt=baseline,
                )

            before_publication = recheck_with_receipt
        else:
            before_publication = lambda: _require_current_receiving(menu, maw)
    cards, skipped = select_cards(menu, options, ledger)
    # Bound the whole invocation, not each individual source dispatch.
    cards = list({card.id: card for card in cards}.values())
    receipts = (
        load_cleanroom_receipts(decode_receipt_stream(sys.stdin))
        if options.mode == "pr-branch"
        else None
    )
    report = ServeReport(options.mode, str(maw), skipped=skipped)
    groups: dict[str, list[str]] = {}
    plans: list[PullRequestServePlan] | None = [] if options.mode == "pr-branch" else None
    for card in cards:
        groups.setdefault(str(card.trace["primary_source"]), []).append(card.id)
    for key, ids in groups.items():
        target = targets[key]
        repo = (
            target.path
            if target.path is not None
            else prey_paths(target.slug, cache_root).repo
            if target.slug is not None
            else None
        )
        part = serve(
            paths[key],
            maw,
            replace(options, ids=ids, top=None, explicit_ids=options.owner_selected_ids),
            config=config,
            ledger=ledger,
            client=client,
            log=log,
            receipt_payloads=receipts,
            prey_repo=repo,
            publication_plans=plans,
            before_publication=before_publication,
        )
        report.served.extend(part.served)
        report.skipped.extend(part.skipped)
        report.previews.extend(part.previews)
        report.ledger_path = part.ledger_path
    if plans is not None:
        served, pr_skipped = publish_serve_plans(plans, config=config, ledger=ledger, log=log)
        report.served.extend(served)
        report.skipped.extend(pr_skipped)
        report.ledger_path = str(ledger.path) if ledger.path else None
    return report
