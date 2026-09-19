"""Orchestrate safe pull-request serving for already implemented nutrients.

This module is intentionally below the CLI surface. It consumes strict clean-room receipts,
freezes exactly those maw files, lets the publication transaction run the secret gate and provider
reconciliation, and commits provider truth to the ledger only after a pull request URL exists.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .errors import CrabError
from .ledger import Ledger
from .nutrients import Candidate
from .pr_publication import (
    PreparedPullRequest,
    generated_files_from_handoff,
    load_cleanroom_implementation_receipt,
    publication_handoff_from_receipt,
    publish_prepared_transaction,
)

RenderPullRequest = Callable[[Candidate], tuple[str, str]]
ListMarkedPullRequests = Callable[[], Mapping[str, Mapping[str, object]]]
PublishPullRequest = Callable[[str, PreparedPullRequest], str]


@dataclass
class PullRequestServeResult:
    """Provider-facing effects and conservative skips for one bounded serve run."""

    served: list[dict[str, object]] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)


class _PullRequestLimitReachedError(Exception):
    """Internal control flow: reconciliation may continue after the creation budget is spent."""


def _read_exact_maw_text(maw_root: Path, path: str) -> str:
    try:
        root = maw_root.resolve(strict=True)
        resolved = (root / path).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise CrabError(
            "publication handoff no longer resolves safely inside the maw",
            hint=f"refusing path: {path}",
        ) from exc
    if not resolved.is_file():
        raise CrabError(
            "publication handoff no longer names a regular maw file",
            hint=f"refusing path: {path}",
        )
    try:
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CrabError(
            "publication handoff file cannot be read as UTF-8 text",
            hint=f"refusing path: {path}",
        ) from exc


def _prepare_from_receipts(
    cards: Sequence[Candidate],
    receipt_payloads: Mapping[str, str],
    maw_root: Path,
    render: RenderPullRequest,
) -> list[tuple[Candidate, PreparedPullRequest]]:
    """Build every immutable payload before provider reads or writes begin."""

    prepared: list[tuple[Candidate, PreparedPullRequest]] = []
    for card in cards:
        if card.license_mode != "REIMPLEMENT":
            if card.license_mode == "IDEAS_ONLY":
                detail = "IDEAS_ONLY nutrients may not publish a branch"
            elif card.license_mode == "COPY":
                detail = "COPY publication is blocked until attribution is wired"
            else:
                detail = f"unsupported publication mode: {card.license_mode}"
            raise CrabError("nutrient is not eligible for clean-room PR publication", hint=detail)

        payload = receipt_payloads.get(card.id)
        if payload is None:
            raise CrabError(
                "missing clean-room implementation receipt for selected nutrient",
                hint=f"missing receipt: {card.id}",
            )
        receipt = load_cleanroom_implementation_receipt(payload)
        if receipt.nutrient_id != card.id:
            raise CrabError(
                "clean-room receipt id does not match the selected nutrient",
                hint=f"selected {card.id}; receipt {receipt.nutrient_id}",
            )
        handoff = publication_handoff_from_receipt(receipt, maw_root)
        files = generated_files_from_handoff(
            card.id,
            handoff,
            lambda path: _read_exact_maw_text(maw_root, path),
        )
        title, body = render(card)
        prepared.append((card, PreparedPullRequest(title=title, body=body, files=files)))
    return prepared


def serve_pr_branches(
    cards: Sequence[Candidate],
    *,
    receipt_payloads: Mapping[str, str],
    maw_root: Path,
    ledger: Ledger,
    render: RenderPullRequest,
    list_marked_prs: ListMarkedPullRequests,
    publish: PublishPullRequest,
    prs_mode: str,
    max_prs_per_run: int,
    confirmed: bool = False,
    now: datetime | None = None,
) -> PullRequestServeResult:
    """Serve prepared REIMPLEMENT nutrients without weakening safety or idempotency.

    ``ask`` requires an explicit confirmation from the caller. All receipts, paths, hashes, and
    publication payloads are validated before the first provider read. The transaction then scans
    the full payload before reconciliation and effects. ``max_prs_per_run`` counts only newly
    created pull requests: reconciliation remains allowed after the creation budget is exhausted.
    """

    if prs_mode == "off":
        raise CrabError("serve.prs is off in .crab.yml")
    if prs_mode == "ask" and not confirmed:
        raise CrabError(
            "serve.prs requires confirmation before pull-request publication",
            hint="preview the nutrient and explicitly confirm this PR serve",
        )
    if prs_mode not in {"ask", "auto"}:
        raise CrabError("invalid serve.prs mode", hint=f"unsupported mode: {prs_mode}")
    if isinstance(max_prs_per_run, bool) or max_prs_per_run < 0:
        raise CrabError("serve.max_prs_per_run must be a non-negative integer")

    selected_ids = [card.id for card in cards]
    if len(set(selected_ids)) != len(selected_ids):
        raise CrabError("duplicate nutrient selected for pull-request publication")
    extras = sorted(set(receipt_payloads) - set(selected_ids))
    if extras:
        raise CrabError(
            "clean-room receipts include nutrients that were not selected",
            hint="unexpected receipt ids: " + ", ".join(extras),
        )

    candidates = [
        card
        for card in cards
        if not (
            (entry := ledger.entries.get(card.id)) is not None
            and entry.status in {"served", "merged", "rejected", "ignored"}
        )
    ]
    result = PullRequestServeResult()
    for card in cards:
        entry = ledger.entries.get(card.id)
        if entry is not None and entry.status in {"served", "merged", "rejected", "ignored"}:
            reason = f"ledger: {entry.status}" + (f" {entry.url}" if entry.url else "")
            result.skipped.append({"id": card.id, "reason": reason})

    prepared = _prepare_from_receipts(candidates, receipt_payloads, maw_root, render)
    created = 0

    for card, payload in prepared:
        def publish_with_budget(branch: str, exact: PreparedPullRequest) -> str:
            nonlocal created
            if created >= max_prs_per_run:
                raise _PullRequestLimitReachedError
            url = publish(branch, exact)
            created += 1
            return url

        try:
            publication = publish_prepared_transaction(
                card.id,
                payload,
                list_marked_prs,
                publish_with_budget,
            )
        except _PullRequestLimitReachedError:
            result.skipped.append({"id": card.id, "reason": "serve.max_prs_per_run reached"})
            continue

        ledger.ensure(card, now=now)
        ledger.mark(card.id, "served", url=publication.url, now=now)
        ledger.save(now=now)
        if publication.created:
            result.served.append(
                {
                    "id": card.id,
                    "url": publication.url,
                    "branch": publication.branch,
                }
            )
        else:
            result.skipped.append(
                {
                    "id": card.id,
                    "reason": f"pull request exists {publication.url}",
                }
            )

    return result
