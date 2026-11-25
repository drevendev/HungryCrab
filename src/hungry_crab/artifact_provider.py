"""Fail-closed comparison of A2's out-of-band provider records (issue #684).

This module compares provider evidence and retrieves exact-attempt job membership.
It checks downloaded archive bytes against the matched provider digest, not the
archive's contents or source/rights authority. The caller must independently
retrieve the remaining native records and review the executed workflow/tool
pins; archive-controlled metadata cannot supply either side of that trust root.

A successful comparison is an identity match, NOT portable admission.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from .artifact_zip import MAX_ARCHIVE_BYTES
from .cache import Slug
from .errors import CrabError
from .fetch.github import GitHubClient

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
    """Native artifact identity/digest, not producer job/attempt lineage.

    GitHub's artifact record identifies a run and its repository/head scope,
    not the job or attempt which
    uploaded it. Never infer those fields from the untrusted ZIP/manifest.
    """

    artifact_id: int
    run_id: int
    repository_id: int
    head_repository_id: int
    head_branch: str
    head_sha: str
    size: int
    digest: str
    expired: bool


@dataclass(frozen=True)
class MatchedProviderRecords:
    """Exact identity receipt only; it cannot establish artifact job/attempt lineage.

    Native run/job success plus an artifact run ID does not prove the selected
    job/attempt produced those bytes. In particular, reruns keep the run ID.
    The constant UNVERIFIED state prevents confusing a match with A2 admission.
    """

    run_id: int
    run_attempt: int
    job_id: int
    artifact_id: int
    artifact_size: int
    artifact_digest: str
    artifact_job_attempt_lineage: Literal["UNVERIFIED"] = field(default="UNVERIFIED", init=False)

    def require_lineage_for_admission(self) -> None:
        """Fail closed until an independent provider-backed lineage witness exists."""
        raise _hold("artifact-to-job/attempt lineage not independently verified")


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

    Only full GitHub SHA-1 OIDs are supported here; do not silently accept a
    Git SHA-256 OID without a separately reviewed object-format contract.
    Never call this with records copied from archive/manifest/PR prose. Even
    matching records cannot authorize COPY, REIMPLEMENT, or publication: A2
    must still validate bytes, complete coverage, sources, rights and grants.
    An artifact run ID is not producer-job/attempt evidence. Any future A2
    admission must independently correlate that lineage or return typed HOLD.
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
                    "repository_id",
                    "workflow_id",
                    "head_repository_id",
                    "run_id",
                    "run_attempt",
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
        or not _positive(artifact.repository_id)
        or not _positive(artifact.head_repository_id)
        or type(artifact.head_branch) is not str
        or not artifact.head_branch
        or not _sha(artifact.head_sha)
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
        "repository_id",
        "workflow_id",
        "workflow_sha",
        "tool_sha",
        "event",
        "head_repository_id",
        "head_ref",
        "head_sha",
        "run_id",
        "run_attempt",
    )
    if any(getattr(run, field) != getattr(expected, field) for field in run_fields):
        raise _hold("producer, request, run or executed revision does not match")
    if (
        job.job_id != expected.job_id
        or job.run_id != run.run_id
        or job.run_attempt != run.run_attempt
        or artifact.artifact_id != expected.artifact_id
        or artifact.run_id != run.run_id
        or artifact.repository_id != run.repository_id
        or artifact.head_repository_id != run.head_repository_id
        or artifact.head_branch != run.head_ref.removeprefix("refs/heads/")
        or artifact.head_sha != run.head_sha
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


def check_downloaded_artifact_bytes(
    matched: MatchedProviderRecords, downloaded_archive: bytes
) -> None:
    """Check the outer ZIP bytes against independently selected provider metadata.

    This establishes only size and digest consistency with the matched native
    artifact record. It does NOT establish the uploading job/attempt, archive
    safety, origin, rights, or permission for portable admission. In particular,
    this function does not remove the require_lineage_for_admission() HOLD.
    The caller must obtain raw archive bytes without extracting or executing
    any member, and must not derive the expected digest from those bytes.
    """
    if type(matched) is not MatchedProviderRecords or type(downloaded_archive) is not bytes:
        raise _hold("missing matched provider receipt or raw downloaded artifact")
    if (
        not _positive(matched.artifact_size)
        or matched.artifact_size > MAX_ARCHIVE_BYTES
        or not _digest(matched.artifact_digest)
        or len(downloaded_archive) != matched.artifact_size
    ):
        raise _hold("downloaded artifact size or provider digest is invalid")
    if "sha256:" + hashlib.sha256(downloaded_archive).hexdigest() != matched.artifact_digest:
        raise _hold("downloaded artifact SHA-256 does not match provider digest")


def fetch_attempt_job(
    expected: ExpectedProvider,
    repository: str,
    *,
    client: GitHubClient,
    require_singleton: bool = False,
) -> ObservedJob:
    """Read the selected job from its native attempt-scoped REST endpoint.

    Repository and expected identity must come from reviewed caller policy,
    never an artifact. Use a fresh, authenticated read-only client. A run-wide
    job listing is not equivalent: reruns reuse run IDs. Refuse incomplete
    listings rather than guess from job names or timestamps. This is only job
    membership evidence, not proof that the job uploaded the artifact.
    Singleton mode additionally rejects every attempt with more than one job.
    It does not validate the reviewed workflow topology, upload step, signature,
    or producer receipt and cannot establish lineage or admit the archive.
    """
    if (
        type(expected) is not ExpectedProvider
        or not _positive(expected.repository_id)
        or not _positive(expected.run_id)
        or not _positive(expected.run_attempt)
        or not _positive(expected.job_id)
        or not _sha(expected.head_sha)
    ):
        raise _hold("invalid expected attempt/job identity")
    if client.cache_dir is not None or (not client.gh and not client.token):
        raise _hold("native attempt job read requires an authenticated uncached client")
    try:
        slug = Slug.parse(repository)
        if slug.host != "github.com" or str(slug) != repository:
            raise _hold("unsupported native producer repository")
        repo = client.repo(slug)
        if not _positive(repo.get("id")) or repo["id"] != expected.repository_id:
            raise _hold("native producer repository ID mismatch")
        listing = client.get(
            f"repos/{slug}/actions/runs/{expected.run_id}/"
            f"attempts/{expected.run_attempt}/jobs?per_page=100&page=1"
        )
    except (CrabError, OSError, ValueError, RecursionError) as exc:
        raise _hold("native attempt-scoped job read unavailable") from exc

    if not isinstance(listing, dict):
        raise _hold("invalid native attempt job listing")
    jobs = listing.get("jobs")
    total = listing.get("total_count")
    if not isinstance(jobs, list) or not _positive(total) or total != len(jobs) or len(jobs) > 100:
        raise _hold("incomplete or unsupported native attempt job listing")
    if type(require_singleton) is not bool:
        raise _hold("invalid singleton job requirement")
    if require_singleton and total != 1:
        raise _hold("native attempt is not a singleton job")
    seen: set[int] = set()
    selected = None
    for job in jobs:
        if (
            not isinstance(job, dict)
            or not _positive(job.get("id"))
            or job["id"] in seen
            or not _positive(job.get("run_id"))
            or job["run_id"] != expected.run_id
            or job.get("head_sha") != expected.head_sha
            or (
                "run_attempt" in job
                and (
                    not _positive(job["run_attempt"]) or job["run_attempt"] != expected.run_attempt
                )
            )
        ):
            raise _hold("conflicting native attempt job identity")
        seen.add(job["id"])
        if job["id"] == expected.job_id:
            selected = job
    if (
        selected is None
        or selected.get("status") != "completed"
        or selected.get("conclusion") != "success"
    ):
        raise _hold("selected job absent or unsuccessful in native attempt")
    return ObservedJob(
        job_id=expected.job_id,
        run_id=expected.run_id,
        run_attempt=expected.run_attempt,
        status="completed",
        conclusion="success",
    )
