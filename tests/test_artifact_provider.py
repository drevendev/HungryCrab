"""Synthetic-only A2 provider-record comparison controls (issue #684).

No calls to Actions, prey content or external providers. These tests do not
claim that a live API reader, policy assessor, or portable admission exists.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from hungry_crab.artifact_provider import (
    ExpectedProvider,
    MatchedProviderRecords,
    ObservedArtifact,
    ObservedJob,
    ObservedRun,
    compare_provider_records,
)
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
        hasattr(match, name)
        for name in ("trusted", "approved", "can_copy", "grant", "admitted")
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
