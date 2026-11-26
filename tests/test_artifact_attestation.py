"""Synthetic verifier-output controls; these do not exercise cryptography."""

import copy
import json
from dataclasses import replace

import pytest

from hungry_crab.artifact_attestation import (
    MAX_CANDIDATES,
    MAX_VERIFIER_OUTPUT,
    ExpectedAttestation,
    select_attestation_candidate,
)
from hungry_crab.errors import CrabError


def _expected() -> ExpectedAttestation:
    return ExpectedAttestation(
        certificate={
            "issuer": "https://token.actions.githubusercontent.com",
            "sourceRepositoryIdentifier": "123",
            "sourceRepositoryURI": "https://github.com/example/producer",
            "sourceRepositoryDigest": "a" * 40,
            "sourceRepositoryRef": "refs/heads/master",
            "buildConfigURI": "https://github.com/example/producer/.github/workflows/a.yml@refs/heads/master",
            "buildConfigDigest": "a" * 40,
            "buildSignerURI": "https://github.com/example/producer/.github/workflows/a.yml@refs/heads/master",
            "buildSignerDigest": "a" * 40,
            "runnerEnvironment": "github-hosted",
            "runInvocationURI": "https://github.com/example/producer/actions/runs/42/attempts/2",
        },
        predicate_type="https://example.test/a2/v1",
        predicate={
            "artifact_id": 7,
            "artifact_size": 42,
            "request": "r",
            "pair": "p",
            "policy": "v",
        },
        zip_sha256="b" * 64,
    )


def _entry(expected: ExpectedAttestation) -> dict:
    return {
        "verificationResult": {
            "signature": {"certificate": dict(expected.certificate)},
            "verifiedTimestamps": [{"timestamp": "2026-11-25T10:00:00Z"}],
            "statement": {
                "subject": [{"name": "bundle.zip", "digest": {"sha256": expected.zip_sha256}}],
                "predicateType": expected.predicate_type,
                "predicate": dict(expected.predicate),
            },
        },
    }


def _select(entries: list, expected: ExpectedAttestation) -> int:
    return select_attestation_candidate(
        json.dumps(entries).encode(),
        expected,
        verifier_exit_code=0,
        lookup_complete=True,
    )


def test_unique_match_not_first() -> None:
    expected = _expected()
    match = _entry(expected)
    old = copy.deepcopy(match)
    old["verificationResult"]["signature"]["certificate"]["runInvocationURI"] += "-old"
    assert _select([old, match], expected) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sourceRepositoryIdentifier", "124"),
        ("runInvocationURI", "https://github.com/example/producer/actions/runs/42/attempts/1"),
        ("buildSignerDigest", "c" * 40),
        ("buildConfigDigest", "c" * 40),
        ("buildConfigURI", "https://github.com/example/producer/.github/workflows/a.yml"),
        ("buildSignerURI", "https://github.com/example/producer/.github/workflows/a.yml"),
        (
            "buildConfigURI",
            "https://github.com/example/producer/.github/workflows/a.yml@refs/heads/other",
        ),
        (
            "buildSignerURI",
            "https://github.com/example/producer/.github/workflows/a.yml@refs/heads/other",
        ),
        ("sourceRepositoryDigest", "d" * 40),
        ("runnerEnvironment", "self-hosted"),
    ],
)
def test_identity_mismatch(field: str, value: str) -> None:
    expected = _expected()
    entry = _entry(expected)
    entry["verificationResult"]["signature"]["certificate"][field] = value
    with pytest.raises(CrabError, match="no unique"):
        _select([entry], expected)


def test_mixed_entries_cannot_supply_a_match() -> None:
    expected = _expected()
    first = _entry(expected)
    second = _entry(expected)
    first["verificationResult"]["statement"]["predicate"]["artifact_id"] = 8
    second["verificationResult"]["signature"]["certificate"]["sourceRepositoryIdentifier"] = "9"
    with pytest.raises(CrabError, match="no unique"):
        _select([first, second], expected)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_id", 8),
        ("artifact_size", 43),
        ("request", "other"),
        ("pair", "other"),
        ("policy", "other"),
        ("artifact_id", 7.0),
    ],
)
def test_signed_predicate_mismatch(field: str, value: object) -> None:
    expected = _expected()
    entry = _entry(expected)
    entry["verificationResult"]["statement"]["predicate"][field] = value
    with pytest.raises(CrabError, match="no unique"):
        _select([entry], expected)


def test_duplicate_matching_entries_stop() -> None:
    expected = _expected()
    with pytest.raises(CrabError, match="no unique"):
        _select([_entry(expected), _entry(expected)], expected)


def test_missing_certificate_field_stops() -> None:
    expected = _expected()
    entry = _entry(expected)
    del entry["verificationResult"]["signature"]["certificate"]["runInvocationURI"]
    with pytest.raises(CrabError, match="unsupported verified"):
        _select([entry], expected)


def test_missing_timestamps_stop() -> None:
    expected = _expected()
    entry = _entry(expected)
    entry["verificationResult"]["verifiedTimestamps"] = []
    with pytest.raises(CrabError, match="unsupported verified"):
        _select([entry], expected)


@pytest.mark.parametrize("payload", [b"[", b"\xff", b'[{"a": 1, "a": 2}]', b"[NaN]"])
def test_malformed_output_stops(payload: bytes) -> None:
    with pytest.raises(CrabError, match="malformed"):
        select_attestation_candidate(
            payload, _expected(), verifier_exit_code=0, lookup_complete=True
        )


@pytest.mark.parametrize(("exit_code", "complete"), [(1, True), (0, False), (False, True)])
def test_unqualified_verifier_stops(exit_code: int, complete: bool) -> None:
    expected = _expected()
    with pytest.raises(CrabError, match="unqualified"):
        select_attestation_candidate(
            json.dumps([_entry(expected)]).encode(),
            expected,
            verifier_exit_code=exit_code,
            lookup_complete=complete,
        )


def test_candidate_cap_stops() -> None:
    expected = _expected()
    with pytest.raises(CrabError, match="capped"):
        _select([_entry(expected)] * MAX_CANDIDATES, expected)


def test_wrong_subject_digest_stops() -> None:
    expected = _expected()
    entry = _entry(expected)
    entry["verificationResult"]["statement"]["subject"][0]["digest"]["sha256"] = "c" * 64
    with pytest.raises(CrabError, match="no unique"):
        _select([entry], expected)


def test_oversized_verifier_output_stops() -> None:
    expected = _expected()
    payload = json.dumps([_entry(expected)]).encode()
    payload += b" " * (MAX_VERIFIER_OUTPUT + 1 - len(payload))
    with pytest.raises(CrabError, match="unqualified"):
        select_attestation_candidate(payload, expected, verifier_exit_code=0, lookup_complete=True)


@pytest.mark.parametrize("count", [0, 2])
def test_wrong_subject_cardinality_stops(count: int) -> None:
    expected = _expected()
    entry = _entry(expected)
    statement = entry["verificationResult"]["statement"]
    statement["subject"] *= count
    with pytest.raises(CrabError, match="subject cardinality"):
        _select([entry], expected)


def test_wrong_predicate_type_stops() -> None:
    expected = _expected()
    entry = _entry(expected)
    entry["verificationResult"]["statement"]["predicateType"] = "https://example.test/other/v1"
    with pytest.raises(CrabError, match="no unique"):
        _select([entry], expected)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issuer", "https://example.test"),
        ("runnerEnvironment", "self-hosted"),
        ("sourceRepositoryIdentifier", "0"),
        ("sourceRepositoryIdentifier", "0123"),
        ("sourceRepositoryIdentifier", "123x"),
        ("buildConfigDigest", ""),
        ("runInvocationURI", " "),
    ],
)
def test_invalid_expected_certificate_stops(field: str, value: str) -> None:
    expected = _expected()
    expected.certificate[field] = value
    with pytest.raises(CrabError, match="unqualified"):
        _select([_entry(expected)], expected)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_id", 0),
        ("artifact_id", True),
        ("artifact_size", -1),
        ("artifact_size", 42.0),
        ("request", ""),
        ("pair", " "),
        ("policy", 1),
    ],
)
def test_invalid_expected_predicate_stops(field: str, value: str | int | float) -> None:
    expected = _expected()
    expected.predicate[field] = value  # type: ignore[assignment]
    with pytest.raises(CrabError, match="unqualified"):
        _select([_entry(expected)], expected)


def test_expected_certificate_missing_field_stops() -> None:
    expected = _expected()
    del expected.certificate["buildSignerDigest"]
    with pytest.raises(CrabError, match="unqualified"):
        _select([_entry(expected)], expected)


def test_expected_predicate_extra_field_stops() -> None:
    expected = _expected()
    expected.predicate["extra"] = "unreviewed"
    with pytest.raises(CrabError, match="unqualified"):
        _select([_entry(expected)], expected)


@pytest.mark.parametrize("digest", ["", "b" * 63, "B" * 64, "z" * 64])
def test_invalid_expected_digest_stops(digest: str) -> None:
    expected = replace(_expected(), zip_sha256=digest)
    with pytest.raises(CrabError, match="unqualified"):
        _select([_entry(expected)], expected)


@pytest.mark.parametrize("predicate_type", ["", " "])
def test_invalid_expected_predicate_type_stops(predicate_type: str) -> None:
    expected = replace(_expected(), predicate_type=predicate_type)
    with pytest.raises(CrabError, match="unqualified"):
        _select([_entry(expected)], expected)
