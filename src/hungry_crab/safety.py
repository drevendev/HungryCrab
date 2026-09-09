"""Prey content is untrusted data.

READMEs, issues and code comments may carry instructions aimed at an agent. The miners never
copy body text into the Markdown summaries (only headings, names and counts), and they flag
instruction-like fragments so a skill can treat the file with extra suspicion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Invisible code points are a classic carrier for hidden instructions, and the Tags block is
# the one that carries whole sentences: it maps every printable ASCII character to a code
# point that renders as nothing, so an instruction can be appended to an innocent heading and
# read back by a model. Built with chr() so the source file itself stays free of them.
#
# Two families are deliberately absent. Variation selectors (U+FE00-FE0F) end almost every
# emoji in a README title, and the emoji joiners already here are load-bearing in Indic and
# Persian text; flagging U+FE0F would flag headings by the hundred. The soft hyphen (U+00AD)
# is real hyphenation in real documentation. A detector that fires on ordinary prose stops
# carrying information, which is the lesson the syrupy meal already taught this module.
_INVISIBLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x180E, 0x180E),  # Mongolian vowel separator
    (0x200B, 0x200D),  # zero-width space, non-joiner, joiner
    (0x2060, 0x2064),  # word joiner and the invisible mathematical operators
    (0xFEFF, 0xFEFF),  # byte-order mark
    (0xE0000, 0xE007F),  # Tags: printable ASCII rendered as nothing
)
_INVISIBLE = "".join(
    chr(low) if low == high else f"{chr(low)}-{chr(high)}" for low, high in _INVISIBLE_RANGES
)

SUSPICIOUS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        (
            r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) "
            r"(?:instructions|prompts|rules|messages)"
        ),
        r"disregard (?:all |any |the )?(?:previous|prior|above|earlier)",
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
        # An HTML comment is a good hiding place, but most of them in a real README are directives
        # to a formatter or a linter. `<!-- prettier-ignore-start -->` is not an attack.
        (
            r"<!--\s*(?!/?\s*(?:prettier|markdownlint|remark|eslint|stylelint|lint|nolint|noqa"
            r"|fmt|type|codecov|all-contributors|toc|editorconfig|doctoc|omit|badges)\b)"
            r"[^>]*?\b(?:instruction|assistant|claude|copilot|agent|ignore|must)\b[^>]*?-->"
        ),
        f"[{_INVISIBLE}]",
        (
            r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n]*\|\s*"
            r"(?:sh|bash|zsh|python\d?|powershell|pwsh|iex)\b"
        ),
    )
)

_SNIPPET = 80


def suspicious_fragments(text: str, *, limit: int = 10) -> list[str]:
    """Short snippets around instruction-like matches (for the JSON side, never for Markdown)."""
    found: list[str] = []
    for pattern in SUSPICIOUS_PATTERNS:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 20)
            snippet = text[start : match.end() + 20].replace("\n", " ").strip()
            found.append(snippet[:_SNIPPET])
            if len(found) >= limit:
                return found
    return found


def is_suspicious(text: str) -> bool:
    return any(pattern.search(text) for pattern in SUSPICIOUS_PATTERNS)


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
