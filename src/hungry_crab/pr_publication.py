"""Prepared pull-request publication guarded before any external effect.

The future ``serve --as pr-branch`` path has a strict transaction boundary: first build the
complete publication payload in memory, then scan every byte the crab is about to publish, then
reconcile provider truth, and only then allow branch/git/GitHub effects. Keeping that ordering in
one helper makes crash recovery and secret blocking explicit instead of relying on every caller to
remember the sequence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from .errors import CrabError
from .publication_safety import format_publication_findings, scan_publication_bundle

_ResultT = TypeVar("_ResultT")
_BRANCH_SAFE_RE = re.compile(r"[^a-z0-9._-]+")
_BRANCH_REPEAT_RE = re.compile(r"[.-]{2,}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_HANDOFF_VERSION = 1


@dataclass(frozen=True)
class GeneratedFile:
    """One exact generated file that would be written to the maw branch."""

    path: str
    content: str


@dataclass(frozen=True)
class HandoffFile:
    """One exact maw-relative text file declared by the nutrient producer."""

    path: str
    sha256: str


@dataclass(frozen=True)
class PublicationHandoff:
    """Machine-readable nutrient-bound declaration of files eligible for publication."""

    nutrient_id: str
    files: tuple[HandoffFile, ...]


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


class _DuplicateObjectMemberError(ValueError):
    """Raised when the JSON wire payload contains an ambiguous object member."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


def _handoff_error(detail: str) -> CrabError:
    return CrabError(
        "invalid publication handoff; refusing to infer files from the maw", hint=detail
    )


def _reject_duplicate_object_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise _DuplicateObjectMemberError(key)
        parsed[key] = value
    return parsed


def _validate_handoff_path(path: str) -> None:
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(not part or part in {".", ".."} for part in parts)
        or ":" in parts[0]
    ):
        raise _handoff_error("file paths must be canonical maw-relative POSIX paths")


def load_publication_handoff(payload: str) -> PublicationHandoff:
    """Parse the exact nutrient/file declaration emitted before PR preparation.

    The schema is intentionally small and strict so producer and publisher cannot disagree about
    which files belong to a nutrient. Unknown or duplicate object members, duplicate paths, path
    traversal, and malformed digests fail closed instead of falling back to working-tree discovery.
    """

    try:
        raw: object = json.loads(payload, object_pairs_hook=_reject_duplicate_object_members)
    except _DuplicateObjectMemberError as exc:
        raise _handoff_error(f"duplicate JSON object member: {exc.key}") from exc
    except (json.JSONDecodeError, TypeError) as exc:
        raise _handoff_error("handoff must be valid JSON") from exc
    if not isinstance(raw, dict):
        raise _handoff_error("handoff root must be an object")
    if set(raw) != {"version", "nutrient_id", "files"}:
        raise _handoff_error("handoff must contain only version, nutrient_id, and files")

    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != _HANDOFF_VERSION:
        raise _handoff_error(f"unsupported handoff version: {version!r}")

    nutrient_id = raw["nutrient_id"]
    if not isinstance(nutrient_id, str) or not nutrient_id.startswith("crab:"):
        raise _handoff_error("nutrient_id must be a crab nutrient id")

    raw_files = raw["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise _handoff_error("handoff must declare at least one generated file")

    files: list[HandoffFile] = []
    seen_paths: set[str] = set()
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != {"path", "sha256"}:
            raise _handoff_error("each file must contain only path and sha256")
        path = raw_file["path"]
        digest = raw_file["sha256"]
        if not isinstance(path, str):
            raise _handoff_error("file path must be a string")
        _validate_handoff_path(path)
        if path in seen_paths:
            raise _handoff_error(f"duplicate handoff path: {path}")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise _handoff_error(f"invalid sha256 for {path}")
        seen_paths.add(path)
        files.append(HandoffFile(path=path, sha256=digest))

    return PublicationHandoff(nutrient_id=nutrient_id, files=tuple(files))


def generated_files_from_handoff(
    nutrient_id: str,
    handoff: PublicationHandoff,
    read_maw_text: Callable[[str], str],
) -> tuple[GeneratedFile, ...]:
    """Freeze exactly the declared maw files after validating nutrient and content identity.

    The publisher consumes returned bytes from ``GeneratedFile.content`` later; it must not reread
    the working tree during branch/write/push effects. This prevents unrelated dirty files or a
    post-prepare edit from changing the publication payload.
    """

    if handoff.nutrient_id != nutrient_id:
        raise _handoff_error("handoff nutrient_id does not match the publication nutrient")

    generated: list[GeneratedFile] = []
    for declared in handoff.files:
        try:
            content = read_maw_text(declared.path)
        except (KeyError, OSError) as exc:
            raise _handoff_error(f"declared file is missing: {declared.path}") from exc
        if not isinstance(content, str):
            raise _handoff_error(f"declared file is not UTF-8 text: {declared.path}")
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual != declared.sha256:
            raise _handoff_error(f"declared file changed after handoff: {declared.path}")
        generated.append(GeneratedFile(path=declared.path, content=content))

    return tuple(generated)


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
