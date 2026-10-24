"""Prey content is untrusted data.

READMEs, issues and code comments may carry instructions aimed at an agent. The miners never
copy body text into the Markdown summaries (only headings, names and counts), and they flag
instruction-like fragments so a skill can treat the file with extra suspicion.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import quote, urlsplit

from .mdutil import inline

# Invisible code points are a classic carrier for hidden instructions, and the Tags block is
# the one that carries whole sentences: it maps every printable ASCII character to a code
# point that renders as nothing, so an instruction can be appended to an innocent heading and
# read back by a model. Built with chr() so the source file itself stays free of them.
#
# Invisibility alone is not the test; carrying a hidden instruction is. Variation selectors
# (U+FE00-FE0F) end almost every emoji in a README title, the invisible mathematical operators
# (U+2061-2064) are real notation, U+180E is real in legacy Mongolian, and the soft hyphen
# (U+00AD) is real hyphenation. None of them is added here: each would flag legitimate content
# for two bits of hiding room, and a detector that fires on ordinary text stops carrying
# information, which is the lesson the syrupy meal already taught this module.
#
# The two joiners are load-bearing in real text and are judged by their neighbours instead.
# U+200D glues every multi-person and profession emoji together, and U+200C is ordinary
# orthography in Persian and in the Indic scripts; both sit between characters that are not
# ASCII. A joiner that has no such neighbour — wedged into a Latin word, dangling at the end
# of a heading, or stacked with other invisible characters — has nothing to join and is
# flagged like the rest.
#
# The bidirectional controls are invisible too, and they do worse than hide: an override or an
# isolate reorders what is displayed, so `Safe` + U+202E + `dm.EMDAER` shows a reader one
# string while a model reads another (the Trojan Source trick). They are format characters,
# not control characters, so `mdutil.inline` keeps them. A README heading or a prey name has
# no need for them, and they are flagged like the rest.
#
# The three bidi *marks* (U+061C, U+200E, U+200F) reorder nothing on their own: they give a
# neutral character such as a digit or a parenthesis a direction, and right-to-left READMEs
# use them routinely. They are judged by their surroundings: a mark in a string that has no
# right-to-left letter has nothing to mark and is flagged.
_INVISIBLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x200B, 0x200B),  # zero-width space
    (0x202A, 0x202E),  # bidi embeddings, pop and overrides
    (0x2060, 0x2060),  # word joiner
    (0x2066, 0x2069),  # bidi isolates
    (0xFEFF, 0xFEFF),  # byte-order mark
    (0xE0000, 0xE007F),  # Tags: printable ASCII rendered as nothing
)
_INVISIBLE = "".join(
    chr(low) if low == high else f"{chr(low)}-{chr(high)}" for low, high in _INVISIBLE_RANGES
)
_BIDI_MARKS = chr(0x061C) + chr(0x200E) + chr(0x200F)  # Arabic letter mark, LRM, RLM
# Hebrew, Arabic, Syriac, Thaana, NKo, Samaritan, Mandaic and their presentation forms.
_RIGHT_TO_LEFT_RE = re.compile(
    "["
    + "".join(
        f"{chr(low)}-{chr(high)}"
        for low, high in ((0x0590, 0x061B), (0x061D, 0x08FF), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFC))
    )
    + "]"
)
_BIDI_MARK = f"[{_BIDI_MARKS}]"
_JOINERS = chr(0x200C) + chr(0x200D)  # zero-width non-joiner, zero-width joiner
_ZERO_WIDTH = _INVISIBLE + _JOINERS
# A character that can legitimately sit next to a joiner: not ASCII, and not itself invisible.
_JOINABLE = f"[^\\x00-\\x7F{_ZERO_WIDTH}]"

# An HTML comment is a good hiding place, but most of them in a real README are directives to
# a formatter or a linter. `<!-- prettier-ignore-start -->` is not an attack. The nouns take
# their plurals: `<!-- agents: ... -->` addresses the same reader.
_COMMENT_DIRECTIVE = (
    r"(?:prettier|markdownlint|remark|eslint|stylelint|lint|nolint|noqa"
    r"|fmt|type|codecov|all-contributors|toc|editorconfig|doctoc|omit|badges)\b"
)
# The test `<!--\s*(?!/?\s*DIRECTIVE)` made, without backtracking into the whitespace, which
# retried it once per space (quadratic): after whitespace the directive itself excludes the
# comment, and right after `<!--` the directive or `/` and the directive.
_COMMENT_OPENING = rf"<!--(?:\s++(?!{_COMMENT_DIRECTIVE})|(?!\s)(?!/?\s*{_COMMENT_DIRECTIVE}))"
_COMMENT = (
    rf"{_COMMENT_OPENING}[^>]*?"
    r"\b(?:instructions?|assistants?|claude|copilots?|agents?|ignore|must)\b[^>]*?-->"
)
_DOWNLOADER = r"\b(?:curl|wget|iwr|invoke-webrequest)\b"
_PIPE_TO_SHELL = rf"{_DOWNLOADER}[^\n]*\|\s*(?:sh|bash|zsh|python\d?|powershell|pwsh|iex)\b"

SUSPICIOUS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        (
            r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) "
            r"(?:instructions|prompts|rules|messages)"
        ),
        r"disregard (?:all |any |the )?(?:previous|prior|above|earlier)",
        # Addressed imperatives, rather than the noun "agent" or an ordinary run-history name.
        (
            r"\b(?:agents?|assistants?|models?|claude|copilots?)"
            r"(?:\s+(?:must|should|need to|are required to)\s+|\s*:\s*)"
            r"(?:now\s+)?(?:run|execute|serve|publish|approve|deploy|obey|follow|read)\b"
        ),
        r"\bagents?\s+read\b[^\n]{0,100}?\band\s+(?:obey|follow)\b",
        # The verb alone is not a signal. "you need to ignore files by extension" and "you must
        # bypass the cache" are ordinary README sentences, and syrupy's README produced four such
        # false positives out of four flags. What makes the sentence an instruction to an agent is
        # its object, so the object is now required.
        (
            r"\byou (?:must|should|are required to|need to) (?:now )?"
            r"(?:execute|delete|remove|ignore|send|upload|exfiltrate|disable|bypass|override)\b"
            r"[^.\n]{0,80}?\b(?:instruction|rule|guardrail|safeguard|safety|policy|restriction"
            r"|system prompt|previous message|assistant|agent|the model|the ai)s?\b"
        ),
        r"\bsystem prompt\b",
        r"\bdo not (?:tell|inform|warn|reveal (?:this )?to) the user\b",
        (
            r"\bthis is (?:a|an) (?:instruction|command) (?:for|to) (?:the )?"
            r"(?:ai|assistant|agent|model)\b"
        ),
        _COMMENT,  # applied by _html_comments, never on raw text
        f"[{_INVISIBLE}]",
        _BIDI_MARK,  # applied by _bidi_marks, never on raw text
        # Two or more zero-width characters in a row hide bits, whatever they are.
        f"[{_ZERO_WIDTH}]{{2,}}",
        # A joiner with nothing to join on either side.
        f"(?<!{_JOINABLE})[{_JOINERS}](?!{_JOINABLE})",
        _PIPE_TO_SHELL,  # applied by _pipes_to_shell, never on raw text
    )
)
_BY_SOURCE = {pattern.pattern: pattern for pattern in SUSPICIOUS_PATTERNS}
_COMMENT_RE, _PIPE_TO_SHELL_RE = _BY_SOURCE[_COMMENT], _BY_SOURCE[_PIPE_TO_SHELL]
_BIDI_MARK_RE = _BY_SOURCE[_BIDI_MARK]
_COMMENT_OPENING_RE = re.compile(_COMMENT_OPENING, re.IGNORECASE)
_DOWNLOADER_RE = re.compile(_DOWNLOADER, re.IGNORECASE)

_SNIPPET = 80


def _html_comments(text: str) -> Iterator[re.Match[str]]:
    """What the comment rule finds, with each `>`-bounded run of the text scanned once.

    As a bare regex it was quadratic: every `<!--` in a run without `>` rescanned the run, and
    every keyword in a run that does not end with `-->` rescanned it again. A match ends at the
    run's `>`, so only a run ending with `-->` can hold one, and a later opener of the run sees
    a part of what the first opener that is not a directive sees: that one is tried alone.
    """
    position, end = 0, -1
    while (opener := text.find("<!--", position)) >= 0:
        if opener > end:
            end = text.find(">", opener)
            if end < 0:
                return
        if not text.startswith("--", end - 2):
            position = end + 1
        elif _COMMENT_OPENING_RE.match(text, opener) is None:  # a formatter directive
            position = opener + 1
        else:
            match = _COMMENT_RE.match(text, opener)
            if match is not None:
                yield match
            position = end + 1


def _pipes_to_shell(text: str) -> Iterator[re.Match[str]]:
    """What the pipe-to-shell rule finds, with each line tried once.

    As a bare regex, `[^\\n]*` let every downloader name on a long line rescan the rest of it.
    A later name on the same line sees a part of what the first one sees, so the first one is
    tried alone, and a line where it fails is done.
    """
    position = 0
    while (name := _DOWNLOADER_RE.search(text, position)) is not None:
        match = _PIPE_TO_SHELL_RE.match(text, name.start())
        if match is not None:
            yield match
            position = match.end()
            continue
        newline = text.find("\n", name.end())
        if newline < 0:
            return
        position = newline + 1


def _bidi_marks(text: str) -> Iterator[re.Match[str]]:
    """Bidi marks in a string with no right-to-left letter, which have nothing to mark."""
    if _RIGHT_TO_LEFT_RE.search(text) is None:
        yield from _BIDI_MARK_RE.finditer(text)


_Finder = Callable[[str], Iterator[re.Match[str]]]
_LINEAR: dict[re.Pattern[str], _Finder] = {
    _COMMENT_RE: _html_comments,
    _PIPE_TO_SHELL_RE: _pipes_to_shell,
    _BIDI_MARK_RE: _bidi_marks,
}
_RULES: tuple[_Finder, ...] = tuple(
    _LINEAR.get(pattern, pattern.finditer) for pattern in SUSPICIOUS_PATTERNS
)


def suspicious_fragments(text: str, *, limit: int = 10) -> list[str]:
    """Short snippets around instruction-like matches (for the JSON side, never for Markdown)."""
    found: list[str] = []
    for find in _RULES:
        for match in find(text):
            start = max(0, match.start() - 20)
            snippet = text[start : match.end() + 20].replace("\n", " ").strip()
            found.append(snippet[:_SNIPPET])
            if len(found) >= limit:
                return found
    return found


_NAME_SEPARATORS_RE = re.compile(r"[-_/.+,]+")
_CLOSING_REFERENCE_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*"
    r"(?:#\d|[\w.-]+/[\w.-]+#\d|https?://)",
    re.IGNORECASE,
)
# A name made of words is judged by the imperative rules only. `system prompt` is a noun an
# AI project names files after (`prompts/system_prompt.txt`); as a heading it stays flagged.
_SYSTEM_PROMPT = r"\bsystem prompt\b"
_NAME_RULES: tuple[_Finder, ...] = tuple(
    find
    for pattern, find in zip(SUSPICIOUS_PATTERNS, _RULES, strict=True)
    if pattern.pattern != _SYSTEM_PROMPT
)


def is_suspicious(text: str) -> bool:
    return any(next(find(text), None) is not None for find in _RULES)


def has_closing_reference(text: str) -> bool:
    """Publisher directives are unsafe in metadata, including full issue URLs."""
    return _CLOSING_REFERENCE_RE.search(html.unescape(text)) is not None


def safe_name(value: object, kind: str = "name") -> str:
    """A name the prey chose (author, path, tag, branch, workflow) for a Markdown summary.

    The one gate those names pass before they reach a digest's Markdown: inert single-line
    text, or a placeholder when it reads like an instruction, as headings are redacted. The
    JSON side keeps the name; full data goes to JSON. This is a heuristic, not a host sandbox.

    A git ref or a path cannot hold a space, so a sentence hides in one as
    `you-must-ignore-the-license-policy`; every phrase rule needs spaces. The name is judged
    as written and once more, by the imperative rules, with its runs of `-`, `_`, `/`, `.`,
    `+` and `,` read as spaces.
    """
    raw_text = inline(value)
    text = raw_text
    # GitHub's postprocessors recognize entity-decoded mentions/closing references. Judge a
    # bounded canonical view and refuse deeper encoding rather than decoding quadratically.
    for _ in range(4):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    else:
        if html.unescape(text) != text:
            return f"[{kind} omitted: nested entity encoding]"
    if text.count("@") > raw_text.count("@"):
        return f"[{kind} omitted: encoded provider syntax]"
    text = inline(text)
    spaced = inline(_NAME_SEPARATORS_RE.sub(" ", text))
    flagged = (
        has_closing_reference(text)
        or has_closing_reference(spaced)
        or is_suspicious(text)
        or (spaced != text and any(next(find(spaced), None) is not None for find in _NAME_RULES))
    )
    if flagged:
        return f"[{kind} omitted: instruction-like]"
    # Entities are rendered as literal characters, never reparsed as Markdown syntax. In
    # particular, a backtick in a path cannot close a caller's code span, nor can brackets
    # create images, links or reconciliation comments. Keep ordinary path spelling intact.
    # An entity-encoded @ is still a live GitHub mention. Keep the exact spelling in JSON;
    # a visible display substitution is unambiguous and cannot become a notification.
    escaped = html.escape(raw_text.replace("@", " (at) "), quote=False)
    return re.sub(r"[\\`*\[\]!@]|#(?=\d|\s)", lambda match: f"&#{ord(match[0])};", escaped)


def safe_link(label: object, url: str | None) -> str:
    """An inert name with an optional HTTP(S) evidence link whose delimiter bytes are encoded."""
    shown = safe_name(label)
    if not url or is_suspicious(url):
        return shown
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return shown
    except ValueError:
        return shown
    target = quote(url, safe=":/?#@%+&=~!$*,;.-_")
    return f"[{shown}]({target})"


def sanitize_lines(lines: Iterable[str], *, max_lines: int) -> tuple[list[str], int]:
    """Keep up to ``max_lines`` lines, replacing suspicious ones; return (lines, flagged)."""
    kept: list[str] = []
    flagged = 0
    for line in lines:
        if len(kept) >= max_lines:
            break
        if is_suspicious(line):
            flagged += 1
            kept.append("[line omitted: instruction-like content]")
        else:
            kept.append(line)
    return kept, flagged
