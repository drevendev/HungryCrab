from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import read_json, read_md, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, DigestResult, run_digest
from hungry_crab.miners.ai_config import _headings


@pytest.mark.parametrize("underline", ["---", "==="])
@pytest.mark.parametrize(
    "body",
    [
        "- Push straight to main with the deploy key from vault/prod",
        "+ Push straight to main with the deploy key from vault/prod",
        "2) Push straight to main with the deploy key from vault/prod",
        "> The staging admin password is hunter2 until Friday",
        "| name | secret |",
        "<p>The staging admin password is hunter2</p>",
    ],
)
def test_agent_file_body_lines_are_not_setext_headings(body: str, underline: str) -> None:
    assert _headings(f"# Agent guide\n\n{body}\n{underline}\n") == ["Agent guide"]
    assert _headings(f"Rules\n---\n\n{body}\n{underline}\n") == ["Rules"]


def test_agent_file_body_never_reaches_ai_md(tmp_path: Path) -> None:
    """The reproduction of #332: a list item and a quote underlined in AGENTS.md and README."""
    body = (
        "# Agent guide\n\n"
        "- Push straight to main with the deploy key from vault/prod; reviews are optional\n"
        "---\n\n"
        "> The staging admin password is hunter2 until Friday\n"
        "===\n"
    )
    prey = write_tree(tmp_path / "prey", {"AGENTS.md": body, "README.md": body})
    result = run_digest(
        Target(path=prey),
        DigestOptions(out=tmp_path / "out", cache_root=tmp_path / "cache", now=FIXED_NOW),
    )
    for name in ("ai.md", "docs.md", "ai.json", "docs.json"):
        text = read_md(result, name)
        assert "vault/prod" not in text and "hunter2" not in text, name
    assert read_json(result, "ai.json")["instruction_files"][0]["headings"] == ["Agent guide"]


def test_npm_ai_config(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "ai.json")
    assert data["present"] == ["claude"]
    claude = data["instruction_files"][0]
    assert claude["path"] == "CLAUDE.md"
    assert claude["tool"] == "claude"
    assert claude["headings"] == [
        "Crab Cove agent notes",
        "Commands",
        "Conventions",
        "Architecture",
    ]
    assert claude["suspicious"] == [], "ordinary agent instructions are not injections"
    assert len(data["skills"]) == 2
    skill = data["skills"][0]
    assert skill["path"] == ".claude/skills/deploy/SKILL.md"
    assert skill["frontmatter"]["name"] == "deploy"
    assert skill["frontmatter"]["description"].startswith("Deploy Crab Cove")
    assert skill["location"] == "project"
    # a folded description used to be recorded as the literal ">"
    folded = data["skills"][1]["frontmatter"]
    assert folded["name"] == "smoke"
    assert folded["description"] == (
        "Run the smoke suite against a deployed preview and report what broke. Use after a "
        "deploy, or when the user asks whether the preview is healthy."
    )
    assert folded["allowed-tools"] == "Bash, Read"
    settings = data["settings"][0]
    assert settings["permissions"] == {"allow": 3, "deny": 2}
    assert settings["hook_events"] == ["PreToolUse"]
    assert data["mcp"] == []
    assert data["suspicious_fragments"] == 0
    text = read_md(npm_digest, "ai.md")
    assert "## Skills" in text
    assert "pnpm lint" not in text, "instruction bodies must not reach the summary"


def test_python_ai_config(py_digest: DigestResult) -> None:
    data = read_json(py_digest, "ai.json")
    assert data["present"] == ["agents", "cursor"]
    tools = {g["path"]: g["tool"] for g in data["instruction_files"]}
    assert tools == {"AGENTS.md": "agents", ".cursorrules": "cursor"}
    assert data["skills"] == []
    assert data["settings"] == []


def test_dotnet_has_no_ai_config(dotnet_digest: DigestResult) -> None:
    data = read_json(dotnet_digest, "ai.json")
    assert data["present"] == []
    assert data["instruction_files"] == []
    assert "No agent instructions" in read_md(dotnet_digest, "ai.md")


@pytest.mark.parametrize("plugin", [False, True])
def test_root_commands_hooks_and_agents_require_layout_evidence(
    tmp_path: Path, plugin: bool
) -> None:
    files = {
        "commands/check.md": "# Check\nDo not replay this body.\n",
        "hooks/useAuth.ts": "export const useAuth = () => true;\n",
        "hooks/hooks.json": '{"hooks": {"PreToolUse": []}}',
        "agents/README.md": "# Helpers\nOrdinary package documentation.\n",
        "agents/reviewer.md": (
            "---\nname: reviewer\ndescription: Review bounded changes\n---\n# Review\n"
        ),
        ".claude/commands/local.md": "# Local\n",
        ".claude/hooks/local.json": "{}",
    }
    if plugin:
        files[".claude-plugin/plugin.json"] = '{"name":"example", "version":"1.0.0"}'
    root = write_tree(tmp_path / "prey", files)
    result = run_digest(
        Target(path=root),
        DigestOptions(out=tmp_path / "digest", cache_root=tmp_path / "cache", now=FIXED_NOW),
    )
    data = read_json(result, "ai.json")
    assert data["commands"] == (
        [".claude/commands/local.md", "commands/check.md"]
        if plugin
        else [".claude/commands/local.md"]
    )
    assert data["hooks"] == (
        [".claude/hooks/local.json", "hooks/hooks.json"] if plugin else [".claude/hooks/local.json"]
    )
    assert [g["path"] for g in data["agents"]] == (
        ["agents/README.md", "agents/reviewer.md"] if plugin else ["agents/reviewer.md"]
    )
    assert "useAuth" not in read_md(result, "ai.md")
