"""Fail closed before Hungry Crab publishes generated text containing likely secrets.

This scanner intentionally sees only text the crab is about to publish. It is not a prey
repository scanner and it never returns the matched value: callers get a logical path, line
number, and rule name suitable for an error message.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "private-key",
        re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", re.IGNORECASE),
    ),
)

_ENV_ASSIGNMENT = re.compile(
    r"""(?ix)
    ^\s*(?:export\s+)?
    (?P<name>[A-Z0-9_]*(?:
        TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET
    )[A-Z0-9_]*)\s*=\s*
    (?P<value>.+?)\s*$
    """
)
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9_+/=-]{31,})(?![A-Za-z0-9])"
)
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_REFERENCE_VALUE = re.compile(
    r"^(?:\$[A-Z_][A-Z0-9_]*|\$\{[A-Z_][A-Z0-9_]*\}|<[^>]+>)$", re.IGNORECASE
)
_ASSIGNMENT_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}=(.+)$")
_PLACEHOLDER_WORDS = (
    "example",
    "placeholder",
    "changeme",
    "replace_me",
    "replace-me",
    "dummy",
    "sample",
    "yourtoken",
    "your_token",
    "your-token",
    "token_here",
    "redacted",
)
_ENTROPY_MIN_LENGTH = 32
_ENTROPY_MIN_BITS = 4.3


@dataclass(frozen=True)
class PublicationFinding:
    """A safe-to-render description of one publication blocker."""

    path: str
    line: int
    rule: str


def _stripped_assignment_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _looks_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if _REFERENCE_VALUE.fullmatch(normalized):
        return True
    compact = re.sub(r"[^a-z0-9_-]+", "", normalized)
    if not compact:
        return True
    return any(word in compact for word in _PLACEHOLDER_WORDS)


def _shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    if not length:
        return 0.0
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _token_value(candidate: str) -> str:
    assignment = _ASSIGNMENT_TOKEN.fullmatch(candidate)
    if assignment is not None:
        return assignment.group(1)
    return candidate


def _looks_high_entropy_secret(value: str) -> bool:
    if len(value) < _ENTROPY_MIN_LENGTH or _looks_placeholder(value):
        return False
    # Commit hashes, content digests and package integrity hashes are expected in generated trace.
    if _HEX.fullmatch(value) or _UUID.fullmatch(value):
        return False
    if value.lower().startswith(("sha256-", "sha384-", "sha512-")):
        return False
    classes = sum(
        (
            any(ch.islower() for ch in value),
            any(ch.isupper() for ch in value),
            any(ch.isdigit() for ch in value),
            any(ch in "_+/=-" for ch in value),
        )
    )
    if classes < 3:
        return False
    return _shannon_entropy(value) >= _ENTROPY_MIN_BITS


def scan_publication(logical_path: str, text: str) -> list[PublicationFinding]:
    """Return blockers in generated ``text`` without retaining matched secret values."""

    findings: list[PublicationFinding] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        matched_shape = False
        for rule, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(PublicationFinding(logical_path, line_number, rule))
                matched_shape = True
        if matched_shape:
            continue

        assignment = _ENV_ASSIGNMENT.match(line)
        if assignment is not None:
            value = _stripped_assignment_value(assignment.group("value"))
            if len(value) >= 8 and not _looks_placeholder(value):
                findings.append(
                    PublicationFinding(logical_path, line_number, "credential-assignment")
                )
                continue

        for match in _TOKEN.finditer(line):
            value = _token_value(match.group(1))
            if _looks_high_entropy_secret(value):
                findings.append(
                    PublicationFinding(logical_path, line_number, "high-entropy-token")
                )
                break

    return findings


def scan_publication_bundle(items: Iterable[tuple[str, str]]) -> list[PublicationFinding]:
    """Scan all generated files/fields before the first publication side effect."""

    findings: list[PublicationFinding] = []
    for logical_path, text in items:
        findings.extend(scan_publication(logical_path, text))
    return findings


def format_publication_findings(findings: Iterable[PublicationFinding]) -> str:
    """Render blockers without ever echoing the matched values."""

    return "\n".join(f"{finding.path}:{finding.line} ({finding.rule})" for finding in findings)
