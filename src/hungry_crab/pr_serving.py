"""Policy and ledger orchestration for clean-room pull-request serving.

The publication modules own payload safety, provider reconciliation and git/GitHub effects. This
module owns the layer around them that ``crab serve --as pr-branch`` needs: configuration gates,
batch preparation, per-run creation limits and committing provider receipts into the maw ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .attribution import (
    COPY_MODES,
    MATERIALIZATION_KIND,
    load_materialization_receipt,
    receipt_kind,
)
from .errors import CrabError
from .ledger import Ledger
from .maw import MawConfig
from .nutrients import Candidate
from .pr_publication import (
    PreparedPullRequest,
    PullRequestPublication,
    check_prepared_publication,
    load_cleanroom_implementation_receipt,
)

PrPreparer = Callable[[Candidate, str], PreparedPullRequest]
PrPublisher = Callable[[Candidate, PreparedPullRequest, bool], PullRequestPublication | None]
_TERMINAL_STATUSES = frozenset({"served", "merged", "rejected", "ignored"})


@dataclass
class PullRequestServeReport:
    """One bounded PR-serving result before the CLI folds it into ``ServeReport``."""

    served: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    ledger_path: str | None = None


@dataclass(frozen=True)
class PullRequestServePlan:
    """A source's frozen, scanned publications and its deferred provider adapter."""

    publications: tuple[tuple[Candidate, PreparedPullRequest, tuple[str, ...]], ...]
    skipped: tuple[dict[str, Any], ...]
    publisher: PrPublisher


def _prepare_publications(
    cards: Sequence[Candidate],
    receipts: Mapping[str, str],
    ledger: Ledger,
    preparer: PrPreparer,
    *,
    copy_needs_ids: bool = False,
    policy_root: Path | None = None,
) -> tuple[list[tuple[Candidate, PreparedPullRequest, list[str]]], list[dict[str, Any]]]:
    """Freeze every actionable card before the first provider read or effect.

    A card that has no pull-request path is skipped with its reason rather than failing the
    batch: a terminal ledger entry, a ``serve_as`` other than ``pr`` (an ``idea`` or an
    ``issue`` card is never published as a pull request, whoever selected it), a license mode
    without a publication path, or a missing receipt. ``--top`` therefore serves what it can.
    A receipt that is present but malformed, or of the wrong kind for the card's license mode,
    still fails closed: it is input, not selection.
    """

    planned: list[tuple[Candidate, PreparedPullRequest, list[str]]] = []
    skipped: list[dict[str, Any]] = []
    for card in cards:
        entry = ledger.entries.get(card.id)
        if entry is not None and entry.status in _TERMINAL_STATUSES:
            skipped.append(
                {
                    "id": card.id,
                    "reason": f"ledger: {entry.status}" + (f" {entry.url}" if entry.url else ""),
                }
            )
            continue
        if card.serve_as != "pr":
            skipped.append({"id": card.id, "reason": f"serve_as: {card.serve_as}"})
            continue
        copying = card.license_mode in COPY_MODES
        if card.license_mode != "REIMPLEMENT" and not copying:
            skipped.append(
                {
                    "id": card.id,
                    "reason": f"license mode {card.license_mode} has no pull-request path",
                }
            )
            continue
        if copying and copy_needs_ids:
            # The verdict asked a person to look (same owner over a copyleft licence, bypassed
            # checks, a nested licence): ranked selection is not a person looking.
            skipped.append(
                {
                    "id": card.id,
                    "reason": "the licence verdict asks for human review; name it with --ids",
                }
            )
            continue
        expected_kind = MATERIALIZATION_KIND if copying else "cleanroom"
        label = "materialization" if copying else "clean-room"
        payload = receipts.get(card.id)
        if payload is None:
            skipped.append({"id": card.id, "reason": f"no {label} receipt"})
            continue
        if receipt_kind(payload) != expected_kind:
            raise CrabError(
                f"the receipt for {card.id} is not a {label} receipt",
                hint=(
                    "COPY and COPY_FILE nutrients take a materialization receipt; "
                    "REIMPLEMENT takes the clean-room implementer's receipt"
                ),
            )
        working_tree_paths: list[str] = []
        if copying:
            receipt = load_materialization_receipt(payload)
            receipt_id = receipt.nutrient_id
            working_tree_paths = [taken.maw_path for taken in receipt.taken]
        else:
            receipt_id = load_cleanroom_implementation_receipt(payload).nutrient_id
        if receipt_id != card.id:
            raise CrabError(
                f"{label} receipt nutrient does not match the selected nutrient",
                hint=f"expected {card.id}, got {receipt_id}",
            )
        prepared = replace(preparer(card, payload), policy_root=policy_root)
        check_prepared_publication(prepared)
        planned.append((card, prepared, working_tree_paths))
    return planned, skipped


def prepare_pull_request_plan(
    cards: Sequence[Candidate],
    receipts: Mapping[str, str],
    *,
    config: MawConfig,
    ledger: Ledger,
    explicit_selection: bool,
    preparer: PrPreparer,
    publisher: PrPublisher,
    human_review: bool = False,
) -> PullRequestServePlan:
    """Validate policy, receipts, containment and all public fields without provider access.

    ``ask`` needs owner-selected IDs; automatic selection is reserved for ``auto``. Skips
    preserve the normal single-source policy. Present but invalid receipts, source verification
    failures and secret findings reject preparation of the whole invocation.
    """

    if config.serve.prs == "off":
        raise CrabError("serve.prs is off in .crab.yml", hint="set serve.prs to ask or auto")
    if config.serve.prs == "ask" and not explicit_selection:
        raise CrabError(
            "serve.prs is ask in .crab.yml",
            hint="review the menu and pass explicit nutrient ids before publishing pull requests",
        )

    planned, skipped = _prepare_publications(
        cards,
        receipts,
        ledger,
        preparer,
        copy_needs_ids=human_review and not explicit_selection,
        policy_root=config.root,
    )
    return PullRequestServePlan(
        tuple((card, prepared, tuple(paths)) for card, prepared, paths in planned),
        tuple(skipped),
        publisher,
    )


def publish_pull_request_plans(
    plans: Sequence[PullRequestServePlan],
    *,
    config: MawConfig,
    ledger: Ledger,
    now: datetime | None = None,
) -> PullRequestServeReport:
    """Publish already checked sources under one creation budget; reconciliation is free.

    After the limit is spent, publishers still reconcile with ``allow_create=False``. They
    return an existing provider receipt or None, never create a new PR. Every successful
    receipt is saved immediately so a later provider failure can be reconciled on retry.
    """
    report = PullRequestServeReport(skipped=[item for plan in plans for item in plan.skipped])
    report.ledger_path = str(ledger.path) if ledger.path else None
    creation_limit = max(0, config.serve.max_prs_per_run)
    created = 0

    publications = (
        (card, prepared, paths, plan.publisher)
        for plan in plans
        for card, prepared, paths in plan.publications
    )
    for card, prepared, working_tree_paths, publisher in publications:
        allow_create = created < creation_limit
        publication = publisher(card, prepared, allow_create)
        if publication is None:
            if allow_create:
                raise CrabError(
                    "pull-request publisher returned no provider receipt",
                    hint=f"publication state is unknown for {card.id}; reconcile before retrying",
                )
            report.skipped.append(
                {
                    "id": card.id,
                    "reason": f"serve.max_prs_per_run reached ({creation_limit})",
                }
            )
            continue

        ledger.ensure(card, now=now)
        ledger.mark(card.id, "served", url=publication.url, now=now)
        ledger.save(now=now)
        report.served.append(
            {
                "id": card.id,
                "title": card.title,
                "url": publication.url,
                "branch": publication.branch,
                "created": publication.created,
            }
        )
        if working_tree_paths:
            report.served[-1]["working_tree_paths"] = list(working_tree_paths)
        if publication.created:
            created += 1

    return report


def serve_cleanroom_pull_requests(
    cards: Sequence[Candidate],
    receipts: Mapping[str, str],
    *,
    config: MawConfig,
    ledger: Ledger,
    explicit_selection: bool,
    preparer: PrPreparer,
    publisher: PrPublisher,
    now: datetime | None = None,
    human_review: bool = False,
) -> PullRequestServeReport:
    """Prepare the whole batch before effects, then count only newly created PRs."""
    plan = prepare_pull_request_plan(
        cards,
        receipts,
        config=config,
        ledger=ledger,
        explicit_selection=explicit_selection,
        preparer=preparer,
        publisher=publisher,
        human_review=human_review,
    )
    return publish_pull_request_plans([plan], config=config, ledger=ledger, now=now)
