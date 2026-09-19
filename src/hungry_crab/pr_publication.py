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
from pathlib import Path
from types import BuiltinMethodType
from typing import TypeVar

from .errors import CrabError
from .publication_safety import format_publication_findings, scan_publication_bundle

_ResultT = TypeVar("_ResultT")
_BRANCH_SAFE_RE = re.compile(r"[^a-z0-9._-]+")
_BRANCH_REPEAT_RE = re.compile(r"[.-]{2,}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_HANDOFF_VERSION = 1
_CLEANROOM_RECEIPT_VERSION = 1
_CLEANROOM_TRACE = "implemented from a specification, without access to the prey source"


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
class CleanroomImplementationReceipt:
    """Exact paths declared by the isolated clean-room producer after implementation."""

    nutrient_id: str
    changed_paths: tuple[str, ...]
    summary: str
    checks: tuple[str, ...]


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


def _receipt_error(detail: str) -> CrabError:
    return CrabError(
        "invalid clean-room implementation receipt; refusing to infer files from the maw",
        hint=detail,
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


def _is_canonical_maw_path(path: str) -> bool:
    parts = path.split("/")
    return not (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(not part or part in {".", ".."} for part in parts)
        or ":" in parts[0]
    )


def _validate_handoff_path(path: str) -> None:
    if not _is_canonical_maw_path(path):
        raise _handoff_error("file paths must be canonical maw-relative POSIX paths")


def _validate_receipt_path(path: str) -> None:
    if not _is_canonical_maw_path(path):
        raise _receipt_error("changed paths must be canonical maw-relative POSIX paths")


def load_cleanroom_implementation_receipt(payload: str) -> CleanroomImplementationReceipt:
    """Parse the isolated implementer's exact machine-readable changed-path declaration.

    The receipt is the only producer-side source of path membership. The trusted caller hashes the
    current content of exactly these paths into a publication handoff; it never discovers files by
    diffing whatever happens to be dirty in the maw.
    """

    try:
        raw: object = json.loads(payload, object_pairs_hook=_reject_duplicate_object_members)
    except _DuplicateObjectMemberError as exc:
        raise _receipt_error(f"duplicate JSON object member: {exc.key}") from exc
    except (json.JSONDecodeError, TypeError) as exc:
        raise _receipt_error("receipt must be valid JSON") from exc
    if not isinstance(raw, dict):
        raise _receipt_error("receipt root must be an object")
    if set(raw) != {"version", "nutrient_id", "changed_paths", "summary", "checks"}:
        raise _receipt_error(
            "receipt must contain only version, nutrient_id, changed_paths, summary, and checks"
        )

    version = raw["version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != _CLEANROOM_RECEIPT_VERSION
    ):
        raise _receipt_error(f"unsupported receipt version: {version!r}")

    nutrient_id = raw["nutrient_id"]
    if not isinstance(nutrient_id, str) or not nutrient_id.startswith("crab:"):
        raise _receipt_error("nutrient_id must be a crab nutrient id")

    raw_paths = raw["changed_paths"]
    if not isinstance(raw_paths, list) or not raw_paths:
        raise _receipt_error("receipt must declare at least one changed path")
    changed_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in raw_paths:
        if not isinstance(path, str):
            raise _receipt_error("changed path must be a string")
        _validate_receipt_path(path)
        if path in seen_paths:
            raise _receipt_error(f"duplicate changed path: {path}")
        seen_paths.add(path)
        changed_paths.append(path)

    summary = raw["summary"]
    if not isinstance(summary, str) or _CLEANROOM_TRACE not in summary:
        raise _receipt_error("summary must contain the clean-room trace sentence")

    raw_checks = raw["checks"]
    if (
        not isinstance(raw_checks, list)
        or not raw_checks
        or any(not isinstance(check, str) or not check.strip() for check in raw_checks)
    ):
        raise _receipt_error("checks must be a non-empty list of command strings")

    return CleanroomImplementationReceipt(
        nutrient_id=nutrient_id,
        changed_paths=tuple(changed_paths),
        summary=summary,
        checks=tuple(raw_checks),
    )


def _mapping_from_legacy_reader(source: object) -> Mapping[object, object] | None:
    """Recover only a bound in-memory mapping reader used by unit tests.

    Filesystem callbacks are deliberately not accepted here: they cannot prove that a lexical maw
    path resolves inside the actual maw root. A mapping has no filesystem aliasing semantics, so it
    remains a useful deterministic seam for existing pure unit tests.
    """

    if (
        isinstance(source, BuiltinMethodType)
        and source.__name__ == "__getitem__"
        and isinstance(source.__self__, Mapping)
    ):
        return source.__self__
    return None


def _read_receipt_maw_text(maw: object, path: str) -> str:
    mapping: Mapping[object, object] | None
    if isinstance(maw, Mapping):
        mapping = maw
    else:
        mapping = _mapping_from_legacy_reader(maw)
    if mapping is not None:
        try:
            content = mapping[path]
        except KeyError as exc:
            raise _receipt_error(f"declared changed file is missing: {path}") from exc
        if not isinstance(content, str):
            raise _receipt_error(f"declared changed file is not UTF-8 text: {path}")
        return content

    if not isinstance(maw, Path):
        raise _receipt_error(
            "maw source must be a root Path so resolved containment can be proven before reading"
        )

    try:
        root = maw.resolve(strict=True)
    except OSError as exc:
        raise _receipt_error("maw root cannot be resolved safely") from exc

    try:
        resolved = (root / path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise _receipt_error(f"declared changed file is missing: {path}") from exc
    except OSError as exc:
        raise _receipt_error(f"declared changed file cannot be resolved safely: {path}") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise _receipt_error(f"declared changed file resolves outside the maw: {path}") from exc

    if not resolved.is_file():
        raise _receipt_error(f"declared changed path is not a regular file: {path}")
    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise _receipt_error(f"declared changed file is not UTF-8 text: {path}") from exc
    except OSError as exc:
        raise _receipt_error(f"declared changed file cannot be read safely: {path}") from exc


def publication_handoff_from_receipt(
    receipt: CleanroomImplementationReceipt,
    maw: Path | Mapping[str, str],
) -> PublicationHandoff:
    """Hash exact receipt files after proving filesystem containment under the maw root.

    Production callers pass the actual maw root. Resolution happens before reading and the resolved
    target itself is read, so symlinks, junctions, and other aliases cannot redirect publication
    bytes outside the maw. In-memory mappings are supported only as an alias-free unit-test seam.
    """

    files: list[HandoffFile] = []
    for path in receipt.changed_paths:
        content = _read_receipt_maw_text(maw, path)
        files.append(
            HandoffFile(path=path, sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
        )
    return PublicationHandoff(nutrient_id=receipt.nutrient_id, files=tuple(files))


def dump_publication_handoff(handoff: PublicationHandoff) -> str:
    """Serialize a handoff canonically so it can be persisted or passed across processes."""

    return (
        json.dumps(
            {
                "version": _HANDOFF_VERSION,
                "nutrient_id": handoff.nutrient_id,
                "files": [
                    {"path": declared.path, "sha256": declared.sha256} for declared in handoff.files
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


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
