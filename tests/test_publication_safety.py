from __future__ import annotations

import pytest

from hungry_crab.publication_safety import (
    PublicationFinding,
    format_publication_findings,
    scan_publication,
    scan_publication_bundle,
)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("aws = AKIAABCDEFGHIJKLMNOP", "aws-access-key"),
        ("token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "github-token"),
        ("slack = xoxb-1234567890-ABCDEFGHIJ", "slack-token"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "private-key"),
    ],
)
def test_high_confidence_secret_shapes_block_without_echoing_value(
    text: str, rule: str
) -> None:
    findings = scan_publication("generated/config.txt", f"safe\n{text}\n")

    assert PublicationFinding("generated/config.txt", 2, rule) in findings
    rendered = format_publication_findings(findings)
    assert rule in rendered
    assert text not in rendered


def test_credential_assignment_blocks_but_placeholders_do_not() -> None:
    text = "\n".join(
        [
            "API_TOKEN=YOUR_TOKEN_HERE",
            "CLIENT_SECRET=${CLIENT_SECRET}",
            "DATABASE_PASSWORD='s3cure-production-password'",
        ]
    )

    assert scan_publication(".env.example", text) == [
        PublicationFinding(".env.example", 3, "credential-assignment")
    ]


def test_high_entropy_token_blocks_without_treating_trace_as_secret() -> None:
    secret = "Q7mZ3vN8pR2xT5kL9cW4dF6hJ1sY0uB3"
    trace = "\n".join(
        [
            "sha=0123456789abcdef0123456789abcdef01234567",
            "url=https://github.com/drevendev/HungryCrab",
            "license=GPL-2.0-or-later",
            "request_id=550e8400-e29b-41d4-a716-446655440000",
            "example=aGVsbG8gd29ybGQgdGhpcyBpcyBhIHRlc3Q=",
            "integrity=sha512-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+/AbCdEfGhIjKlMnOpQr==",
            "token=YOUR_TOKEN_HERE",
        ]
    )

    assert scan_publication("generated/secret.txt", secret) == [
        PublicationFinding("generated/secret.txt", 1, "high-entropy-token")
    ]
    assert scan_publication("generated/trace.txt", trace) == []


def test_clean_bundle_passes_unchanged() -> None:
    items = [
        ("generated/README.md", "# Nutrient\n\nNo credentials here.\n"),
        ("PR_TITLE", "feat: carry a useful nutrient"),
        ("PR_BODY", "Trace: https://github.com/example/prey/commit/0123456789abcdef"),
    ]

    assert scan_publication_bundle(items) == []


def test_bundle_reports_only_safe_location_metadata() -> None:
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    findings = scan_publication_bundle(
        [("generated/settings.yml", f"name: crab\ntoken: {secret}\n")]
    )

    assert findings == [PublicationFinding("generated/settings.yml", 2, "github-token")]
    rendered = format_publication_findings(findings)
    assert secret not in rendered
    assert rendered == "generated/settings.yml:2 (github-token)"
