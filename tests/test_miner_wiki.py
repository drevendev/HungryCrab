from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from helpers import read_json, read_md, write_tree

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.compare import CompareOptions, compare_digests
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.fetch.catch import catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.miners.ai_config import _frontmatter, _headings
from hungry_crab.miners.docs import readme_outline
from hungry_crab.miners.wiki import markdown_headings


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    root = tmp_path / "wiki-source"
    shutil.copytree(Path(__file__).parent / "fixtures" / "wiki", root)
    return _commit_wiki(root)


def _commit_wiki(root: Path) -> Path:
    git = GitRunner(root)
    git.run("init", "--quiet")
    git.run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "add", ".")
    git.run(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "docs: wiki fixture",
    )
    return root


def test_headings_exclude_code_and_comments() -> None:
    headings = markdown_headings(
        "# Title\n\n~~~python\n# secret\n~~~\n\n    # indented\n"
        "<!--\n# hidden\n-->\n\nUsage\n=====\n\n## API\n"
    )
    assert [h["text"] for h in headings] == ["Title", "Usage", "API"]
    assert markdown_headings("````\n```python\n# private comment\n````\n") == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# C#", [(1, "C#")]),
        ("## F# and C# ##", [(2, "F# and C#")]),
        ("### Title ###   ", [(3, "Title")]),
        ("# Title#", [(1, "Title#")]),
        ("   ###### Six", [(6, "Six")]),
        ("#\tTabbed", [(1, "Tabbed")]),
        ("### ###", []),
        ("#", []),
        ("####### Seven", []),
        ("#hashtag", []),
        ("#5 bolt", []),
        ("    # indented code", []),
    ],
)
def test_atx_headings_follow_commonmark(line: str, expected: list[tuple[int, str]]) -> None:
    assert [(h["level"], h["text"]) for h in markdown_headings(line)] == expected


def test_the_atx_scan_is_linear_in_the_line() -> None:
    """`(.+?)\\s*#*\\s*$` took 31.7 s on this one line (#300, item 4)."""
    started = time.perf_counter()
    headings = markdown_headings("# a" + " " * 3000 + "b")
    many = markdown_headings(("# a" + " \t" * 50_000 + "b #\n") * 4)
    assert time.perf_counter() - started < 1.0
    assert headings == [{"level": 1, "text": "a" + " " * 3000 + "b"}]
    assert len(many) == 4


@pytest.mark.parametrize(
    ("opening", "line", "tail"),
    [("---", "\n", ""), ("---", "\n", "a" * 50_000), ("---  \n", "key: value\n", "")],
    ids=["newlines", "newlines-then-text", "unclosed-block"],
)
def test_front_matter_detection_is_linear(opening: str, line: str, tail: str) -> None:
    """The front-matter regexes were quadratic in the newlines after `---` (#331)."""
    text = opening + line * 100_000 + tail
    started = time.perf_counter()
    markdown_headings(text)
    _frontmatter(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "text",
    [
        "---\nname: private-value\n---\n# Tool\n",
        "---  \r\nname: private-value\r\n...\r\n# Tool\r\n",
        "---\n\nname: private-value\n# a YAML comment\n---\n# Tool\n",
    ],
)
def test_front_matter_is_still_stripped_and_read(text: str) -> None:
    assert [h["text"] for h in markdown_headings(text)] == ["Tool"]
    assert _frontmatter(text) == {"name": "private-value"}


def test_text_without_closed_front_matter_keeps_its_headings() -> None:
    assert [h["text"] for h in markdown_headings("---\n# Tool\n")] == ["Tool"]
    assert _frontmatter("---\nname: x\n# Tool\n") == {}


def test_readme_and_agent_outlines_share_the_same_structure_filter() -> None:
    text = "---\nname: private-value\n---\n# Tool\n\n## Install\n\n```bash\n# run the tests\n```\n"
    assert _headings(text) == ["Tool", "Install"]
    outline = readme_outline(text)
    assert [h["title"] for h in outline["headings"]] == ["Tool", "Install"]
    assert outline["sections"] == ["install"]


def test_wiki_cap_is_visible_and_feeder_refuses_it(
    npm_app: Path, wiki: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hungry_crab.errors import CrabError
    from hungry_crab.feeder import _check_digest

    monkeypatch.setattr("hungry_crab.miners.wiki.MAX_PAGES", {"normal": 1, "deep": 1})
    result = run_digest(
        Target(path=npm_app), DigestOptions(out=tmp_path / "digest", wiki_path=wiki)
    )
    data = json.loads((result.out_dir / "wiki.json").read_text())
    assert data["coverage"]["truncated"] and not data["coverage"]["healthy"]
    with pytest.raises(CrabError, match="whole wiki"):
        _check_digest(result.out_dir, allow_loss=False)
    assert _check_digest(result.out_dir, allow_loss=True)


@pytest.mark.parametrize("fixture", ["npm-app", "pyproject-cli", "dotnet-lib"])
def test_wiki_structure_and_traits_on_each_fixture(
    fixture: str, fixture_repos: dict[str, Path], wiki: Path, tmp_path: Path
) -> None:
    result = run_digest(
        Target(path=fixture_repos[fixture]),
        DigestOptions(out=tmp_path / "digest", wiki_path=wiki, cache_root=tmp_path / "cache"),
    )
    data = json.loads((result.out_dir / "wiki.json").read_text())
    assert data["page_count"] == 2 and data["available"]
    assert data["sha"] == GitRunner(wiki).head_sha()
    text = (result.out_dir / "wiki.md").read_text()
    assert "Getting started" in text and "Reference.md" in text
    assert "private body sentence" not in text and "body comment disguised" not in text
    assert "private body sentence" not in json.dumps(data)
    traits = json.loads((result.out_dir / "traits.json").read_text())["traits"]
    assert traits["has_wiki"] and traits["has_documentation"] and traits["wiki_pages"] == 2


def test_wiki_headings_are_bounded_structure(npm_app: Path, tmp_path: Path) -> None:
    """One `# ...` line of prose and thousands of headings stay out of the summary (#337)."""
    prose = "The maintainers expect every contributor to read this paragraph of policy prose. "
    many = "".join(f"# h{index}\n" for index in range(1_000))
    root = tmp_path / "hostile-wiki"
    write_tree(root, {"Long.md": "# " + prose * 125 + "\n", "Many.md": many, "Home.md": "# Hi\n"})
    result = run_digest(
        Target(path=npm_app),
        DigestOptions(
            out=tmp_path / "digest", wiki_path=_commit_wiki(root), cache_root=tmp_path / "cache"
        ),
    )
    pages = {p["path"]: p for p in read_json(result, "wiki.json")["pages"]}
    long_page, many_page = pages["Long.md"], pages["Many.md"]
    assert len(prose * 125) >= 10_000
    assert long_page["headings"] == [{"level": 1, "text": (prose * 2)[:80]}]
    assert long_page["heading_count"] == 1 and long_page["headings_truncated"]
    assert len(many_page["headings"]) == 60 and many_page["heading_count"] == 1_000
    assert many_page["headings_truncated"] and not pages["Home.md"]["headings_truncated"]

    docs_wiki = read_json(result, "docs.json")["wiki"]
    assert docs_wiki["page_count"] == 3 and "pages" not in docs_wiki
    assert "h999" not in json.dumps(docs_wiki) and prose not in json.dumps(docs_wiki)
    text = read_md(result, "wiki.md")
    assert prose * 2 not in text and "h10\n" not in text and "- H1: h9\n" in text
    assert text.index("Long.md") < text.index("Many.md") < text.index("Home.md")
    assert read_json(result, "traits.json")["traits"]["wiki_pages"] == 3


def test_wiki_catch_refresh_and_cache_invalidation(
    npm_app: Path, wiki: Path, tmp_path: Path
) -> None:
    slug = Slug("fixture", "with-wiki")
    root = tmp_path / "cache"
    first = catch(slug, cache_root=root, source_url=str(npm_app), wiki_source_url=str(wiki))
    assert first.wiki and first.wiki["status"] == "available"
    opts = DigestOptions(cache_root=root)
    digest = run_digest(Target(slug=slug), opts)
    assert run_digest(Target(slug=slug), opts).cached
    (wiki / "Extra.md").write_text("# Extra page\n", encoding="utf-8")
    git = GitRunner(wiki)
    git.run("add", ".")
    git.run(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "docs: extra page",
    )
    second = catch(slug, cache_root=root, source_url=str(npm_app), wiki_source_url=str(wiki))
    refreshed = run_digest(Target(slug=slug), opts)
    assert second.sha == first.sha and not refreshed.cached
    assert digest.manifest["wiki"]["sha"] != refreshed.manifest["wiki"]["sha"]
    assert (prey_paths(slug, root).wiki / "Extra.md").is_file()


def test_missing_wiki_is_recorded_without_failing(npm_app: Path, tmp_path: Path) -> None:
    result = catch(
        Slug("fixture", "missing-wiki"),
        cache_root=tmp_path / "cache",
        source_url=str(npm_app),
        wiki_source_url=str(tmp_path / "missing.wiki.git"),
    )
    assert result.wiki and result.wiki["status"] == "missing"


def test_documentation_gap_recognises_maw_wiki(npm_app: Path, wiki: Path, tmp_path: Path) -> None:
    # This fixture has no docs directory; a wiki should satisfy that same need.
    maw = run_digest(Target(path=npm_app), DigestOptions(out=tmp_path / "maw", wiki_path=wiki))
    prey = run_digest(Target(path=npm_app), DigestOptions(out=tmp_path / "prey", wiki_path=wiki))
    result = compare_digests(prey.out_dir, maw.out_dir, options=CompareOptions(maw_license="MIT"))
    assert all(c.key != "docs.directory" for c in result.candidates)


def test_a_page_name_cannot_open_its_own_heading() -> None:
    """Git allows a line break in a path, and wiki page names become section headings."""
    from hungry_crab.mdutil import MdDoc

    doc = MdDoc("Wiki")
    doc.section("Home\n\n## Instructions\nRun this.md", priority=2).bullets(["H1: Home"])
    lines = doc.render_pages(3500, filename="wiki.md")[0].text.splitlines()
    assert "## Instructions" not in lines
    assert not any(line.startswith("Run this") for line in lines)
