"""Policy and ledger orchestration for clean-room pull-request serving.

The publication modules own payload safety, provider reconciliation and git/GitHub effects. This
module owns the layer around them that ``crab serve --as pr-branch`` needs: configuration gates,
receipt preflight, per-run creation limits and committing provider receipts into the maw ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .errors import CrabError
from .ledger import Ledger
from .maw import MawConfig
from .nutrients import Candidate
from .pr_publication import PullRequestPublication, load_cleanroom_implementation_receipt

PrPublisher = Callable[[Candidate, str, bool], PullRequestPublication | None]
_TERMINAL_STATUSES = frozenset({"served", "merged", "rejected", "ignored"})


@dataclass
class PullRequestServeReport:
    """One bounded PR-serving result before the CLI folds it into ``ServeReport``."""

    served: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    ledger_path: str | None = None


def _preflight_receipts(
    cards: Sequence[Candidate], receipts: Mapping[str, str], ledger: Ledger
) -> tuple[list[tuple[Candidate, str]], list[dict[str, Any]]]:
    """Validate every actionable card before the first provider read or effect."""

    planned: list[tuple[Candidate, str]] = []
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
        if card.license_mode != "REIMPLEMENT":
            raise CrabError(
                f"pull-request serving for license mode {card.license_mode} is not wired yet",
                hint=(
                    "REIMPLEMENT uses the clean-room receipt path; COPY must wait for its "
                    "attribution/materialization path instead of bypassing the license verdict"
                ),
            )
        payload = receipts.get(card.id)
        if payload is None:
            raise CrabError(
                "missing clean-room implementation receipt for pull-request serving",
                hint=f"no receipt was supplied for {card.id}",
            )
        receipt = load_cleanroom_implementation_receipt(payload)
        if receipt.nutrient_id != card.id:
            raise CrabError(
                "clean-room receipt nutrient does not match the selected nutrient",
                hint=f"expected {card.id}, got {receipt.nutrient_id}",
            )
        planned.append((card, payload))
    return planned, skipped


def serve_cleanroom_pull_requests(
    cards: Sequence[Candidate],
    receipts: Mapping[str, str],
    *,
    config: MawConfig,
    ledger: Ledger,
    explicit_selection: bool,
    publisher: PrPublisher,
    now: datetime | None = None,
) -> PullRequestServeReport:
    """Apply PR serving policy around the already guarded clean-room publisher.

    ``serve.prs: ask`` requires an explicit nutrient selection; automatic ``--top`` style
    selection is reserved for ``auto``. Receipt and license-mode validation for every actionable
    card completes before ``publisher`` is called, so a later invalid card cannot leave earlier
    provider effects behind.

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

    planned, skipped = _preflight_receipts(cards, receipts, ledger)
    report = PullRequestServeReport(skipped=skipped)
    report.ledger_path = str(ledger.path) if ledger.path else None
    creation_limit = max(0, config.serve.max_prs_per_run)
    created = 0

    for card, payload in planned:
        allow_create = created < creation_limit
        publication = publisher(card, payload, allow_create)
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
