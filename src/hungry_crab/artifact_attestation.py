"""Select a matching candidate from trusted verifier output (A2, #684).

This is an offline policy comparison, NOT a signature verifier or admission
receipt. A future reviewed caller must run the pinned verifier over the exact
bounded ZIP and independently establish complete lookup and producer topology.
Never feed archive-provided output or expected identity into this adapter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .errors import CrabError

MAX_VERIFIER_OUTPUT = 4 * 1024 * 1024
MAX_CANDIDATES = 100
_CERT_FIELDS = (
    "issuer",
    "sourceRepositoryIdentifier",
    "sourceRepositoryURI",
    "sourceRepositoryDigest",
    "sourceRepositoryRef",
    "buildConfigURI",
    "buildConfigDigest",
    "buildSignerURI",
    "buildSignerDigest",
    "runnerEnvironment",
    "runInvocationURI",
)
_PREDICATE_FIELDS = ("artifact_id", "artifact_size", "request", "pair", "policy")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ExpectedAttestation:
    """Reviewed external policy, never populated from a candidate or archive."""

    certificate: dict[str, str]
    predicate_type: str
    predicate: dict[str, str | int]
    zip_sha256: str


def _hold(reason: str) -> CrabError:
    return CrabError(f"REMOTE_VERIFICATION_REQUIRED: {reason}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _hold("unsupported verifier output object")
    return value


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _valid_expected(expected: ExpectedAttestation) -> bool:
    cert = expected.certificate
    predicate = expected.predicate
    return (
        set(cert) == set(_CERT_FIELDS)
        and all(type(v) is str and bool(v.strip()) for v in cert.values())
        and cert["issuer"] == "https://token.actions.githubusercontent.com"
        and cert["runnerEnvironment"] == "github-hosted"
        and re.fullmatch(r"[1-9][0-9]*", cert["sourceRepositoryIdentifier"]) is not None
        and type(expected.predicate_type) is str
        and bool(expected.predicate_type.strip())
        and set(predicate) == set(_PREDICATE_FIELDS)
        and all(_positive(predicate[k]) for k in ("artifact_id", "artifact_size"))
        and all(
            isinstance(predicate[k], str) and bool(str(predicate[k]).strip())
            for k in ("request", "pair", "policy")
        )
        and type(expected.zip_sha256) is str
        and _SHA256.fullmatch(expected.zip_sha256) is not None
    )


def select_attestation_candidate(
    output: bytes,
    expected: ExpectedAttestation,
    *,
    verifier_exit_code: int,
    lookup_complete: bool,
) -> int:
    """Return the sole matching array index, not proof of signature or lineage.

    The exit code and completeness must come from the independent caller, not
    from the JSON. The cap is fail-closed even when completeness is asserted.
    An unrelated, well-shaped historical candidate may precede the match.
    """
    if (
        type(expected) is not ExpectedAttestation
        or not _valid_expected(expected)
        or type(output) is not bytes
        or not 0 < len(output) <= MAX_VERIFIER_OUTPUT
        or type(verifier_exit_code) is not int
        or verifier_exit_code != 0
        or lookup_complete is not True
    ):
        raise _hold("invalid expected policy or unqualified verifier result")
    try:
        candidates = json.loads(
            output.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError) as exc:
        raise _hold("malformed verifier JSON") from exc
    if not isinstance(candidates, list) or not 0 < len(candidates) < MAX_CANDIDATES:
        raise _hold("empty, capped or unsupported verifier result")
    matches: list[int] = []
    for index, candidate in enumerate(candidates):
        verification = _object(_object(candidate).get("verificationResult"))
        signature = _object(verification.get("signature"))
        certificate = _object(signature.get("certificate"))
        timestamps = verification.get("verifiedTimestamps")
        statement = _object(verification.get("statement"))
        if (
            any(
                not isinstance(certificate.get(k), str) or not str(certificate[k]).strip()
                for k in _CERT_FIELDS
            )
            or not isinstance(timestamps, list)
            or not timestamps
            or any(not isinstance(t, dict) or not t for t in timestamps)
            or not isinstance(statement.get("predicateType"), str)
        ):
            raise _hold("missing or unsupported verified identity fields")
        if any(certificate[k] != expected.certificate[k] for k in _CERT_FIELDS):
            continue
        if statement["predicateType"] != expected.predicate_type:
            continue
        subject = statement.get("subject")
        if not isinstance(subject, list) or len(subject) != 1:
            raise _hold("unsupported signed subject cardinality")
        digest = _object(_object(subject[0]).get("digest"))
        predicate = _object(statement.get("predicate"))
        if digest.get("sha256") != expected.zip_sha256:
            continue
        if set(predicate) != set(expected.predicate) or any(
            type(predicate[k]) is not type(v) or predicate[k] != v
            for k, v in expected.predicate.items()
        ):
            continue
        matches.append(index)
    if len(matches) != 1:
        raise _hold("no unique matching verified attestation candidate")
    return matches[0]
