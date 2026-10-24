from __future__ import annotations

import base64
import random
import time
from pathlib import Path

import pytest

from hungry_crab.compare.candidates import Side
from hungry_crab.errors import CrabError
from hungry_crab.nutrients import Candidate
from hungry_crab.pr_publication import (
    GeneratedFile,
    PreparedPullRequest,
    nutrient_branch_name,
    nutrient_spec_path,
    publication_items,
    publish_prepared_pull_request,
)
from hungry_crab.publication_safety import (
    PublicationFinding,
    format_publication_findings,
    scan_publication,
    scan_publication_bundle,
)
from hungry_crab.serve import render_issue

# Every secret-shaped vector is assembled at run time, so that this file is not itself a finding
# when the repository's own tree is scanned (see the corpus test at the end).
GITHUB = "ghp" + "_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
OPAQUE = "Q7mZ3vN8pR2xT5kL" + "9cW4dF6hJ1sY0uB3"
PASSWORD = "Tr0ub4dor&3" + "-correct-horse"
LITERAL = "Zx81" + "kq0Lm2"
DASHES = "-" * 5


def _key_header(kind: str) -> str:
    return f"{DASHES}BEGIN {kind}{DASHES}"


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (f"aws = {AWS}", "aws-access-key"),
        ("aws = " + "ASIA" + "ABCDEFGHIJKLMNOP", "aws-access-key"),
        (f"token = {GITHUB}", "github-token"),
        ("token = " + "github_pat_" + "11ABCDEFG0123456789" + "_abcdefghijklmnop", "github-token"),
        ("GITLAB=" + "glpat-" + "A1b2C3d4E5f6G7h8I9j0", "gitlab-token"),
        ("slack = " + "xoxb-" + "1234567890-ABCDEFGHIJ", "slack-token"),
        ("slack = " + "xoxp-" + "1234567890-" + "1234567890-abcdefghij", "slack-token"),
        ("slack = " + "xoxe.xoxp-" + "1-Mi0yLTEyMzQ1Njc4OTAx", "slack-token"),
        (
            "url = https://hooks.slack.com/services/" + "T0ABCDEFG/B0ABCDEFG/aBcDeFgHiJkL",
            "slack-webhook",
        ),
        (_key_header("OPENSSH PRIVATE KEY"), "private-key"),
        (_key_header("RSA PRIVATE KEY"), "private-key"),
        (_key_header("PRIVATE KEY"), "private-key"),
        (_key_header("ENCRYPTED PRIVATE KEY"), "private-key"),
        (_key_header("PGP PRIVATE KEY BLOCK"), "private-key"),
        (
            "DATABASE_URL=postgres://crab:" + "s3cure-production-password@db.example/crab",
            "url-credentials",
        ),
        ("redis://:" + "n0t-a-placeholder@cache.internal:6379/0", "url-credentials"),
    ],
)
def test_high_confidence_secret_shapes_block_without_echoing_value(text: str, rule: str) -> None:
    findings = scan_publication("generated/config.txt", f"safe\n{text}\n")

    assert PublicationFinding("generated/config.txt", 2, rule) in findings
    rendered = format_publication_findings(findings)
    assert rule in rendered
    assert text not in rendered


@pytest.mark.parametrize("secret", [GITHUB, "postgres://crab:" + PASSWORD + "@db.example/crab"])
def test_html_entities_cannot_hide_a_published_credential(secret: str) -> None:
    encoded = "".join(f"&#{ord(ch)};" for ch in secret)
    findings = scan_publication("pull-request-body.md", f"safe\n> {encoded}\n")
    assert findings and all(f.line == 2 for f in findings)
    assert secret not in format_publication_findings(findings)
    assert scan_publication(f"generated/{encoded}.txt", "safe")[0].path == "<redacted path>"
    with pytest.raises(CrabError):
        publish_prepared_pull_request(
            PreparedPullRequest("safe", encoded, ()),
            publish=lambda *_: pytest.fail("encoded credential reached publication"),
        )


def test_quoting_a_receipt_does_not_disable_its_secret_scan() -> None:
    from hungry_crab.receipt_text import quote_receipt_text

    quoted = quote_receipt_text("credential: " + GITHUB)
    assert GITHUB in quoted, "literal data must still pass the secret scan"
    assert scan_publication("pull-request-body.md", quoted)


@pytest.mark.parametrize("password", [PASSWORD, "password(at)" + LITERAL, "pass&Tab;word"])
def test_visible_at_substitution_cannot_hide_url_credentials(password: str) -> None:
    from hungry_crab.safety import safe_name

    shown = safe_name("postgres://crab:" + password + "@db.example/crab")
    assert "@" not in shown
    assert any(f.rule == "url-credentials" for f in scan_publication("PR_BODY", shown))
    with pytest.raises(CrabError):
        publish_prepared_pull_request(
            PreparedPullRequest("safe", shown, ()),
            lambda _: pytest.fail("transformed credentials reached publication"),
        )


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


def test_placeholder_words_only_match_as_complete_values() -> None:
    assignment_value = "prod-example-" + OPAQUE
    entropy_value = "prod_sample_" + OPAQUE
    text = "\n".join(
        [
            "API_TOKEN=YOUR_TOKEN_HERE",
            "CLIENT_SECRET=placeholder",
            f"API_TOKEN={assignment_value}",
            entropy_value,
        ]
    )

    findings = scan_publication("generated/secrets.txt", text)

    assert findings == [
        PublicationFinding("generated/secrets.txt", 3, "credential-assignment"),
        PublicationFinding("generated/secrets.txt", 4, "high-entropy-token"),
    ]
    rendered = format_publication_findings(findings)
    assert assignment_value not in rendered
    assert entropy_value not in rendered


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("config.yml", f"password: {PASSWORD}"),
        ("config.yml", f"  - client-secret: {PASSWORD}  # production"),
        ("config.json", f'{{"password": "{PASSWORD}"}}'),
        ("pyproject.toml", f'password = "{PASSWORD}"'),
        ("settings.ini", f"password = {PASSWORD}"),
        ("app.properties", "db.password=hunter2x"),
        ("short.env", "PASSWORD=hunter2"),
        ("deploy.sh", f"  export API_KEY={LITERAL}"),
        ("PR_BODY", f"Configure it with\n\nsecret_key: {PASSWORD}"),
        ("src/app.py", f'password = "{LITERAL}"'),
        ("src/app.py", 'SECRET_KEY = "dj-insecure-' + '9f8e7d6c5b4a"'),
        ("src/app.py", f'client = Client(url, api_key="{LITERAL}", timeout=3)'),
        ("src/app.py", f'def connect(token: str = "{LITERAL}") -> None:'),
        ("src/app.py", f"self.password = '{LITERAL}'  # temporary"),
        ("src/app.ts", f'const config = {{ apiKey: "{LITERAL}", region }};'),
        ("src/main.go", f'token := "{LITERAL}"'),
        ("src/app.php", f"$config = ['password' => '{LITERAL}'];"),
        ("src/app.php", f"$password = '{LITERAL}';"),
        # In a configuration file a credential-named key needs no entropy, only eight characters.
        (".env", "DB_PASSWORD=supersecretpassword"),
        ("prod.env", "export DB_PASSWORD=supersecretpassword"),
        ("config.yml", "password: supersecretpassword"),
        ("config.json", '{"client_secret": "supersecretpassword"}'),
        ("settings.ini", "password = supersecretpassword"),
        ("pyproject.toml", 'token = "supersecrettoken"'),
    ],
)
def test_credential_literals_block_in_any_syntax(path: str, line: str) -> None:
    findings = scan_publication(path, line)

    assert [finding.rule for finding in findings] == ["credential-assignment"]
    rendered = format_publication_findings(findings)
    assert LITERAL not in rendered and PASSWORD not in rendered


@pytest.mark.parametrize(
    ("path", "line"),
    [
        # The false positives #135 lists, from this repository and as probes.
        ("src/digest.py", "    tokens = estimate_tokens(text)"),
        ("src/maw.py", '        token_env=str(serve.get("token_env") or "").strip(),'),
        ("src/matrix.py", '_TOKEN_RE = re.compile(r"[()]|[^\\s()]+")'),
        ("src/ci.py", '    secrets = doc.section("Secrets referenced (names only)", priority=4)'),
        ("src/guard.py", "        program_token = tokens[0]"),
        ("src/x.py", "TOKEN_TTL_SECONDS = 60 * 60 * 24"),
        ("src/x.py", 'TOKENIZER = "tiktoken"'),
        ("src/x.py", 'access_key_id = request.headers.get("x-access-key")'),
        # Names, calls, attributes, annotations, templates and references are not literals.
        ("src/x.py", "token = client.token if source_url is None else None"),
        ("src/x.py", 'token = os.environ["GH_TOKEN"]'),
        ("src/x.py", 'password = "prefix" + suffix'),
        ("src/x.py", 'token = f"{prefix}-{suffix}"'),
        ("src/x.py", "def load(token: str | None = None, password: str = '') -> None:"),
        ("src/x.py", "password: hunter22"),
        ("src/x.ts", "  secret: Uint8Array;"),
        ("src/x.py", 'token_env = "CRAB_APP_TOKEN"'),
        ("src/x.py", 'token = "GH_TOKEN"'),
        ("src/x.py", 'password = "Enter your password"'),
        ("src/x.py", 'if password == "hunter22":'),
        ("action.yml", "    default: ${{ github.token }}"),
        ("ci.yml", "          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}"),
        ("config.yaml", "wifi_password: !secret wifi_password"),
        ("config.yaml", "password: *shared_password"),
        ("README.md", "token: required"),
        ("deploy.sh", "TOKEN=$(gh auth token)"),
        ("deploy.sh", 'export GH_TOKEN="$GITHUB_TOKEN"'),
        # Dev defaults, documentation placeholders and references in URLs.
        ("compose.yml", "DATABASE_URL: postgres://postgres:postgres@localhost:5432/test"),
        ("README.md", "Use `postgres://user:password@localhost/db`."),
        ("README.md", "git clone https://x-access-token:${GH_TOKEN}@github.com/owner/repo"),
        ("src/x.py", 'url = f"https://{user}:{password}@{host}/"'),
        ("README.md", "Trace: https://github.com/drevendev/HungryCrab/issues/135"),
    ],
)
def test_ordinary_code_and_references_do_not_block(path: str, line: str) -> None:
    assert scan_publication(path, line) == []


@pytest.mark.parametrize(
    ("path", "line"),
    [
        # The placeholders documentation prints in a secret's shape. Assembled at run time: the
        # forge's own push protection reads a literal webhook URL as a secret, placeholder or not.
        (
            "src/app.py",
            'DOCS = "https://hooks.slack.com/services/T'
            + "0" * 8
            + "/B"
            + "0" * 8
            + "/"
            + "X" * 24
            + '"',
        ),
        ("src/app.py", 'TOKEN = "ghp_' + "x" * 36 + '"'),
        ("README.md", "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"),
        ("README.md", "aws_access_key_id = AKIA" + "X" * 16),
        # References to a value kept elsewhere.
        ("config.yml", "password: env:DB_PASSWORD"),
        ("config.yml", "secret: vault:secret/data/app#password"),
        ("config.yml", "password: ${DB_PASSWORD}"),
        (".env", "DB_PASSWORD=${DB_PASSWORD:-}"),
        (".env", "DB_PASSWORD=$DB_PASSWORD"),
        ("settings.ini", "password = %DB_PASSWORD%"),
        ("src/app.py", 'password = "%DB_PASSWORD%"'),
        # Placeholders and defaults in configuration files.
        ("config.yml", "password: your-password-here"),
        ("config.yml", "password: '********'"),
        ("config.yml", "password: password"),
        ("config.yml", "  password: CHANGE_ME"),
        ("ci.yml", "      POSTGRES_PASSWORD: postgres"),
        # Quoted values in code that read as names.
        ("src/lexer.py", "    token = 'IDENT2'"),
        ("src/lexer.py", "    token = 'LPAREN'"),
        ("src/lexer.py", "    token = 'Int64Lit'"),
        ("src/app.py", 'Token = "Identifier1"'),
        ("src/app.py", 'next_token = "NextToken1"'),
        ("src/app.py", 'secret = "SecretName2"'),
    ],
)
def test_placeholders_references_and_names_do_not_block(path: str, line: str) -> None:
    assert scan_publication(path, line) == []


@pytest.mark.parametrize(
    "line",
    [
        'uv run --directory "$env:CRAB_ACTION_PATH" python "$env:CRAB_ACTION_PATH/scripts/run.py"',
        '  ".github/ISSUE_TEMPLATE/feature_request.md",',
        '"path": "skills/claude-api/python/managed-agents/README.md"',
        'cat "$CRAB_CACHE_DIR/github/acme/widget/repo/bin/cat" README.md',
    ],
)
def test_paths_are_judged_one_segment_at_a_time(line: str) -> None:
    assert scan_publication("generated/notes.txt", line) == []


IMAGE_DIGEST = "sha256:" + "4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1"
REGISTRY_PASSWORD = "Kj8sLq2W" + "p9xZ"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYq9R2kL7mN3"


@pytest.mark.parametrize(
    ("path", "line"),
    [
        # #388: digest-pinned image references are not user:password@host.
        (".github/workflows/lint.yml", f"      - uses: docker://alpine:3.20@{IMAGE_DIGEST}"),
        (".github/workflows/lint.yml", f"      - uses: docker://node:20-alpine@{IMAGE_DIGEST}"),
        (".github/workflows/lint.yml", f"        image: docker://python:3.12-slim@{IMAGE_DIGEST}"),
        ("scripts/mirror.sh", f"skopeo copy docker://alpine:3.20@{IMAGE_DIGEST} oci:alpine"),
        ("docs/ci.md", f"Pin the action as `docker://alpine:3.20@{IMAGE_DIGEST}`."),
        (
            ".github/workflows/lint.yml",
            f"      - uses: docker://ghcr.io/owner/tool:1.0@{IMAGE_DIGEST}",
        ),
        ("scripts/mirror.sh", "skopeo inspect oci://layout:v1.2@" + "sha512:" + "ab" * 64),
        # Translations are labels, not configuration.
        ("config/locales/de.yml", "    password: Passwort"),
        ("config/locales/es.yml", "    password: Contraseña"),
        ("src/locales/nl.json", '    "password": "Wachtwoord",'),
        ("src/locales/de.json", '    "secret": "Geheimnis",'),
        ("src/locale/fr.json", '    "token": "Jetonsecret",'),
        ("app/translations/messages.de.yaml", "  password: Kennwortfeld"),
        ("src/i18n/pt-BR.yaml", "  password: Senhasecreta"),
        # Laravel keeps its translations under lang/, named after the locale.
        ("lang/de.json", '    "Password": "Passwort",'),
        ("resources/lang/de.json", '    "Password": "Passwort",'),
        ("src/lang/pt_BR.json", '    "password": "Palavrapasse",'),
        # A chart names the Kubernetes Secret that holds the credential.
        ("charts/app/values.yaml", "  existingSecret: my-release-postgresql"),
        ("charts/app/values.yaml", "  auth:\n    existingSecret: db-credentials.v2"),
        # Header names are constants, not credentials.
        ("src/web/csrf.ts", 'export const csrfToken = "X-CSRF-Token";'),
        ("src/web/api.ts", 'const apiKey = "X-API-Key";'),
        ("src/web/api.py", 'API_KEY = "x-api-key"'),
        ("src/web/trace.ts", 'const traceToken = "X-B3-TraceId";'),
        ("config/http.yml", "auth_token: X-Auth-Token"),
        ("src/web/hooks.ts", 'const webhookSecret = "X-Hub-Signature-256";'),
        # Slack's prefixes are xoxa, xoxb, xoxc, xoxd, xoxe, xoxp, xoxr, xoxs and xapp.
        ("docs/emoji.md", "Signed off with xoxo-from-the-whole-team"),
        ("src/slugs.py", "SLUG = 'xoxo-gossip-girl-recap'"),
    ],
)
def test_the_0_6_1_false_positives_do_not_block(path: str, line: str) -> None:
    assert scan_publication(path, line + "\n") == []


@pytest.mark.parametrize(
    ("path", "line", "rule"),
    [
        # The narrowing of #388 keeps the shapes it does not describe.
        ("README.md", "https://crab:" + PASSWORD + "@sha256.example.com/", "url-credentials"),
        ("compose.yml", "url: redis://:" + PASSWORD + "@cache:6379/0", "url-credentials"),
        ("src/web/api.ts", 'const apiKey = "X-9f8e7d6c' + '5b4a3";', "credential-assignment"),
        ("charts/app/values.yaml", "existingSecret: " + PASSWORD, "credential-assignment"),
        ("charts/app/values.yaml", "adminPassword: my-release-postgresql", "credential-assignment"),
        ("config/locales/de.yml", f"password: {LITERAL}", "credential-assignment"),
        # A file under lang/ that is not named after a locale is code or configuration.
        ("lang/go/config.yml", "api_token: Summer2024Token", "credential-assignment"),
        ("lang/settings.json", '"password": "Summer2024Token",', "credential-assignment"),
        ("docs/slack.md", "SLACK=" + "xoxs-" + "1234567890-ABCDEFGHIJ", "slack-token"),
        ("docs/slack.md", "SLACK=" + "xapp-" + "1-A0123456789-abcdef", "slack-token"),
        # Review of #388: an image reference with a password still carries the password.
        (
            "scripts/mirror.sh",
            "skopeo copy docker://deploy:"
            + REGISTRY_PASSWORD
            + "@registry.example.com/app:1 oci:x",
            "url-credentials",
        ),
        (
            "scripts/chart.sh",
            "helm pull oci://robot:" + REGISTRY_PASSWORD + "@registry.example.com/charts/app",
            "url-credentials",
        ),
        # Only translation formats under translation directories are judged like code; `lang/`
        # is a directory of language-specific code as often as of translations.
        ("lang/py/.env", "DB_PASSWORD=" + "Summer2024", "credential-assignment"),
        (
            "lang/java/src/main/resources/application.properties",
            "spring.datasource.password=" + "Summer2024",
            "credential-assignment",
        ),
        ("languages/go/config.yml", "api_token: " + "Summer2024Token", "credential-assignment"),
        ("translations/.env", "SECRET_KEY=" + "correcthorsebatterystaple", "credential-assignment"),
        (
            "translations/settings.toml",
            'api_key = "' + "correcthorsebattery" + '"',
            "credential-assignment",
        ),
        # Only `existingSecret` names a Secret; these keys hold the credential.
        (
            "charts/app/values.yaml",
            "  existingPassword: " + "s3cr3tpassw0rd",
            "credential-assignment",
        ),
        (
            "charts/app/values.yaml",
            "  existing_api_token: " + "3f9a2b7c1d8e4f6a" + "0b5c9d2e7f1a3b8c",
            "credential-assignment",
        ),
        (
            "config/app.yml",
            "use_existing_token: " + "q7rk9zp2wm4tn8vbl3xc",
            "credential-assignment",
        ),
        # Text glued to a forge URL is not part of its path.
        (
            "PR_BODY",
            "see https://github.com/o/r/blob/main/a.py," + AWS_SECRET,
            "high-entropy-token",
        ),
        (
            "PR_BODY",
            "see https://github.com/o/r/blob/main/a.py;aws_secret_access_key=" + AWS_SECRET,
            "high-entropy-token",
        ),
        # The path after a forge URL's commit is judged as any path is, dense base64 whole.
        ("PR_BODY", "see https://github.com/o/r/tree/main/" + AWS_SECRET, "high-entropy-token"),
        # A random value behind `X-` is not a header name.
        ("config/app.yml", "api_key: X-" + "Q7Rk9Zp2Wm4" + "Tn8VbL3", "credential-assignment"),
    ],
)
def test_the_0_6_1_true_positives_still_block(path: str, line: str, rule: str) -> None:
    assert [finding.rule for finding in scan_publication(path, line + "\n")] == [rule]


def test_the_crab_s_own_branch_and_specification_names_do_not_block() -> None:
    nutrient = "crab:docs:astral-sh-uv:contributing-guide-with-reproducible-benchmarks"
    spec = nutrient_spec_path(nutrient)
    branch = nutrient_branch_name(nutrient)
    body = f"Specification: [`{spec}`](https://github.com/o/r/blob/{branch}/{spec})"

    assert scan_publication_bundle([(spec, "# Specification\n"), ("PR_BODY", body)]) == []


def test_lock_files_skip_the_entropy_pass_but_not_the_shapes() -> None:
    uv_lock = "\n".join(
        [
            '[[package]]\nname = "pyyaml"\nversion = "6.0.2"',
            (
                'sdist = { url = "https://files.pythonhosted.org/packages/54/ed/'
                "79a089b6be93607fa5cdaedf301d7dfb23af5f25c398d5ead2525b063e17/"
                'pyyaml-6.0.2.tar.gz", '
                'hash = "sha256:d584d9ec91ad65861cc08d42e834324ef890a082e591037abe114850ff7bbc3e" }'
            ),
        ]
    )
    go_sum = "github.com/spf13/cobra v1.8.0 h1:7aJaZx1B85qltLMc546zn58" + "BxxfZdR/W22ej9CFoEf0="

    assert scan_publication("uv.lock", uv_lock) == []
    assert scan_publication("service/go.sum", go_sum) == []
    assert scan_publication("notes/go.txt", go_sum) != [], "the same hash elsewhere is opaque"
    leaked = f'source = {{ registry = "https://crab:{LITERAL}@pypi.internal/simple" }}'
    assert scan_publication("uv.lock", leaked) == [
        PublicationFinding("uv.lock", 1, "url-credentials")
    ]


def test_a_private_key_body_without_its_header_blocks() -> None:
    rng = random.Random(135)
    der = bytes.fromhex("308204bd020100300d06092a864886f70d0101010500048204a7308204a302010002")
    body = base64.b64encode(der + bytes(rng.randrange(256) for _ in range(600))).decode()
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]

    findings = scan_publication("generated/key.txt", "\n".join(lines))

    assert len(findings) >= len(lines) // 2
    assert {finding.rule for finding in findings} == {"high-entropy-token"}
    rendered = format_publication_findings(findings)
    assert not any(line in rendered for line in lines)


def test_high_entropy_token_blocks_without_treating_trace_as_secret() -> None:
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

    assert scan_publication("generated/secret.txt", OPAQUE) == [
        PublicationFinding("generated/secret.txt", 1, "high-entropy-token")
    ]
    assert scan_publication("generated/trace.txt", trace) == []


def test_slashes_do_not_hide_a_base64_blob() -> None:
    # Two slashes cut 44 characters of base64 into pieces shorter than the entropy minimum.
    rng = random.Random(7)
    blob = base64.b64encode(bytes(rng.randrange(256) for _ in range(33))).decode()
    blob = blob[:14] + "/" + blob[15:28] + "/" + blob[29:]

    assert scan_publication("generated/key.txt", f"key={blob}") == [
        PublicationFinding("generated/key.txt", 1, "high-entropy-token")
    ]


_LONG = 400_000
BACKSLASH = chr(92)


@pytest.mark.parametrize(
    ("path", "line"),
    [
        # Unterminated literals full of backslashes: escapes must not backtrack.
        ("src/x.py", 'token = "' + BACKSLASH * 38),
        ("src/x.c", 'secret = "' + BACKSLASH * 2 * 18 + BACKSLASH),
        ("src/x.py", 'token = "' + BACKSLASH * _LONG),
        ("src/x.c", 'secret = "' + BACKSLASH * 2 * (_LONG // 2) + BACKSLASH),
        # A repeated assignment: every match must examine a bounded stretch of the line.
        ("src/x.py", "token=" * (_LONG // 6)),
        (".env", "token=" * (_LONG // 6)),
        ("config.yml", "token: " * (_LONG // 7)),
        ("src/x.py", 'password="' + "password=" * (_LONG // 9) + '"'),
        ("config.yml", "token: " + " " * _LONG + "x"),
        ("src/x.py", "a-" * (_LONG // 2)),
        # Forge URLs: a project path that never reaches a view, and views without a path.
        ("PR_BODY", "https://gitlab.com" + "/a" * (_LONG // 2)),
        ("PR_BODY", "https://gitlab.com/" * (_LONG // 19)),
        ("PR_BODY", "https://github.com/o/r/blob" * (_LONG // 27)),
        ("PR_BODY", "https://gitlab.com/g/r/-/blob/" + "a/" * (_LONG // 2)),
    ],
    ids=[
        "backslashes-38",
        "c-string-47",
        "backslashes-400k",
        "c-string-400k",
        "token=-repeated",
        "token=-repeated-env",
        "token:-repeated",
        "names-inside-a-literal",
        "spaces-before-value",
        "dashed-words",
        "gitlab-project-path",
        "gitlab-hosts",
        "github-views",
        "gitlab-long-path",
    ],
)
def test_a_long_hostile_line_scans_in_linear_time(path: str, line: str) -> None:
    started = time.perf_counter()
    scan_publication(path, line)
    assert time.perf_counter() - started < 1.0


def test_github_trace_exemption_does_not_hide_opaque_path_segments() -> None:
    safe = "Trace: https://github.com/example/prey/commit/0123456789abcdef"
    suspicious = f"Trace: https://github.com/example/prey/commit/{OPAQUE}"

    assert scan_publication("PR_BODY", safe) == []
    findings = scan_publication("PR_BODY", suspicious)
    assert findings == [PublicationFinding("PR_BODY", 1, "high-entropy-token")]
    assert OPAQUE not in format_publication_findings(findings)


_EVIDENCE_PATHS = [
    "okhttp/src/main/kotlin/okhttp3/internal/http2/Http2Connection.kt",
    "src/main/java/com/example/Http2Handler.java",
    "src/Microsoft/Extensions/Http/HttpClientFactory.cs",
    "src/http/client.go",
    "CONTRIBUTING.md",
    "app/Http/Controllers/UserController.php",
    "src/app/(auth)/login/page.tsx",
]


@pytest.mark.parametrize(
    "prey_url", ["https://github.com/square/okhttp", "https://gitlab.com/group/sub/okhttp"]
)
def test_the_crab_s_own_evidence_urls_are_not_secrets(prey_url: str) -> None:
    """#387: `render_issue` links evidence at the meal's commit; no commit makes that a secret."""

    rng = random.Random(387)
    for _ in range(300):
        sha = f"{rng.getrandbits(160):040x}"
        prey = Side(label="square/okhttp", sha=sha, url=prey_url, root=None)
        card = Candidate(
            category="pattern",
            key="square-okhttp-http2-flow-control",
            title="Bound HTTP/2 flow-control windows",
            what="The prey bounds every HTTP/2 stream window.",
            evidence=[prey.evidence(path) for path in _EVIDENCE_PATHS],
            origin="repository",
            license_mode="REIMPLEMENT",
        )
        menu = {"prey": {"label": prey.label, "sha": sha, "url": prey_url, "license": "MIT"}}
        title, body = render_issue(card, menu)
        assert f"/blob/{sha}/" in body
        assert scan_publication("PR_BODY", body) == [], sha
        assert scan_publication("PR_TITLE", title) == []
    prepared = PreparedPullRequest(
        title=title, body=body, files=(GeneratedFile("src/flow.py", "WINDOW = 65535\n"),)
    )
    assert publish_prepared_pull_request(prepared, lambda _: "published") == "published"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r/blob/0123456789abcdef0123456789abcdef01234567/{secret}",
        "https://github.com/o/r/tree/main/{secret}/docs",
        "https://github.com/o/r/blob/{secret}/README.md",
        "https://gitlab.com/g/r/-/blob/0123456789abcdef0123456789abcdef01234567/{secret}",
        "https://github.com/o/r/blob/0123456789abcdef/README.md?token={secret}",
    ],
)
def test_a_forge_url_still_shows_its_opaque_segments(url: str) -> None:
    findings = scan_publication("PR_BODY", f"See [x]({url.format(secret=OPAQUE)}).")

    assert findings == [PublicationFinding("PR_BODY", 1, "high-entropy-token")]


def test_clean_bundle_passes_unchanged() -> None:
    items = [
        ("generated/README.md", "# Nutrient\n\nNo credentials here.\n"),
        ("PR_TITLE", "feat: carry a useful nutrient"),
        ("PR_BODY", "Trace: https://github.com/example/prey/commit/0123456789abcdef"),
    ]

    assert scan_publication_bundle(items) == []


def test_bundle_reports_only_safe_location_metadata() -> None:
    findings = scan_publication_bundle(
        [("generated/settings.yml", f"name: crab\ntoken: {GITHUB}\n")]
    )

    assert findings == [PublicationFinding("generated/settings.yml", 2, "github-token")]
    rendered = format_publication_findings(findings)
    assert GITHUB not in rendered
    assert rendered == "generated/settings.yml:2 (github-token)"


def test_a_secret_shaped_path_blocks_and_is_never_rendered() -> None:
    prepared = PreparedPullRequest(
        title="safe",
        body="safe",
        files=(
            GeneratedFile("generated/notes.txt", "safe\n"),
            GeneratedFile(f"generated/{GITHUB}.txt", f"{AWS}\n"),
        ),
    )

    findings = scan_publication_bundle(publication_items(prepared))

    assert findings == [
        PublicationFinding("<redacted path #2>", 0, "github-token"),
        PublicationFinding("<redacted path #2>", 1, "aws-access-key"),
    ]
    rendered = format_publication_findings(findings)
    assert GITHUB not in rendered and AWS not in rendered
    assert rendered.splitlines() == [
        "<redacted path #2> (path: github-token)",
        "<redacted path #2>:1 (aws-access-key)",
    ]
    assert scan_publication(f"generated/{GITHUB}.txt", "safe\n") == [
        PublicationFinding("<redacted path>", 0, "github-token")
    ]


def test_a_secret_shaped_path_alone_refuses_before_publication() -> None:
    prepared = PreparedPullRequest(
        title="safe", body="safe", files=(GeneratedFile(f"generated/{GITHUB}.txt", "safe\n"),)
    )
    published: list[PreparedPullRequest] = []

    with pytest.raises(CrabError) as caught:
        publish_prepared_pull_request(prepared, published.append)

    assert published == []
    assert GITHUB not in f"{caught.value} {caught.value.hint}"


def test_the_repository_tree_is_not_a_finding() -> None:
    """#135's acceptance corpus: this repository's own sources, tests and lock file."""

    root = Path(__file__).resolve().parents[1]
    files = [root / "uv.lock", root / "pyproject.toml"]
    for top in ("src", "tests"):
        files += [
            path
            for path in sorted((root / top).rglob("*"))
            if path.is_file()
            and not any(part.startswith((".", "__")) for part in path.relative_to(root).parts)
        ]
    # Synthetic GitHub tokens that the pull-request publication tests feed to the scanner.
    vectors = {"tests/test_pr_publication.py", "tests/test_pr_serve.py"}
    unexpected = []
    for path in files:
        data = path.read_bytes()
        if b"\0" in data:
            continue
        logical = path.relative_to(root).as_posix()
        for finding in scan_publication(logical, data.decode("utf-8", errors="replace")):
            if not (logical in vectors and finding.rule == "github-token"):
                unexpected.append(finding)
    assert len(files) > 100
    assert unexpected == []
