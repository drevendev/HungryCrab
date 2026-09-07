"""The plugin, skill, agent and command files must stay well-formed."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from hungry_crab import __version__

ROOT = Path(__file__).resolve().parents[1]
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _frontmatter(path: Path) -> dict[str, str]:
    match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    assert match, f"{path} has no frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_plugin_manifest_and_marketplace_agree() -> None:
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert plugin["name"] == "crab"
    assert plugin["license"] == "MIT"
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    assert set(entries) == {"crab"}
    assert entries["crab"]["source"] == "./"
    assert entries["crab"]["version"] == plugin["version"]
    # The plugin version once stayed at 0.2.0 while the CLI moved on, so every agent was told
    # it was up to date while its skills still said --host. Cutting a release moves both.
    assert plugin["version"] == __version__.replace(".dev", "-dev."), (
        "the plugin ships the CLI's skills, so it carries the CLI's version"
    )
    assert marketplace["name"] == "hungry-crab"


@pytest.mark.parametrize("skill", ["eat", "license", "serve"])
def test_skills_have_matching_names_and_descriptions(skill: str) -> None:
    fields = _frontmatter(ROOT / "skills" / skill / "SKILL.md")
    assert fields["name"] == skill
    assert len(fields["description"]) > 60
    assert "Use when" in fields["description"]


def test_skill_references_exist() -> None:
    assert (ROOT / "skills" / "eat" / "references" / "categories.md").is_file()
    assert (ROOT / "skills" / "license" / "references" / "matrix.md").is_file()
    assert (ROOT / "skills" / "serve" / "references" / "issue-template.md").is_file()


@pytest.mark.parametrize("agent", ["crab-historian", "crab-architect"])
def test_agents_are_read_only_and_named(agent: str) -> None:
    fields = _frontmatter(ROOT / "agents" / f"{agent}.md")
    assert fields["name"] == agent
    tools = {tool.strip() for tool in fields["tools"].split(",")}
    assert {"Read", "Grep", "Glob"} <= tools
    assert not tools & {"Write", "Edit", "NotebookEdit"}
    text = (ROOT / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    assert "untrusted" in text


@pytest.mark.parametrize("command", ["sniff", "menu"])
def test_commands_carry_descriptions(command: str) -> None:
    fields = _frontmatter(ROOT / "commands" / f"{command}.md")
    assert fields["description"]
    assert "$ARGUMENTS" in (ROOT / "commands" / f"{command}.md").read_text(encoding="utf-8")


def test_skill_protocol_mentions_every_cli_step() -> None:
    text = (ROOT / "skills" / "eat" / "SKILL.md").read_text(encoding="utf-8")
    for command in ("crab sniff", "crab compare", "crab serve", "crab ledger mark", "crab tune"):
        assert command in text
    assert "untrusted" in text and "dry-run" in text


def test_the_packaging_version_and_the_importable_one_agree() -> None:
    """`pyproject.toml` was the one version file nothing checked.

    `__version__` is a literal in `src/hungry_crab/__init__.py` and `[project].version` is a
    separate literal, with no `importlib.metadata` between them. `crab update` reads one from
    each side — `fetch_remote` takes master's `pyproject.toml`, `check_cli` takes the installed
    `__version__` — so a release that bumps one and forgets the other makes it compare two
    different numbers indefinitely, reporting `master is at X` to a crab that is already current.
    The commit comparison added later does not rescue that: it only runs when the two version
    strings are equal, which is exactly what a drift removes.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


def test_master_carries_a_development_version() -> None:
    """A released version string on `master` cannot identify the commit that produced it.

    0.2.2 sat in every version file for seven commits after the release that never happened, and
    `manifest.json` records `crab_version` as part of the digest cache's reuse key — so two crabs
    that behave differently looked identical to it. The release procedure in `CONTRIBUTING.md`
    ends by reopening the next `.dev0`; this is what notices when that step is skipped.
    """
    assert ".dev" in __version__, (
        f"master is on {__version__}, which is a release version. A release tags the commit and "
        "then reopens the next X.Y.Z.dev0 — see the Releasing section in CONTRIBUTING.md."
    )


def test_every_changelog_version_has_a_link_reference() -> None:
    """A heading with no reference, or a reference with no heading, means the bookkeeping slipped.

    Both link references at the bottom of the file once pointed at `v0.2.2`, a tag that was never
    created, so two of the three were 404s.
    """
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^## \[([^\]]+)\]", text, re.MULTILINE))
    references = set(re.findall(r"^\[([^\]]+)\]: https://", text, re.MULTILINE))
    assert "Unreleased" in headings
    assert headings == references, (
        f"headings without a reference: {sorted(headings - references)}; "
        f"references without a heading: {sorted(references - headings)}"
    )
