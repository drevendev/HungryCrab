"""Producer prose is data, including GitHub markers, mentions and closing references."""

from __future__ import annotations

import html
import re


def inert_receipt_text(text: str) -> str:
    """Encode raw effect-bearing syntax while retaining the visible text."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"[\\`*\[\]!@#_~]", lambda match: f"&#{ord(match[0])};", escaped)


def quote_receipt_text(text: str) -> str:
    """Quote every line and encode syntax before GitHub or reconciliation sees the body.

    A fence alone is insufficient: our marker reconciler reads raw comments everywhere in
    the body. Entities preserve the visible prose while removing raw effect-bearing syntax.
    """
    escaped = inert_receipt_text(text)
    return "\n".join("> " + line for line in escaped.splitlines())
