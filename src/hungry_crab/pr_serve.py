"""Bridge clean-room implementation receipts into the guarded PR publication transaction.

The clean-room agent changes the maw and returns an exact nutrient-scoped receipt. This module is
the trusted bridge from that receipt to the already guarded publication transaction: freeze the
receipt-declared files, preserve their content identity, scan the complete PR payload, reconcile
provider truth, and only then invoke the concrete git/GitHub effect adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from .cache import Slug
from .errors import CrabError
from .fetch.git import GitRunner
from .pr_effects import publish_git_pull_request
from .pr_publication import (
    PreparedPullRequest,
    PullRequestPublication,
    generated_files_from_handoff,
    load_cleanroom_implementation_receipt,
    publication_handoff_from_receipt,
    publish_prepared_transaction,
)

MarkedPullRequests = Callable[[], Mapping[str, Mapping[str, object]]]
GhRunner = Callable[..., str]


class _CreationDeferredError(Exception):
    """Internal signal: reconciliation found no PR, but this run has no creation budget left."""


def _read_maw_text(maw_root: Path, path: str) -> str:
    """Read one handoff path only after proving its resolved target stays inside the maw."""

    try:
        root = maw_root.resolve(strict=True)
    except OSError as exc:
        raise CrabError("cannot resolve the maw safely for pull-request publication") from exc
    try:
        target = (root / path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise CrabError(
            "publication handoff changed after clean-room implementation",
            hint=f"declared file is missing: {path}",
        ) from exc
    except OSError as exc:
        raise CrabError(
            "publication handoff changed after clean-room implementation",
            hint=f"declared file cannot be resolved safely: {path}",
        ) from exc
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise CrabError(
            "publication handoff changed after clean-room implementation",
            hint=f"declared file resolves outside the maw: {path}",
        ) from exc
    if not target.is_file():
        raise CrabError(
            "publication handoff changed after clean-room implementation",
            hint=f"declared path is not a regular file: {path}",
        )
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise CrabError(
            "publication handoff changed after clean-room implementation",
            hint=f"declared file is not UTF-8 text: {path}",
        ) from exc
    except OSError as exc:
        raise CrabError(
            "publication handoff changed after clean-room implementation",
            hint=f"declared file cannot be read safely: {path}",
        ) from exc


def prepare_cleanroom_pull_request(
    nutrient_id: str,
    title: str,
    body: str,
    receipt_payload: str,
    maw_root: Path,
) -> PreparedPullRequest:
    """Freeze exactly one clean-room receipt into an immutable PR payload.

    The receipt is parsed before any provider access. Its declared files are first hashed through
    the containment-checking handoff producer, then read again through the same containment rule;
    the handoff hash check rejects any file that changed between those two reads. Unrelated dirty
    maw files are never discovered or included.
    """

    receipt = load_cleanroom_implementation_receipt(receipt_payload)
    if receipt.nutrient_id != nutrient_id:
        raise CrabError(
            "clean-room receipt nutrient does not match the selected nutrient",
            hint=f"expected {nutrient_id}, got {receipt.nutrient_id}",
        )

    handoff = publication_handoff_from_receipt(receipt, maw_root)
    files = generated_files_from_handoff(
        nutrient_id,
        handoff,
        lambda path: _read_maw_text(maw_root, path),
    )

    rendered_body = body.rstrip()
    if receipt.summary not in rendered_body:
        rendered_body += f"\n\n## Clean-room implementation\n\n{receipt.summary}"
    rendered_body += "\n"
    return PreparedPullRequest(title=title, body=rendered_body, files=files)


def publish_cleanroom_git_pull_request(
    nutrient_id: str,
    title: str,
    body: str,
    receipt_payload: str,
    maw_root: Path,
    slug: Slug,
    *,
    list_marked_prs: MarkedPullRequests,
    run_gh: GhRunner,
    git: GitRunner | None = None,
    allow_create: bool = True,
) -> PullRequestPublication | None:
    """Publish one clean-room nutrient through the guarded deterministic PR transaction.

    PREPARE happens entirely before provider reads. ``publish_prepared_transaction`` then scans
    every generated file plus PR title/body and reconciles a marker-bearing PR. When
    ``allow_create`` is false, reconciliation still runs but the effect callback stops before any
    git/GitHub publication effect and this function returns ``None`` if no existing PR was found.
    """

    prepared = prepare_cleanroom_pull_request(
        nutrient_id,
        title,
        body,
        receipt_payload,
        maw_root,
    )

    def publish_effect(branch: str, payload: PreparedPullRequest) -> str:
        if not allow_create:
            raise _CreationDeferredError
        return publish_git_pull_request(
            slug,
            maw_root,
            branch,
            payload,
            run_gh=run_gh,
            git=git,
        )

    try:
        return publish_prepared_transaction(
            nutrient_id,
            prepared,
            list_marked_prs,
            publish_effect,
        )
    except _CreationDeferredError:
        return None
