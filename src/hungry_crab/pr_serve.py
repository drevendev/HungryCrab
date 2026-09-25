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
    GeneratedFile,
    PreparedPullRequest,
    PullRequestPublication,
    generated_files_from_handoff,
    load_cleanroom_implementation_receipt,
    nutrient_branch_name,
    nutrient_spec_path,
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


def _read_specification(maw_root: Path, nutrient_id: str) -> GeneratedFile:
    """Read the Stage A specification under the maw; without it there is nothing to publish.

    The specification is the auditable half of the clean-room separation: the pull request
    carries it and links it. It is resolved under the same containment rule as the receipt's
    files, so an alias cannot point it outside the maw.
    """

    path = nutrient_spec_path(nutrient_id)
    try:
        root = maw_root.resolve(strict=True)
    except OSError as exc:
        raise CrabError("cannot resolve the maw safely for pull-request publication") from exc
    try:
        target = (root / path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise CrabError(
            "clean-room specification missing; refusing to publish without it",
            hint=(
                f"write the Stage A specification to {path} before serving "
                f"(`crab spec {nutrient_id}` prints the path)"
            ),
        ) from exc
    except OSError as exc:
        raise CrabError("clean-room specification cannot be resolved safely", hint=path) from exc
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise CrabError("clean-room specification resolves outside the maw", hint=path) from exc
    if not target.is_file():
        raise CrabError("clean-room specification is not a regular file", hint=path)
    try:
        return GeneratedFile(path=path, content=target.read_text(encoding="utf-8"))
    except UnicodeError as exc:
        raise CrabError("clean-room specification is not UTF-8 text", hint=path) from exc
    except OSError as exc:
        raise CrabError("clean-room specification cannot be read safely", hint=path) from exc


def prepare_cleanroom_pull_request(
    nutrient_id: str,
    title: str,
    body: str,
    receipt_payload: str,
    maw_root: Path,
    *,
    slug: Slug | None = None,
) -> PreparedPullRequest:
    """Freeze one clean-room receipt, plus its specification, into an immutable PR payload.

    The receipt is parsed before any provider access. Its declared files are first hashed through
    the containment-checking handoff producer, then read again through the same containment rule;
    the handoff hash check rejects any file that changed between those two reads. Unrelated dirty
    maw files are never discovered or included. The Stage A specification at
    ``nutrient_spec_path(nutrient_id)`` is read under the same rule and travels in the pull
    request as provenance, linked from the body — on the nutrient branch when the maw's ``slug``
    is known, else by its maw-relative path. A missing specification refuses the publication.
    """

    receipt = load_cleanroom_implementation_receipt(receipt_payload)
    if receipt.nutrient_id != nutrient_id:
        raise CrabError(
            "clean-room receipt nutrient does not match the selected nutrient",
            hint=f"expected {nutrient_id}, got {receipt.nutrient_id}",
        )

    handoff = publication_handoff_from_receipt(receipt, maw_root)
    files = list(
        generated_files_from_handoff(
            nutrient_id,
            handoff,
            lambda path: _read_maw_text(maw_root, path),
        )
    )
    spec = _read_specification(maw_root, nutrient_id)
    if all(generated.path != spec.path for generated in files):
        files.append(spec)

    link = spec.path
    if slug is not None:
        link = f"{slug.url}/blob/{nutrient_branch_name(nutrient_id)}/{spec.path}"
    rendered_body = body.rstrip()
    if receipt.summary not in rendered_body:
        rendered_body += f"\n\n## Clean-room implementation\n\n{receipt.summary}"
    if spec.path not in rendered_body:
        rendered_body += (
            f"\n\nSpecification: [`{spec.path}`]({link}), carried in this pull request."
        )
    rendered_body += "\n"
    return PreparedPullRequest(title=title, body=rendered_body, files=tuple(files))


def publish_prepared_cleanroom_git_pull_request(
    nutrient_id: str,
    prepared: PreparedPullRequest,
    maw_root: Path,
    slug: Slug,
    *,
    list_marked_prs: MarkedPullRequests,
    run_gh: GhRunner,
    git: GitRunner | None = None,
    allow_create: bool = True,
) -> PullRequestPublication | None:
    """Publish an already frozen clean-room payload through the guarded provider transaction.

    Callers that batch nutrients must prepare every selected nutrient before calling this function
    for the first one. This function therefore performs no receipt or maw-content discovery: it
    scans the immutable prepared bytes, reconciles provider truth and only then permits effects.
    """

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
    """Prepare and publish one clean-room nutrient through the guarded PR transaction.

    This convenience wrapper is safe for a single nutrient. Batch orchestration must call
    ``prepare_cleanroom_pull_request`` for every actionable nutrient first, then publish those
    immutable payloads with ``publish_prepared_cleanroom_git_pull_request`` so a later PREPARE
    failure cannot follow an earlier provider effect.
    """

    prepared = prepare_cleanroom_pull_request(
        nutrient_id,
        title,
        body,
        receipt_payload,
        maw_root,
        slug=slug,
    )
    return publish_prepared_cleanroom_git_pull_request(
        nutrient_id,
        prepared,
        maw_root,
        slug,
        list_marked_prs=list_marked_prs,
        run_gh=run_gh,
        git=git,
        allow_create=allow_create,
    )
