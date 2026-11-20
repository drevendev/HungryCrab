"""Fail-closed comparison of A2's out-of-band provider records (issue #684).

This module does NOT fetch provider evidence, validate a downloaded artifact, or
decide source/rights authority. The caller must independently retrieve native
run/job/artifact records and independently review the executed workflow/tool
pins; archive-controlled metadata cannot supply either side of that trust root.

A successful comparison is an identity match, NOT portable admission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import CrabError

_FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_PROVIDER_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class ExpectedProvider:
    """Owner-reviewed exact producer/request selection, never read from a ZIP."""

    repository_id: int
    workflow_id: int
    workflow_sha: str
    tool_sha: str
    event: str
    head_repository_id: int
    head_ref: str
    head_sha: str
    run_id: int
    run_attempt: int
    job_id: int
    artifact_id: int
    artifact_size: int
    artifact_digest: str


@dataclass(frozen=True)
class ObservedRun:
    """Provider run plus separately audited executed workflow/tool revisions."""

    repository_id: int
    workflow_id: int
    workflow_sha: str
    tool_sha: str
    event: str
    head_repository_id: int
    head_ref: str
    head_sha: str
    run_id: int
    run_attempt: int
    status: str
    conclusion: str


@dataclass(frozen=True)
class ObservedJob:
    """Native job identity and terminal conclusion."""

    job_id: int
    run_id: int
    run_attempt: int
    status: str
    conclusion: str


@dataclass(frozen=True)
class ObservedArtifact:
    """Native artifact identity/digest; the bundle's own manifest is irrelevant."""

    artifact_id: int
    run_id: int
    size: int
    digest: str
    expired: bool


@dataclass(frozen=True)
class MatchedProviderRecords:
    """Exact comparison receipt only; no permission, grant, or admission flag."""

    run_id: int
    run_attempt: int
    job_id: int
    artifact_id: int
    artifact_size: int
    artifact_digest: str


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _sha(value: object) -> bool:
    return isinstance(value, str) and _FULL_GIT_SHA.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return isinstance(value, str) and _PROVIDER_DIGEST.fullmatch(value) is not None


def _hold(reason: str) -> CrabError:
    return CrabError(f"REMOTE_VERIFICATION_REQUIRED: {reason}")


def compare_provider_records(
    expected: ExpectedProvider,
    run: ObservedRun,
    job: ObservedJob,
    artifact: ObservedArtifact,
) -> MatchedProviderRecords:
    """Compare independent native records to one reviewed, exact expected identity.

    Only full GitHub SHA-1 OIDs are supported here; do not silently accept a\n    Git SHA-256 OID without a separately reviewed object-format contract.\n    Never call this with records copied from archive/manifest/PR prose. Even
    matching records cannot authorize COPY, REIMPLEMENT, or publication: A2
    must still validate bytes, complete coverage, sources, rights and grants.
    """
    if (
        type(expected) is not ExpectedProvider
        or type(run) is not ObservedRun
        or type(job) is not ObservedJob
        or type(artifact) is not ObservedArtifact
    ):
        raise _hold("missing native producer record or reviewed expected identity")

    for candidate in (expected, run):
        if (
            not all(
                _positive(getattr(candidate, field))
                for field in (
                    "repository_id", "workflow_id", "head_repository_id",
                    "run_id", "run_attempt",
                )
            )
            or not _sha(candidate.workflow_sha)
            or not _sha(candidate.tool_sha)
            or not _sha(candidate.head_sha)
            or type(candidate.event) is not str
            or not candidate.event
            or type(candidate.head_ref) is not str
            or not candidate.head_ref.startswith("refs/heads/")
            or len(candidate.head_ref) <= len("refs/heads/")
            or any(ord(c) <= 32 or ord(c) == 127 for c in candidate.head_ref)
        ):
            raise _hold("incomplete or unsupported producer identity")

    if (
        not all(_positive(getattr(expected, k)) for k in ("job_id", "artifact_id"))
        or not _positive(job.job_id)
        or not _positive(job.run_id)
        or not _positive(job.run_attempt)
        or not _positive(artifact.artifact_id)
        or not _positive(artifact.run_id)
        or type(expected.artifact_size) is not int
        or type(artifact.size) is not int
        or expected.artifact_size <= 0
        or artifact.size <= 0
        or not _digest(expected.artifact_digest)
        or not _digest(artifact.digest)
        or type(artifact.expired) is not bool
    ):
        raise _hold("missing native artifact identity, provider digest or size")

    if (
        run.status != "completed"
        or run.conclusion != "success"
        or job.status != "completed"
        or job.conclusion != "success"
        or artifact.expired
    ):
        raise _hold("producer job/run incomplete, unsuccessful, or artifact expired")

    run_fields = (
        "repository_id", "workflow_id", "workflow_sha", "tool_sha",
        "event", "head_repository_id", "head_ref", "head_sha",
        "run_id", "run_attempt",
    )
    if any(getattr(run, field) != getattr(expected, field) for field in run_fields):
        raise _hold("producer, request, run or executed revision does not match")
    if (
        job.job_id != expected.job_id
        or job.run_id != run.run_id
        or job.run_attempt != run.run_attempt
        or artifact.artifact_id != expected.artifact_id
        or artifact.run_id != run.run_id
        or artifact.size != expected.artifact_size
        or artifact.digest != expected.artifact_digest
    ):
        raise _hold("native job/artifact identity or provider digest mismatch")

    return MatchedProviderRecords(
        run_id=run.run_id,
        run_attempt=run.run_attempt,
        job_id=job.job_id,
        artifact_id=artifact.artifact_id,
        artifact_size=artifact.size,
        artifact_digest=artifact.digest,
    )
