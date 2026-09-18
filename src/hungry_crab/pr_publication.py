"""Prepared pull-request publication guarded before any external effect.

The future ``serve --as pr-branch`` path has a strict transaction boundary: first build the
complete publication payload in memory, then scan every byte the crab is about to publish, then
reconcile provider truth, and only then allow branch/git/GitHub effects. Keeping that ordering in
one helper makes crash recovery and secret blocking explicit instead of relying on every caller to
remember the sequence.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from .errors import CrabError
from .publication_safety import format_publication_findings, scan_publication_bundle

_ResultT = TypeVar("_ResultT")
_BRANCH_SAFE_RE = re.compile(r"[^a-z0-9._-]+")
_BRANCH_REPEAT_RE = re.compile(r"[.-]{2,}")


@dataclass(frozen=True)
class GeneratedFile:
    """One exact generated file that would be written to the maw branch."""

    path: str
    content: str


@dataclass(frozen=True)
class PreparedPullRequest:
    """The complete immutable payload that must exist before publication starts."""

    title: str
    body: str
    files: tuple[GeneratedFile, ...]


@dataclass(frozen=True)
class PullRequestPublication:
    """Provider receipt for one nutrient's deterministic pull-request publication."""

    branch: str
    url: str
    created: bool


def publication_items(prepared: PreparedPullRequest) -> Iterable[tuple[str, str]]:
    """Yield every generated field that can become public, with safe logical locations."""

    for generated in prepared.files:
        yield generated.path, generated.content
    yield "PR_TITLE", prepared.title
    yield "PR_BODY", prepared.body


def nutrient_branch_name(nutrient_id: str) -> str:
    """Return a stable, collision-resistant branch name for one nutrient id."""

    if not nutrient_id.startswith("crab:"):
        raise CrabError("pull-request publication requires a crab nutrient id")
    readable = _BRANCH_SAFE_RE.sub("-", nutrient_id.removeprefix("crab:").lower())
    readable = _BRANCH_REPEAT_RE.sub("-", readable).strip("-.") or "nutrient"
    digest = hashlib.sha256(nutrient_id.encode("utf-8")).hexdigest()[:8]
    return f"crab/{readable[:48]}-{digest}"


def _required_marker(nutrient_id: str) -> str:
    return f"<!-- {nutrient_id} -->"


def publish_prepared_pull_request(
    prepared: PreparedPullRequest,
    publish: Callable[[PreparedPullRequest], _ResultT],
) -> _ResultT:
    """Scan the whole prepared payload before invoking the first publication effect.

    ``publish`` owns all branch, working-tree, push and pull-request effects. Callers must do no
    such work before entering this function. A finding raises before the callback is invoked and
    reports only path/line/rule metadata, never the matched value.
    """

    findings = scan_publication_bundle(publication_items(prepared))
    if findings:
        raise CrabError(
            "refusing to publish a pull request containing a possible secret",
            hint=format_publication_findings(findings),
        )
    return publish(prepared)


def publish_prepared_transaction(
    nutrient_id: str,
    prepared: PreparedPullRequest,
    list_marked_prs: Callable[[], Mapping[str, Mapping[str, object]]],
    publish: Callable[[str, PreparedPullRequest], str],
) -> PullRequestPublication:
    """Reconcile provider truth before creating one nutrient's branch and pull request.

    The body marker and the branch name are deterministic identities. The complete payload is
    scanned before even the provider reconciliation read. If a marker-bearing pull request already
    exists, its URL is returned and ``publish`` is not called; this is the crash window where PR
    creation succeeded but the local ledger did not save. Otherwise ``publish`` receives the
    deterministic branch name and owns branch/write/push/PR effects. An empty or malformed
    reconciliation result fails closed instead of risking a duplicate pull request.
    """

    branch = nutrient_branch_name(nutrient_id)
    marker = _required_marker(nutrient_id)
    if not prepared.body.lstrip().startswith(marker):
        raise CrabError("prepared pull request body must open with its nutrient marker")

    def reconcile_then_publish(payload: PreparedPullRequest) -> PullRequestPublication:
        existing = list_marked_prs().get(nutrient_id)
        if existing is not None:
            url = existing.get("url")
            if not isinstance(url, str) or not url.strip():
                raise CrabError(
                    "matching pull request has no usable URL; refusing to create a duplicate"
                )
            return PullRequestPublication(branch=branch, url=url, created=False)

        url = publish(branch, payload)
        if not url.strip():
            raise CrabError(
                "pull request publication returned no URL; reconcile provider state before retrying"
            )
        return PullRequestPublication(branch=branch, url=url, created=True)

    return publish_prepared_pull_request(prepared, reconcile_then_publish)
