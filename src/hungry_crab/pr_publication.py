"""Prepared pull-request publication guarded before any external effect.

The future ``serve --as pr-branch`` path has a strict transaction boundary: first build the
complete publication payload in memory, then scan every byte the crab is about to publish, and
only then allow branch/git/GitHub effects. Keeping that ordering in one helper makes a secret hit
provably effect-free instead of relying on every caller to remember the sequence.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from .errors import CrabError
from .publication_safety import format_publication_findings, scan_publication_bundle

_ResultT = TypeVar("_ResultT")


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


def publication_items(prepared: PreparedPullRequest) -> Iterable[tuple[str, str]]:
    """Yield every generated field that can become public, with safe logical locations."""

    for generated in prepared.files:
        yield generated.path, generated.content
    yield "PR_TITLE", prepared.title
    yield "PR_BODY", prepared.body


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
