from __future__ import annotations

from pathlib import Path

from hungry_crab.digest import _reading_order, run_miners
from hungry_crab.mdutil import MdDoc
from hungry_crab.miners import MineContext, MinerResult
from hungry_crab.tokens import estimate_tokens


class _DocMiner:
    name = "docs"
    requires: tuple[str, ...] = ()
    json_file = None
    md_file = "docs.md"

    def __init__(self, lines: int) -> None:
        self.lines = lines

    def run(self, ctx: MineContext) -> MinerResult:
        doc = MdDoc("Docs", source=ctx.source_line())
        section = doc.section("Entries", priority=7)
        for index in range(self.lines):
            section.line(f"entry {index:03d} " + "x" * 36)
        return MinerResult(self.name, data={}, doc=doc)


def test_render_pages_is_lossless_bounded_and_non_mutating() -> None:
    doc = MdDoc("Budget", source="Source line")
    important = doc.section("Important", priority=1)
    for index in range(5):
        important.line(f"important {index}")
    filler = doc.section("Filler", priority=9)
    for index in range(80):
        filler.line(f"filler {index:03d} " + "x" * 40)

    before = doc.render()
    pages = doc.render_pages(120, "history.md")
    joined = "\n".join(page.text for page in pages)

    assert len(pages) > 1
    assert all(estimate_tokens(page.text) <= 120 for page in pages)
    assert "> Next: `history.2.md`" in pages[0].text
    assert all(page.text.startswith("# Budget\n\n> Source line\n") for page in pages)
    assert pages[0].priority == 1
    assert pages[-1].priority == 9
    for index in range(5):
        assert joined.count(f"important {index}") == 1
    for index in range(80):
        assert joined.count(f"filler {index:03d} " + "x" * 40) == 1
    assert doc.render() == before


def test_render_pages_splits_one_oversize_generated_line_without_dropping_characters() -> None:
    doc = MdDoc("Long")
    section = doc.section("Payload")
    section.line("z" * 2_000)

    pages = doc.render_pages(100, "docs.md")

    assert len(pages) > 1
    assert all(estimate_tokens(page.text) <= 100 for page in pages)
    assert sum(page.text.count("z") for page in pages) == 2_000


def test_run_miners_owns_page_family_and_removes_stale_siblings(tmp_path: Path) -> None:
    out_dir = tmp_path / "digest"
    out_dir.mkdir()
    ctx = MineContext(root=tmp_path, sha="a" * 40, ref="main", label="fixture", md_budget=90)

    first = run_miners(ctx, [_DocMiner(60)], out_dir)
    first_files = first[0]["files"]
    assert first_files[0] == "docs.md"
    assert len(first_files) > 1
    assert first[0]["page_priorities"] == {name: 7 for name in first_files}
    assert all((out_dir / name).is_file() for name in first_files)
    assert _reading_order(set(first_files)) == first_files

    ctx.md_budget = 500
    second = run_miners(ctx, [_DocMiner(1)], out_dir)

    assert second[0]["files"] == ["docs.md"]
    assert not any(path.name.startswith("docs.2") for path in out_dir.iterdir())
