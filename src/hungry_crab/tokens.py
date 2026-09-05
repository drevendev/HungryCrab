"""Token estimation for the digest budget.

No tokenizer dependency: the digest only needs a stable estimate to keep every section within
budget. Markdown tables and code-like text sit around 3.5 characters per token, which is what
this module assumes; the manifest reports the estimate so a skill can plan its reading.
"""

from __future__ import annotations

import math

CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))
