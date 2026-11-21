"""Synthetic-only A2 provider-record comparison controls (issue #684).

No calls to Actions, prey content or external providers. These tests do not
claim that a live API reader, policy assessor, or portable admission exists.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from hungry_crab.artifact_provider import (
    ExpectedProvider,
    MatchedProviderRecords,
    ObservedArtifact,
    ObservedJob,
    ObservedRun,
    check_downloaded_artifact_bytes,
    compare_provider_records,
)
from hungry_crab.artifact_zip import MAX_ARCHIVE_BYTES
from hungry_crab.errors import CrabError

_SHA = "a" * 40
_WORKFLOW = "b" * 40
_TOOL = "c" * 40
_DIGEST = "sha256:" + "d" * 64


def _records() -> tuple[ExpectedProvider, ObservedRun, ObservedJob, ObservedArtifact]:
    expected = ExpectedProvider(
        repository_id=123,
        workflow_id=456,
        workflow_sha=_WORKFLOW,
        tool_sha=_TOOL,
        event="workflow_dispatch",
        head_repository_id=123,
        head_ref="refs/heads/master",
        head_sha=_SHA,
        run_id=789,
        run_attempt=2,
        job_id=300,
        artifact_id=400,
        artifact_size=1024,
        artifact_digest=_DIGEST,
    )
    run = ObservedRun(
        repository_id=expected.repository_id,
        workflow_id=expected.workflow_id,
        workflow_sha=expected.workflow_sha,
        tool_sha=expected.tool_sha,
        event=expected.event,
        head_repository_id=expected.head_repository_id,
        head_ref=expected.head_ref,
        head_sha=expected.head_sha,
        run_id=expected.run_id,
        run_attempt=expected.run_attempt,
        status="completed",
        conclusion="success",
    )
    job = ObservedJob(
        job_id=expected.job_id,
        run_id=expected.run_id,
        run_attempt=expected.run_attempt,
        status="completed",
        conclusion="success",
    )
    artifact = ObservedArtifact(
        artifact_id=expected.artifact_id,
        run_id=expected.run_id,
        size=expected.artifact_size,
        digest=expected.artifact_digest,
        expired=False,
    )
    return expected, run, job, artifact


def test_matching_native_records_is_not_rights_authority() -> None:
    expected, run, job, artifact = _records()
    match = compare_provider_records(expected, run, job, artifact)
    assert match == MatchedProviderRecords(789, 2, 300, 400, 1024, _DIGEST)
    assert not any(
        hasattr(match, name) for name in ("trusted", "approved", "can_copy", "grant", "admitted")
    )


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("repository_id", 777),
        ("workflow_id", 777),
        ("workflow_sha", "d" * 40),
        ("tool_sha", "d" * 40),
        ("event", "pull_request_target"),
        ("head_repository_id", 777),
        ("head_ref", "refs/heads/untrusted"),
        ("head_sha", "d" * 40),
        ("run_id", 777),
        ("run_attempt", 1),
    ],
)
def test_refuse_run_which_differs_from_owner_selection(field: str, bad: object) -> None:
    expected, run, job, artifact = _records()
    run = replace(run, **{field: bad})
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, job, artifact)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("job_id", 301),
        ("run_id", 790),
        ("run_attempt", 1),
        ("status", "queued"),
        ("conclusion", "failure"),
    ],
)
def test_refuse_nonmatching_or_unsuccessful_job(field: str, bad: object) -> None:
    expected, run, job, artifact = _records()
    job = replace(job, **{field: bad})
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, job, artifact)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("artifact_id", 401),
        ("run_id", 790),
        ("size", 1025),
        ("digest", "sha256:" + "e" * 64),
        ("digest", None),
        ("expired", True),
        ("expired", None),
    ],
)
def test_refuse_wrong_expired_or_unverifiable_artifact(field: str, bad: object) -> None:
    expected, run, job, artifact = _records()
    artifact = replace(artifact, **{field: bad})
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, job, artifact)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("status", "in_progress"),
        ("status", "queued"),
        ("conclusion", "cancelled"),
        ("conclusion", "failure"),
    ],
)
def test_refuse_nonterminal_or_failed_run(field: str, bad: object) -> None:
    expected, run, job, artifact = _records()
    run = replace(run, **{field: bad})
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, job, artifact)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("repository_id", True),
        ("run_attempt", 0),
        ("head_sha", "abcdef0"),
        ("head_sha", "a" * 64),
        ("workflow_sha", "abcdef0"),
        ("head_ref", None),
        ("head_ref", "refs/heads/evil ref"),
        ("event", None),
        ("artifact_digest", "d" * 64),
        ("artifact_size", True),
        ("job_id", -1),
    ],
)
def test_refuse_malformed_owner_selection(field: str, bad: object) -> None:
    expected, run, job, artifact = _records()
    expected = replace(expected, **{field: bad})
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, job, artifact)


def test_refuse_fake_native_records() -> None:
    expected, run, job, artifact = _records()
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, job, None)  # type: ignore[arg-type]
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, None, artifact)  # type: ignore[arg-type]


def test_prior_attempt_artifact_cannot_prove_current_job_lineage() -> None:
    expected, run, job, _artifact = _records()
    # The successful second attempt reuses the run ID. Native artifact
    # metadata has no producer job/attempt: a leftover first-attempt artifact
    # can have exactly these fields, including the reviewed artifact ID.
    prior_attempt_artifact = ObservedArtifact(
        artifact_id=expected.artifact_id,
        run_id=expected.run_id,
        size=expected.artifact_size,
        digest=expected.artifact_digest,
        expired=False,
    )
    assert run.run_attempt == job.run_attempt == 2
    assert not hasattr(prior_attempt_artifact, "run_attempt")
    assert not hasattr(prior_attempt_artifact, "job_id")
    identity_match = compare_provider_records(expected, run, job, prior_attempt_artifact)
    assert identity_match.artifact_job_attempt_lineage == "UNVERIFIED"
    # The identity comparison still succeeds, but a portable admission gate
    # MUST HOLD until independent provider-backed artifact lineage exists.
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        identity_match.require_lineage_for_admission()


def test_refuse_self_consistent_mismatched_attempt_or_digest() -> None:
    expected, run, job, artifact = _records()
    # A different successful attempt with its own valid native job cannot be
    # substituted merely by updating the self-declared archive manifest.
    new_run = replace(run, run_attempt=3)
    new_job = replace(job, run_attempt=3)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, new_run, new_job, artifact)
    new_artifact = replace(artifact, digest="sha256:" + "e" * 64)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        compare_provider_records(expected, run, job, new_artifact)


def _matched_downloaded_archive() -> tuple[MatchedProviderRecords, bytes]:
    # Inert synthetic bytes deliberately need not be a valid ZIP: this gate
    # checks only the provider's outer-archive digest, not member integrity.
    archive = b"PK" + bytes(range(32))
    expected, run, job, artifact = _records()
    native_digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    expected = replace(expected, artifact_size=len(archive), artifact_digest=native_digest)
    artifact = replace(artifact, size=len(archive), digest=native_digest)
    return compare_provider_records(expected, run, job, artifact), archive


def test_matching_downloaded_zip_bytes_remains_lineage_unverified() -> None:
    matched, archive = _matched_downloaded_archive()
    assert check_downloaded_artifact_bytes(matched, archive) is None
    assert matched.artifact_job_attempt_lineage == "UNVERIFIED"
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        matched.require_lineage_for_admission()


def test_downloaded_zip_bytes_refuse_same_size_tampering_and_truncation() -> None:
    matched, archive = _matched_downloaded_archive()
    corrupted = archive[:-1] + bytes([archive[-1] ^ 1])
    assert len(corrupted) == len(archive)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        check_downloaded_artifact_bytes(matched, corrupted)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        check_downloaded_artifact_bytes(matched, archive[:-1])


def test_downloaded_zip_bytes_refuse_untrusted_receipts_and_unbounded_data() -> None:
    matched, archive = _matched_downloaded_archive()
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        check_downloaded_artifact_bytes(None, archive)  # type: ignore[arg-type]
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        check_downloaded_artifact_bytes(matched, bytearray(archive))  # type: ignore[arg-type]
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        check_downloaded_artifact_bytes(replace(matched, artifact_digest="invalid"), archive)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        check_downloaded_artifact_bytes(
            replace(matched, artifact_size=MAX_ARCHIVE_BYTES + 1), archive
        )
