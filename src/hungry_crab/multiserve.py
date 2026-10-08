"""Dispatch a verified aggregate selection through the existing per-source serve policy."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .cache import prey_paths, resolve_target
from .errors import CrabError
from .ledger import Ledger
from .maw import MawConfig
from .multifeed import load_multi
from .serve import (
    IssueClient,
    ServeOptions,
    ServeReport,
    decode_receipt_stream,
    load_cleanroom_receipts,
    select_cards,
    serve,
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
    recorded_root = menu["maw"].get("root")
    if not isinstance(recorded_root, str) or Path(recorded_root).resolve() != maw.resolve():
        raise CrabError("multi-prey bundle belongs to a different maw")
    cards, skipped = select_cards(menu, options, ledger)
    # Bound the whole invocation, not each individual source dispatch.
    cards = list({card.id: card for card in cards}.values())
    if options.mode == "pr-branch":
        for card in cards[config.serve.max_prs_per_run :]:
            skipped.append({"id": card.id, "reason": "multi-prey PR budget exceeded"})
        cards = cards[: config.serve.max_prs_per_run]
    receipts = (
        load_cleanroom_receipts(decode_receipt_stream(sys.stdin))
        if options.mode == "pr-branch"
        else None
    )
    report = ServeReport(options.mode, str(maw), skipped=skipped)
    groups: dict[str, list[str]] = {}
    for card in cards:
        groups.setdefault(str(card.trace["primary_source"]), []).append(card.id)
    for key, ids in groups.items():
        target = resolve_target(targets[key])
        repo = (
            target.path
            if target.path
            else prey_paths(target.slug, cache_root).repo
            if target.slug
            else None
        )
        part = serve(
            paths[key],
            maw,
            replace(options, ids=ids, top=None),
            config=config,
            ledger=ledger,
            client=client,
            log=log,
            receipt_payloads=receipts,
            prey_repo=repo,
        )
        report.served.extend(part.served)
        report.skipped.extend(part.skipped)
        report.previews.extend(part.previews)
        report.ledger_path = part.ledger_path
    return report
