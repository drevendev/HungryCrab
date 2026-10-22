"""Fail closed before Hungry Crab publishes generated text containing likely secrets.

This scanner intentionally sees only text the crab is about to publish. It is not a prey
repository scanner and it never returns the matched value: callers get a logical path, line
number, and rule name suitable for an error message. A path that is itself secret-shaped is
scanned too, and is never rendered.

Every line goes through three passes, and the first that finds something decides the line:

1. Shapes: token formats their issuers document (AWS key ids, GitHub, GitLab and Slack tokens,
   Slack webhooks, PEM private-key headers) and a password embedded in a URL.
2. Credential assignments: a name whose last word is a credential word (``token``, ``secret``,
   ``password``, ``api_key`` and a few more) bound to a literal that looks like a credential.
   Only a quoted literal is a value in code, so ``tokens = estimate_tokens(text)`` (plural, and
   a call) and ``token = other`` (a name) are not findings. Unquoted values count in
   configuration files and in dotenv-style ``NAME=value`` lines.
3. Entropy: long opaque tokens. A path is judged one segment at a time, and lock files, which
   carry only hashes and URLs, skip this pass.

The rules were written for this module from the issuers' public token formats.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    (
        "gitlab-token",
        re.compile(r"\bgl(?:pat|dt|rt|ptt|oas|cbt|ft|imt|soat|agent)-[A-Za-z0-9_.-]{20,}"),
    ),
    ("slack-token", re.compile(r"\b(?:xox[a-z]|xapp)-[A-Za-z0-9-]{10,}")),
    (
        "slack-webhook",
        re.compile(r"\bhooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9_/-]{16,}"),
    ),
    (
        "private-key",
        re.compile(r"-{4,5} ?BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)? ?-{4,5}", re.IGNORECASE),
    ),
)
# scheme://user:password@host; the user part may be empty (redis://:password@host). Every
# repetition is bounded so that a long line cannot make a search quadratic.
_URL_CREDENTIALS = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]{0,31}://"
    r"(?P<user>[^\s/?#@:]{0,256}):(?P<password>[^\s/?#@]{1,256})@[\w\[]"
)

# A name, optionally quoted (JSON, YAML), then an assignment or key separator. Comparisons
# (==, !=, <=, >=), scopes (::) and URLs (://) are not separators.
_ASSIGNMENT = re.compile(
    r"""(?x)
    (?<![\w.\\])
    (?P<quote>["']?)(?P<name>[A-Za-z_][\w.-]{0,63})(?P=quote)
    [ \t]*(?P<sep>:=|=>|(?<![=!<>:])=(?![=~>])|:(?![:=/]))[ \t]*
    """
)
_ANNOTATION = re.compile(r"[A-Za-z_][\w.\[\], |]{0,80}?[ \t]+=(?![=>])[ \t]*")
_QUOTED = re.compile(r"""(?:[rRbBuU]{1,2})?(?P<q>["'])(?P<literal>(?:\\.|(?!(?P=q)).)*)(?P=q)""")
_QUOTED_TAIL = re.compile(r"[ \t]*(?:[,;)\]}].*|#.*|//.*)?")
_BARE = re.compile(r"(?P<value>[^\s\"'`]+)[ \t]*(?:[ \t][#;].*)?")
_NAME_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_CREDENTIAL_WORDS = frozenset(
    {
        "token",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "apikey",
        "secretkey",
        "accesskey",
        "privatekey",
    }
)
_KEY_QUALIFIERS = frozenset(
    {"api", "access", "secret", "private", "signing", "encryption", "client", "auth", "master"}
)
_ENV_NAME = re.compile(r"[A-Z]+(?:_[A-Z0-9]+)*")
_ATTRIBUTE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_EXPRESSION_CHARS = frozenset("()[]{}`")
_YAML_WORDS = frozenset({"true", "false", "yes", "no", "on", "off", "null", "none", "nil", "~"})
_LITERAL_MIN_LENGTH = 6
_LITERAL_MIN_BITS = 2.5
# Files whose bare `name: value` and `name = value` lines are literals, not code.
_CONFIG_SUFFIXES = frozenset(
    {".yml", ".yaml", ".ini", ".cfg", ".conf", ".properties", ".env", ".md", ".markdown"}
)
# Generated files that carry only resolved versions, URLs and content hashes.
_LOCK_FILES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "pdm.lock",
        "pipfile.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "cargo.lock",
        "gemfile.lock",
        "composer.lock",
        "go.sum",
        "flake.lock",
        "packages.lock.json",
        ".terraform.lock.hcl",
    }
)

_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9_+/=-]{31,})(?![A-Za-z0-9])")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_WORD_PART = re.compile(r"[a-z]+|[0-9a-f]+")
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_REFERENCE_VALUE = re.compile(
    r"^(?:\$[A-Z_][A-Z0-9_]*|\$\{[A-Z_][A-Z0-9_]*\}|<[^>]+>)$", re.IGNORECASE
)
_ASSIGNMENT_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}=(.+)$")
_SAFE_GITHUB_TRACE_URL = re.compile(
    r"https?://(?:www\.)?github\.com/"
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"(?:/commit/[0-9a-fA-F]{7,64}(?![A-Za-z0-9]))?"
)
_PLACEHOLDER_VALUES = frozenset(
    {
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
        "your_token_here",
        "token_here",
        "redacted",
    }
)
# What documentation writes where a URL's password goes.
_URL_PLACEHOLDERS = frozenset({"pass", "password", "passwd", "pwd", "pw", "secret", "token"})
_MASKED = re.compile(r"[*x.]+", re.IGNORECASE)
_ENTROPY_MIN_LENGTH = 32
_ENTROPY_MIN_BITS = 4.3
# A token with slashes is judged whole only when it is base64 that is denser than any path.
_SLASHED_MIN_LENGTH = 40
_SLASHED_MIN_BITS = 4.6


@dataclass(frozen=True)
class PublicationFinding:
    """A safe-to-render description of one publication blocker.

    ``line`` 0 means the logical path itself matched; such a path is rendered redacted.
    """

    path: str
    line: int
    rule: str


def _looks_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if _REFERENCE_VALUE.fullmatch(normalized):
        return True
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_VALUES


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
    if classes < 3 or _identifier_like(value):
        return False
    return _shannon_entropy(value) >= _ENTROPY_MIN_BITS


def _identifier_like(value: str) -> bool:
    """Lowercase words and hex or digit runs joined by - or _, as in a crab branch name."""

    parts = [part for part in re.split(r"[-_]+", value) if part]
    return len(parts) > 1 and all(_WORD_PART.fullmatch(part) for part in parts)


def _opaque_secret(value: str) -> bool:
    """A long opaque token; a path is judged segment by segment, never as one blob."""

    if "/" not in value or value.lower().startswith(("sha256-", "sha384-", "sha512-")):
        return _looks_high_entropy_secret(value)
    if (
        len(value) >= _SLASHED_MIN_LENGTH
        and _BASE64.fullmatch(value)
        and any(ch.islower() for ch in value)
        and any(ch.isupper() for ch in value)
        and any(ch.isdigit() for ch in value)
        and _shannon_entropy(value) >= _SLASHED_MIN_BITS
    ):
        return True
    return any(_looks_high_entropy_secret(part) for part in value.split("/"))


def _is_credential_name(name: str) -> bool:
    """The last word of the name's last component is a credential word, as a whole word."""

    words = [word.lower() for word in _NAME_WORD.findall(re.split(r"[.\[\]]", name)[-1])]
    while words and words[-1].isdigit():
        words.pop()
    if not words:
        return False
    if words[-1] in _CREDENTIAL_WORDS:
        return True
    return words[-1] == "key" and len(words) > 1 and words[-2] in _KEY_QUALIFIERS


def _credential_literal(value: str) -> bool:
    """A literal that could be a credential rather than a name, reference, template or prose."""

    if len(value) < _LITERAL_MIN_LENGTH or _looks_placeholder(value):
        return False
    if any(ch.isspace() for ch in value) or "{" in value or "%s" in value or "%(" in value:
        return False
    if value.lower() in _YAML_WORDS or _ENV_NAME.fullmatch(value):
        return False
    letters = any(ch.isalpha() for ch in value)
    digits = any(ch.isdigit() for ch in value)
    classes = sum(
        (
            any(ch.islower() for ch in value),
            any(ch.isupper() for ch in value),
            digits,
            any(not ch.isalnum() for ch in value),
        )
    )
    if not letters or not (digits or classes >= 3):
        return False
    return _shannon_entropy(value) >= _LITERAL_MIN_BITS


def _bare_literal(value: str) -> bool:
    """An unquoted value that is a literal, not a reference, alias, tag or expression."""

    if value[:1] in {"$", "!", "*", "&", "<", "@", "%"} or "${" in value or "$(" in value:
        return False
    if any(ch in _EXPRESSION_CHARS for ch in value) or _ATTRIBUTE.fullmatch(value):
        return False
    return _credential_literal(value.rstrip(",;"))


def _accepts_bare_values(logical_path: str) -> bool:
    """Configuration files, Markdown and the pull request's own fields (no file suffix)."""

    path = PurePosixPath(logical_path.replace("\\", "/"))
    name = path.name.lower()
    return (
        path.suffix.lower() in _CONFIG_SUFFIXES
        or name.startswith(".env")
        or (not path.suffix and "/" not in logical_path)
    )


def _is_lock_file(logical_path: str) -> bool:
    name = PurePosixPath(logical_path.replace("\\", "/")).name.lower()
    return name in _LOCK_FILES or name.endswith((".lock", ".lockfile"))


def _shape_rules(line: str) -> list[str]:
    rules = [rule for rule, pattern in _SECRET_PATTERNS if pattern.search(line)]
    for match in _URL_CREDENTIALS.finditer(line):
        password = match.group("password")
        if not (
            _looks_placeholder(password)
            or password.lower() in _URL_PLACEHOLDERS
            or password.lower() == match.group("user").lower()
            or _MASKED.fullmatch(password)
            or password.startswith(("$", "%", "{", "<"))
        ):
            rules.append("url-credentials")
            break
    return rules


def _credential_assignment(line: str, *, bare_values: bool) -> bool:
    for match in _ASSIGNMENT.finditer(line):
        if not _is_credential_name(match.group("name")):
            continue
        rest = line[match.end() :]
        annotated = _ANNOTATION.match(rest) if match.group("sep") == ":" else None
        if annotated is not None:  # `token: str = "..."`
            rest = rest[annotated.end() :]
        quoted = _QUOTED.match(rest)
        if quoted is not None:
            tail = rest[quoted.end() :]
            if _QUOTED_TAIL.fullmatch(tail) and _credential_literal(quoted.group("literal")):
                return True
            continue
        bare = _BARE.fullmatch(rest)
        if bare is None or annotated is not None:
            continue
        prefix = line[: match.start()]
        # NAME=value at the start of a line, as a dotenv file or a shell script writes it.
        dotenv = match.group(0) == f"{match.group('name')}=" and (
            prefix == "" or re.fullmatch(r"\s*export\s+", prefix) is not None
        )
        key_line = bare_values and re.fullmatch(r"\s*(?:-\s+)?", prefix) is not None
        if (dotenv or key_line) and _bare_literal(bare.group("value")):
            return True
    return False


def _entropy_hit(line: str) -> bool:
    # Repository URLs and hex commit trace URLs are public identifiers. Strip only those
    # known-safe shapes for the generic entropy pass; extra path/query/fragment content
    # remains visible.
    entropy_line = _SAFE_GITHUB_TRACE_URL.sub(" ", line)
    return any(_opaque_secret(_token_value(m.group(1))) for m in _TOKEN.finditer(entropy_line))


def _path_rules(logical_path: str) -> list[str]:
    """Rules a logical path matches: a generated file name is published like its content."""

    segments = re.sub(r"[\\/]", " ", logical_path)
    rules = _shape_rules(segments)
    if not rules and _entropy_hit(segments):
        rules.append("high-entropy-token")
    return rules


def scan_publication(
    logical_path: str, text: str, *, ordinal: int | None = None
) -> list[PublicationFinding]:
    """Return blockers in generated ``text`` without retaining matched secret values.

    A secret-shaped ``logical_path`` is reported on line 0 under a redacted label (numbered by
    ``ordinal`` when the caller scans a bundle), and so is every finding in its content.
    """

    path_rules = _path_rules(logical_path)
    if path_rules:
        shown = "<redacted path>" if ordinal is None else f"<redacted path #{ordinal}>"
    else:
        shown = logical_path
    findings = [PublicationFinding(shown, 0, rule) for rule in path_rules]
    bare_values = _accepts_bare_values(logical_path)
    lock_file = _is_lock_file(logical_path)

    for line_number, line in enumerate(text.splitlines(), start=1):
        rules = _shape_rules(line)
        if rules:
            findings.extend(PublicationFinding(shown, line_number, rule) for rule in rules)
            continue
        if lock_file:
            continue
        if _credential_assignment(line, bare_values=bare_values):
            findings.append(PublicationFinding(shown, line_number, "credential-assignment"))
            continue
        if _entropy_hit(line):
            findings.append(PublicationFinding(shown, line_number, "high-entropy-token"))

    return findings


def scan_publication_bundle(items: Iterable[tuple[str, str]]) -> list[PublicationFinding]:
    """Scan all generated files/fields before the first publication side effect."""

    findings: list[PublicationFinding] = []
    for ordinal, (logical_path, text) in enumerate(items, start=1):
        findings.extend(scan_publication(logical_path, text, ordinal=ordinal))
    return findings


def format_publication_findings(findings: Iterable[PublicationFinding]) -> str:
    """Render blockers without ever echoing the matched values."""

    return "\n".join(
        f"{finding.path} (path: {finding.rule})"
        if finding.line == 0
        else f"{finding.path}:{finding.line} ({finding.rule})"
        for finding in findings
    )
