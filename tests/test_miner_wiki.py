from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.compare import CompareOptions, compare_digests
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.fetch.catch import catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.miners.ai_config import _headings
from hungry_crab.miners.docs import readme_outline
from hungry_crab.miners.wiki import markdown_headings


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    root = tmp_path / "wiki-source"
    shutil.copytree(Path(__file__).parent / "fixtures" / "wiki", root)
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
