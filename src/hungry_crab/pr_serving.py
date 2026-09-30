"""Policy and ledger orchestration for clean-room pull-request serving.

The publication modules own payload safety, provider reconciliation and git/GitHub effects. This
module owns the layer around them that ``crab serve --as pr-branch`` needs: configuration gates,
batch preparation, per-run creation limits and committing provider receipts into the maw ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
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


def _prepare_publications(
    cards: Sequence[Candidate],
    receipts: Mapping[str, str],
    ledger: Ledger,
    preparer: PrPreparer,
) -> tuple[list[tuple[Candidate, PreparedPullRequest]], list[dict[str, Any]]]:
    """Freeze every actionable card before the first provider read or effect.

    A card that has no pull-request path is skipped with its reason rather than failing the
    batch: a terminal ledger entry, a ``serve_as`` other than ``pr`` (an ``idea`` or an
    ``issue`` card is never published as a pull request, whoever selected it), a license mode
    without a publication path, or a missing receipt. ``--top`` therefore serves what it can.
    A receipt that is present but malformed, or of the wrong kind for the card's license mode,
    still fails closed: it is input, not selection.
    """

    planned: list[tuple[Candidate, PreparedPullRequest]] = []
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
        receipt_id = (
            load_materialization_receipt(payload).nutrient_id
            if copying
            else load_cleanroom_implementation_receipt(payload).nutrient_id
        )
        if receipt_id != card.id:
            raise CrabError(
                f"{label} receipt nutrient does not match the selected nutrient",
                hint=f"expected {card.id}, got {receipt_id}",
            )
        planned.append((card, preparer(card, payload)))
    return planned, skipped


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
) -> PullRequestServeReport:
    """Apply PR serving policy around the already guarded clean-room publisher.

    ``serve.prs: ask`` requires an explicit nutrient selection; automatic ``--top`` style
    selection is reserved for ``auto``. Every actionable card is fully prepared into an immutable
    publication payload before ``publisher`` is called for any card. Receipt structure, nutrient
    identity, maw path containment, UTF-8 readability, file identity and drift detection therefore
    all complete before the first provider reconciliation/read/effect.

    ``publisher`` receives ``allow_create=False`` after the configured creation budget is spent.
    It must still perform the safe scan + provider reconciliation and return an existing PR when
    one is found, but it must return ``None`` instead of creating a new provider effect otherwise.
    Consequently reconciled PRs do not consume ``max_prs_per_run``.
    """

    if config.serve.prs == "off":
        raise CrabError("serve.prs is off in .crab.yml", hint="set serve.prs to ask or auto")
    if config.serve.prs == "ask" and not explicit_selection:
        raise CrabError(
            "serve.prs is ask in .crab.yml",
            hint="review the menu and pass explicit nutrient ids before publishing pull requests",
        )

    planned, skipped = _prepare_publications(cards, receipts, ledger, preparer)
    report = PullRequestServeReport(skipped=skipped)
    report.ledger_path = str(ledger.path) if ledger.path else None
    creation_limit = max(0, config.serve.max_prs_per_run)
    created = 0

    for card, prepared in planned:
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
        if publication.created:
            created += 1

    return report
