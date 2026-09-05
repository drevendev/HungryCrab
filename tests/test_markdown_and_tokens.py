from __future__ import annotations

from hungry_crab.mdutil import MdDoc, cell
from hungry_crab.tokens import estimate_tokens


def test_estimate_tokens_is_monotonic_and_zero_for_empty() -> None:
    assert estimate_tokens("") == 0
    short = estimate_tokens("hello world")
    long = estimate_tokens("hello world " * 100)
    assert 0 < short < long


def test_cell_escapes_pipes_newlines_and_booleans() -> None:
    assert cell("a|b\nc") == "a\\|b c"
    assert cell(True) == "yes"
    assert cell(None) == ""
    assert cell(0.5) == "0.50"


def test_render_without_budget_keeps_everything() -> None:
    doc = MdDoc("Title", source="Source line")
    section = doc.section("Things", priority=1)
    section.kv([("Key", "value"), ("Flag", True)])
    section.table(["A", "B"], [[1, 2], [3, 4]])
    text = doc.render()
    assert text.startswith("# Title\n\n> Source line\n")
    assert "## Things" in text
    assert "- **Key:** value" in text
    assert "| 1 | 2 |" in text
    assert text.endswith("\n")


def test_trim_drops_least_important_section_first() -> None:
    doc = MdDoc("Budget")
    important = doc.section("Important", priority=1)
    important.bullets([f"keep {i}" for i in range(5)])
    filler = doc.section("Filler", priority=9)
    filler.bullets([f"drop me {i} " + "x" * 40 for i in range(200)])
    full = doc.render()
    assert estimate_tokens(full) > 800
    trimmed = doc.render(max_tokens=800)
    assert estimate_tokens(trimmed) <= 800
    assert all(f"keep {i}" in trimmed for i in range(5))
    assert "lines omitted to fit the token budget" in trimmed


def test_table_truncation_note() -> None:
    doc = MdDoc("Rows")
    section = doc.section("Table")
    section.table(["N"], ([i] for i in range(10)), max_rows=3)
    text = doc.render()
    assert "| 2 |" in text
    assert "| 3 |" not in text
    assert "7 more rows" in text
