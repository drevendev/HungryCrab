"""The plugin, skill, agent and command files must stay well-formed."""

from __future__ import annotations

import json
import re
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
    # The plugin version stayed at 0.2.0 while the CLI moved to 0.3.0.dev0, so every agent
    # was told it was up to date while its skills still said --host.
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
