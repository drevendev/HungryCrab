from __future__ import annotations

from helpers import read_json, read_md

from hungry_crab.digest import DigestResult


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
