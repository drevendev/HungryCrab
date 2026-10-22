"""A heading reaches a summary as text: markup is removed, code spans and comparisons are kept."""

from __future__ import annotations

import time

import pytest

from hungry_crab.miners.wiki import plain_heading


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        # This repository's own heading in docs/design/01-concept-and-skill.md.
        ("`/crab:eat <prey> [--maw .]`", "`/crab:eat <prey> [--maw .]`"),
        ("Use `Vec<T>` here", "Use `Vec<T>` here"),
        ("``a ` b``", "``a ` b``"),
        ("when a <= b", "when a &lt;= b"),
        ("a < b > c", "a < b > c"),
        ('Install <img src="https://tracker.example/pixel.png">', "Install"),
        ("Usage [docs](https://evil.example/landing)", "Usage docs"),
        ("Safe <b>bold</b>", "Safe bold"),
        ("<https://evil.example>", ""),
        ("[label]: https://evil.example", "label: https://evil.example"),
        # An escaped backtick is text: what follows it is not a code span (re-review N5).
        ("Install \\`<img src=x onerror=alert(1)>`", "Install \\``"),
        ("See \\`[docs](https://evil.example)`", "See \\`docs`"),
        ("Keep \\\\`code <b>`", "Keep \\\\`code <b>`"),
    ],
)
def test_plain_heading(raw: str, text: str) -> None:
    assert plain_heading(raw) == text


def test_code_span_matching_is_linear() -> None:
    hostile = "`" + " ``" * 100_000 + "".join("`" * n + " " for n in range(1, 400))
    started = time.perf_counter()
    plain_heading(hostile)
    assert time.perf_counter() - started < 1.0
