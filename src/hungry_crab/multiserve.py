"""Dispatch a verified aggregate selection through the existing per-source serve policy."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .analysis_context import fingerprint
from .cache import prey_paths
from .digest import worktree_fingerprint
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



def _require_current_receiving(menu: dict[str, Any], maw: Path) -> None:
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
    if (
        not is_root
        or not isinstance(worktree, str)
        or not worktree
        or worktree == "unknown"
        or context.get("root") != str(maw.resolve())
        or head != context.get("sha")
        or head != menu["maw"].get("sha")
        or selected_ref != context.get("selected_ref")
        or worktree != context.get("worktree")
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
    if options.mode in {"issue", "pr-branch"}:
        _require_current_receiving(menu, maw)
    before_publication: Callable[[], None] | None = (
        (lambda: _require_current_receiving(menu, maw))
        if options.mode in {"issue", "pr-branch"}
        else None
    )
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
