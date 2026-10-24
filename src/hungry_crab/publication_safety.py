"""Fail closed before Hungry Crab publishes generated text containing likely secrets.

This scanner intentionally sees only text the crab is about to publish. It is not a prey
repository scanner and it never returns the matched value: callers get a logical path, line
number, and rule name suitable for an error message. A path that is itself secret-shaped is
scanned too, and is never rendered.

Every line goes through three passes, and the first that finds something decides the line:

1. Shapes: token formats their issuers document (AWS key ids, GitHub, GitLab and Slack tokens,
   Slack webhooks, PEM private-key headers) and a password embedded in a URL. The placeholders
   documentation prints in those shapes (all zeros, all x, AWS's ``...EXAMPLE``) are not, and
   neither is an image pinned by digest (``docker://alpine:3.20@sha256:...``): a digest after
   the ``@`` is not a host.
2. Credential assignments: a name whose last word is a credential word (``token``, ``secret``,
   ``password``, ``api_key`` and a few more) bound to a literal that looks like a credential.
   Only a quoted literal is a value in code, so ``tokens = estimate_tokens(text)`` (plural, and
   a call) and ``token = other`` (a name) are not findings, and neither is a literal that reads
   as a name (``token = 'IDENT2'``). Unquoted values count in configuration files and in
   dotenv-style ``NAME=value`` lines. In a configuration file any literal of eight or more
   characters counts; a YAML or JSON file under a locale directory (``locales/``, ``i18n/``,
   ``translations/``) holds translations and is judged like code. Placeholders, references to
   a store (``env:NAME``, ``${NAME}``, ``$NAME``, ``%NAME%``), ``X-`` header names and the
   Kubernetes Secret a chart's ``existingSecret`` names never do.
3. Entropy: long opaque tokens. A path is judged one segment at a time, and a GitHub or GitLab
   commit, blob, tree or raw URL (`/blob/<sha>/<path>`, as the crab links evidence) is read as
   its commit and its path, judged apart; lock files, which carry only hashes and URLs, skip
   this pass.

The rules were written for this module from the issuers' public token formats.
"""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

# Each shape names the issuer-chosen part `body`; a body that documentation writes as a
# placeholder (all zeros or all x, or AWS's `...EXAMPLE` key) is not a secret.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)(?P<body>[0-9A-Z]{16})\b")),
    ("github-token", re.compile(r"\bgh[pousr]_(?P<body>[A-Za-z0-9]{20,})")),
    ("github-token", re.compile(r"\bgithub_pat_(?P<body>[A-Za-z0-9_]{20,})")),
    (
        "gitlab-token",
        re.compile(
            r"\bgl(?:pat|dt|rt|ptt|oas|cbt|ft|imt|soat|agent)-(?P<body>[A-Za-z0-9_.-]{20,})"
        ),
    ),
    # Slack's documented prefixes only: `xoxo-from-the-team` is a word, not a token.
    ("slack-token", re.compile(r"\b(?:xox[abcdeprs]|xapp)-(?P<body>[A-Za-z0-9-]{10,})")),
    (
        "slack-webhook",
        re.compile(
            r"\bhooks\.slack\.com/(?:services|workflows|triggers)/(?P<body>[A-Za-z0-9_/-]{16,})"
        ),
    ),
    (
        "private-key",
        re.compile(r"-{4,5} ?BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)? ?-{4,5}", re.IGNORECASE),
    ),
)
_PLACEHOLDER_SEGMENT = re.compile(r"[A-Z]?(?:0+|x+|X+)")
# scheme://user:password@host; the user part may be empty (redis://:password@host). A digest
# after the `@` is not a host: `docker://alpine:3.20@sha256:...` pins an image by content, while
# `docker://deploy:password@registry/...` still carries a password. Every repetition is bounded
# so that a long line cannot make a search quadratic.
_URL_CREDENTIALS = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]{0,31}://"
    r"(?P<user>[^\s/?#@:]{0,256}):(?P<password>[^\s/?#@]{1,256})"
    r"@(?!sha(?:256|384|512):)[\w\[]"
)

# A name, optionally quoted (JSON, YAML), then an assignment or key separator. Comparisons
# (==, !=, <=, >=), scopes (::) and URLs (://) are not separators.
_ASSIGNMENT = re.compile(
    r"""(?x)
    (?<![\w.\\])
    (?P<quote>["']?)(?P<name>[A-Za-z_][\w.-]{0,63}+)(?P=quote)
    [ \t]*(?P<sep>:=|=>|(?<![=!<>:])=(?![=~>])|:(?![:=/]))[ \t]*
    """
)
_ANNOTATION = re.compile(r"[A-Za-z_][\w.\[\], |]{0,80}?[ \t]{1,4}=(?![=>])[ \t]*")
# The alternatives inside each string literal are disjoint (an escape, or any other character
# but the quote), so an unterminated literal fails in linear time.
_QUOTED = re.compile(
    r"""(?:[rRbBuU]{1,2})?(?:"(?P<double>(?:[^"\\]|\\.)*)"|'(?P<single>(?:[^'\\]|\\.)*)')"""
)
# What may follow a literal for it to be the whole value; only the start is examined.
_QUOTED_TAIL = re.compile(r"[ \t]*(?:[,;)\]}#]|//|$)")
# A bare value is the rest of the line, up to a comment.
_BARE = re.compile(r"(?P<value>[^\s\"'`]{1,256}+)[ \t]*+(?:$|(?<=[ \t])(?=[#;]))")
# Where a key starts on a line: after the indentation and an `export ` or a YAML `- `.
_LINE_HEAD = re.compile(r"[ \t]*(?:(?P<export>export[ \t]+)|-[ \t]+)?")
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
# In a configuration file a credential-named key holds a credential whatever it looks like.
_CONFIG_LITERAL_MIN_LENGTH = 8
# A quoted value in code that reads as a name (`IDENT2`, `NextToken1`, `Int64Lit`).
_CODE_NAME_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[0-9]+|_+|.")
# Files whose bare `name: value` and `name = value` lines are literals, not code.
_BARE_SUFFIXES = frozenset(
    {".yml", ".yaml", ".ini", ".cfg", ".conf", ".properties", ".env", ".md", ".markdown"}
)
# Configuration files proper: no Markdown prose, but TOML and JSON, whose strings are quoted.
_CONFIG_SUFFIXES = frozenset(
    {".yml", ".yaml", ".ini", ".cfg", ".conf", ".properties", ".env", ".toml", ".json"}
)
# Directories that hold translations: `password: Passwort` there is a label, not a credential,
# so their YAML and JSON files are judged like code rather than like configuration. Only those
# formats: a dotenv, INI, TOML or properties file there is configuration like anywhere else.
_LOCALE_DIRECTORIES = frozenset({"locale", "locales", "i18n", "l10n", "translations"})
# `lang/` holds Laravel's translations (`lang/de.json`) but also the code of multi-language trees
# (`lang/go/config.yml`), so a file there counts only when it is named after a locale.
_LANG_DIRECTORY = "lang"
_LOCALE_NAME = re.compile(r"[a-z]{2,3}(?:[_-][A-Za-z]{2,4})?")
_TRANSLATION_SUFFIXES = frozenset({".yml", ".yaml", ".json"})
# A Kubernetes object name (RFC 1123 subdomain): what a chart's `existingSecret` holds.
_KUBERNETES_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}+(?<=[a-z0-9]))?")
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
# References to a value kept elsewhere: $NAME, ${NAME} (with a default), %NAME%, <name>, and
# a secret store's scheme (env:NAME, vault:path, op://vault/item).
_REFERENCE_VALUE = re.compile(
    r"^(?:\$[A-Z_][A-Z0-9_]*|\$\{[A-Z_][A-Z0-9_]*(?::?[-=?+][^}]*)?\}|%[A-Z_][A-Z0-9_]*%|<[^>]+>"
    r"|(?:env|vault|file|secret|secrets|ssm|sops|keyring|op):\S+)$",
    re.IGNORECASE,
)
_ASSIGNMENT_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}=(.+)$")
# The forge URLs the crab writes itself: a GitHub repository, optionally followed by a commit,
# blob, tree or raw view (`/blob/<sha>/<path>`), and the same views of a GitLab project, with
# or without GitLab's `/-/` separator. The host, the repository and the view are public
# identifiers; what follows the view is kept, the commit or branch apart from the path after
# it, so a hex commit is judged as a digest and the path as any path is. The path is only what
# `quote(path, safe="/")` writes when the crab builds such a link (letters, digits, `-._~/`
# and `%` escapes), so text glued to a URL with `,`, `;` or `=` is not part of it and is still
# judged whole. Every repetition is bounded, and each starts after a literal host, so a long
# line scans linearly.
_FORGE_VIEW = r"/(?:blob|tree|raw|commit)(?=/)"
_FORGE_PATH = r"/[A-Za-z0-9._~%/-]{1,2048}+"
_SAFE_FORGE_URL = re.compile(
    r"https?://(?:"
    r"(?:www\.)?github\.com/[A-Za-z0-9_.-]{1,100}+/[A-Za-z0-9_.-]{1,100}+"
    rf"(?:{_FORGE_VIEW}(?P<github_path>{_FORGE_PATH}))?"
    r"|gitlab\.com(?:/[A-Za-z0-9_.-]{1,255}+){2,20}?(?:/-)?"
    rf"{_FORGE_VIEW}(?P<gitlab_path>{_FORGE_PATH})"
    r")"
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
        "change_me",
        "change-me",
        "changeit",
    }
)
# What documentation writes where a URL's password goes.
_URL_PLACEHOLDERS = frozenset({"pass", "password", "passwd", "pwd", "pw", "secret", "token"})
_MASKED = re.compile(r"[*x.]+", re.IGNORECASE)
# `your-password-here`, `YOUR_API_KEY`, `<your token>`: what documentation asks the reader for.
_YOUR_PREFIX = ("your", "<your")
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


def _literal_placeholder(value: str) -> bool:
    """An assigned value documentation writes instead of a credential, or a reference to one."""

    normalized = value.strip().lower()
    return (
        _looks_placeholder(value)
        or normalized in _URL_PLACEHOLDERS
        or normalized.startswith(_YOUR_PREFIX)
        or _MASKED.fullmatch(normalized) is not None
    )


def _placeholder_body(body: str) -> bool:
    """A token body made of zeros or x (T00000000/B00000000/XXXX), or AWS's documented example."""

    if body.endswith("EXAMPLE"):
        return True
    segments = [segment for segment in re.split(r"[/_.-]", body) if segment]
    return bool(segments) and all(_PLACEHOLDER_SEGMENT.fullmatch(part) for part in segments)


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


@lru_cache(maxsize=1024)
def _name_words(name: str) -> tuple[str, ...]:
    """The lowercase words of a name's last component, without trailing numbers."""

    words = [word.lower() for word in _NAME_WORD.findall(re.split(r"[.\[\]]", name)[-1])]
    while words and words[-1].isdigit():
        words.pop()
    return tuple(words)


def _is_credential_name(name: str) -> bool:
    """The last word of the name's last component is a credential word, as a whole word."""

    words = _name_words(name)
    if not words:
        return False
    if words[-1] in _CREDENTIAL_WORDS:
        return True
    return words[-1] == "key" and len(words) > 1 and words[-2] in _KEY_QUALIFIERS


def _code_name(value: str) -> bool:
    """CamelCase or upper-case words with digits, as a token kind or a type is named."""

    return value[:1].isupper() and all(
        part[0].isupper() or part[0].isdigit() or part[0] == "_"
        for part in _CODE_NAME_PART.findall(value)
    )


def _header_name(value: str) -> bool:
    """An HTTP header name in the `X-` convention: `X-CSRF-Token`, `X-API-Key`, `x-api-key`.

    Each word after the `X-` reads as a name (CamelCase, upper-case or a lowercase word) or is a
    number (`X-Hub-Signature-256`), and other digits appear only in a short word
    (`X-B3-TraceId`), which a random string with a leading `X-` (`X-Q7Rk9Zp2Wm4Tn8VbL3`) does
    not do. At least one word is a word.
    """

    if value[:2] not in {"X-", "x-"}:
        return False
    words = value[2:].split("-")
    return any(word.isalpha() for word in words) and all(
        word.isdigit()
        or (
            word
            and (word.isalpha() or len(word) <= 4)
            and (_code_name(word) or (word.isalpha() and word.islower()))
        )
        for word in words
    )


def _secret_object_name(name: str, value: str) -> bool:
    """A Kubernetes Secret named by a chart value (`existingSecret: my-release-postgresql`).

    The key is `existingSecret` (or `existing_secret`): the Secret already exists elsewhere, and
    the value is shaped as a Kubernetes object name (lowercase letters, digits, `-` and `.`),
    so it names the object holding the credential rather than being one. `existingPassword`
    or `use_existing_token` hold the credential itself.
    """

    words = _name_words(name)
    return words[-2:] == ("existing", "secret") and _KUBERNETES_NAME.fullmatch(value) is not None


def _credential_literal(value: str, *, name: str = "", config: bool = False) -> bool:
    """A literal that could be a credential rather than a name, reference, template or prose.

    In a configuration file any other literal of eight or more characters is a credential. In
    code the literal must also vary like one, and a value that reads as a name is not.
    """

    if len(value) < _LITERAL_MIN_LENGTH or _literal_placeholder(value):
        return False
    if any(ch.isspace() for ch in value) or "{" in value or "%s" in value or "%(" in value:
        return False
    if value.lower() in _YAML_WORDS or _ENV_NAME.fullmatch(value):
        return False
    if value.lower() in _name_words(name):  # `POSTGRES_PASSWORD: postgres`, a service default
        return False
    if _header_name(value) or _secret_object_name(name, value):
        return False
    if config and len(value) >= _CONFIG_LITERAL_MIN_LENGTH:
        return True
    if not config and _code_name(value):
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


def _bare_literal(value: str, *, name: str, config: bool) -> bool:
    """An unquoted value that is a literal, not a reference, alias, tag or expression."""

    if value[:1] in {"$", "!", "*", "&", "<", "@", "%"} or "${" in value or "$(" in value:
        return False
    if any(ch in _EXPRESSION_CHARS for ch in value) or _ATTRIBUTE.fullmatch(value):
        return False
    return _credential_literal(value.rstrip(",;"), name=name, config=config)


def _is_dotenv(name: str) -> bool:
    return name.startswith(".env") or name.endswith(".env")


def _accepts_bare_values(logical_path: str) -> bool:
    """Configuration files, Markdown and the pull request's own fields (no file suffix)."""

    path = PurePosixPath(logical_path.replace("\\", "/"))
    return (
        path.suffix.lower() in _BARE_SUFFIXES
        or _is_dotenv(path.name.lower())
        or (not path.suffix and "/" not in logical_path)
    )


def _is_config_file(logical_path: str) -> bool:
    path = PurePosixPath(logical_path.replace("\\", "/"))
    if path.suffix.lower() in _TRANSLATION_SUFFIXES:
        directories = {part.lower() for part in path.parts[:-1]}
        if directories & _LOCALE_DIRECTORIES or (
            _LANG_DIRECTORY in directories and _LOCALE_NAME.fullmatch(path.stem) is not None
        ):
            return False
    return path.suffix.lower() in _CONFIG_SUFFIXES or _is_dotenv(path.name.lower())


def _is_lock_file(logical_path: str) -> bool:
    name = PurePosixPath(logical_path.replace("\\", "/")).name.lower()
    return name in _LOCK_FILES or name.endswith((".lock", ".lockfile"))


def _shape_rules(line: str) -> list[str]:
    rules: list[str] = []
    for rule, pattern in _SECRET_PATTERNS:
        if rule in rules:
            continue
        for match in pattern.finditer(line):
            body = match.groupdict().get("body")
            if body is None or not _placeholder_body(body):
                rules.append(rule)
                break
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


def _credential_assignment(line: str, *, bare_values: bool, config: bool) -> bool:
    # Every check below starts at a position in the line and examines a bounded stretch of it,
    # so that a line repeating `token=` does not cost the square of its length.
    head = _LINE_HEAD.match(line)
    key_start = head.end() if head is not None else 0
    exported = head is not None and head.group("export") is not None
    for match in _ASSIGNMENT.finditer(line):
        name = match.group("name")
        if not _is_credential_name(name):
            continue
        pos = match.end()
        annotated = _ANNOTATION.match(line, pos) if match.group("sep") == ":" else None
        if annotated is not None:  # `token: str = "..."`
            pos = annotated.end()
        quoted = _QUOTED.match(line, pos)
        if quoted is not None:
            literal = quoted.group("double")
            if literal is None:
                literal = quoted.group("single")
            if _credential_literal(literal, name=name, config=config) and _QUOTED_TAIL.match(
                line, quoted.end()
            ):
                return True
            continue
        if annotated is not None:
            continue
        # NAME=value at the start of a line, as a dotenv file or a shell script writes it.
        dotenv = match.group(0) == f"{match.group('name')}=" and (
            match.start() == 0 or (exported and match.start() == key_start)
        )
        key_line = bare_values and match.start() == key_start
        if not (dotenv or key_line):
            continue
        bare = _BARE.match(line, pos)
        if bare is not None and _bare_literal(bare.group("value"), name=name, config=config):
            return True
    return False


def _forge_url_segments(match: re.Match[str]) -> str:
    """A forge URL without its public identifiers: the commit, then the path after it.

    A hex commit followed by a mixed-case path reads as one dense token, so the two are not
    left joined. The commit (or branch) is judged on its own, and the path as any path is: one
    segment at a time, unless the whole of it is base64 denser than any path.
    """

    path = (match.group("github_path") or match.group("gitlab_path") or "").lstrip("/")
    ref, _, rest = path.partition("/")
    return f" {ref} {rest} "


def _entropy_hit(line: str) -> bool:
    # Repository URLs and the commit, blob, tree and raw URLs the crab writes are public
    # identifiers. Strip only those known-safe shapes for the generic entropy pass; the path,
    # query and fragment after them remain visible.
    entropy_line = _SAFE_FORGE_URL.sub(_forge_url_segments, line)
    return any(_opaque_secret(_token_value(m.group(1))) for m in _TOKEN.finditer(entropy_line))


def _path_rules(logical_path: str) -> list[str]:
    """Rules a logical path matches: a generated file name is published like its content."""

    rules: list[str] = []
    for variant in dict.fromkeys((logical_path, html.unescape(logical_path))):
        segments = re.sub(r"[\\/]", " ", variant)
        found = _shape_rules(variant) + _shape_rules(segments)
        if not found and _entropy_hit(segments):
            found.append("high-entropy-token")
        rules.extend(found)
    return list(dict.fromkeys(rules))


def _line_rules(line: str, *, bare_values: bool, config: bool, lock_file: bool) -> list[str]:
    rules = _shape_rules(line)
    if rules or lock_file:
        return rules
    if _credential_assignment(line, bare_values=bare_values, config=config):
        return ["credential-assignment"]
    return ["high-entropy-token"] if _entropy_hit(line) else []


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
    config = _is_config_file(logical_path)
    lock_file = _is_lock_file(logical_path)

    for line_number, line in enumerate(text.splitlines(), start=1):
        # Renderers encode Markdown/provider syntax as entities. Those bytes must not hide a
        # credential that a reader sees after one HTML decoding pass (for example `&#95;`
        # in a token prefix, or `&#64;` in URL credentials). Preserve physical line numbers.
        rules = []
        for variant in dict.fromkeys((line, html.unescape(line))):
            rules.extend(
                _line_rules(variant, bare_values=bare_values, config=config, lock_file=lock_file)
            )
        findings.extend(
            PublicationFinding(shown, line_number, rule) for rule in dict.fromkeys(rules)
        )

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
