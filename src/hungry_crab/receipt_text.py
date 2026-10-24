"""Producer prose is data, including GitHub markers, mentions and closing references."""

from __future__ import annotations

import html
import re


def inert_receipt_text(text: str) -> str:
    """Encode raw effect-bearing syntax while retaining the visible text."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"[\\`*\[\]!@#_~]", lambda match: f"&#{ord(match[0])};", escaped)


def quote_receipt_text(text: str) -> str:
    """Literal fenced data, with raw reconciliation comments disabled as a second boundary.

    GitHub recognizes closing references even inside a quote and after entity decoding.
    Code fences suppress that action; their length exceeds every backtick run in the data.
    Our own reconciler scans raw Markdown, so comments must be neutralized separately.
    """
    literal = text.replace("<!--", "< !--")
    longest = max((len(run) for run in re.findall(r"`+", literal)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{literal}\n{fence}"
