"""Platform labels describe configuration evidence, not ownership of portable skills."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hungry_crab.miners.ai_config import AiConfigMiner
from hungry_crab.miners.base import FileInfo, MineContext, MinerResult

_SKILL = "---\nname: review\ndescription: Review a change.\n---\n# Review\nBODY_SENTINEL\n"


def _context(tmp_path: Path, content: dict[str, str]) -> MineContext:
    files = []
    for rel, text in content.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        files.append(
            FileInfo(
                path=rel,
                name=path.name,
                ext=path.suffix,
                size=len(text.encode("utf-8")),
                language=None,
                is_code=False,
                vendored=False,
                generated=False,
                binary=False,
                loc=len(text.splitlines()),
                depth=len(Path(rel).parts) - 1,
                lockfile=False,
                manifest_kind=None,
            )
        )
    return MineContext(
        root=tmp_path,
        sha="a" * 40,
        ref="main",
        label="synthetic/skills",
        results={"inventory": MinerResult("inventory", {}, extra={"files": files})},
    )


def _markdown(result: MinerResult) -> str:
    assert result.doc is not None
    return result.doc.render()


@pytest.mark.parametrize(
    ("path", "collection"),
    [
        ("skills/review/SKILL.md", "skills"),
        (".agents/skills/review/SKILL.md", "skills"),
        ("docs/example/SKILL.md", "skills"),
        ("agents/reviewer.md", "agents"),
        ("hooks/preflight.py", "hooks"),
    ],
)
def test_portable_content_does_not_imply_claude(tmp_path: Path, path: str, collection: str) -> None:
    result = AiConfigMiner().run(_context(tmp_path, {path: _SKILL}))
    assert result.data["present"] == []
    assert len(result.data[collection]) == 1
    text = _markdown(result)
    assert "Portable skills, subagents or hook files found" in text
    assert "No agent instructions, skills" not in text
    assert "BODY_SENTINEL" not in text


@pytest.mark.parametrize(
    "path",
    [
        "CLAUDE.md",
        ".claude/skills/review/SKILL.md",
        ".claude/agents/reviewer.md",
        ".claude/commands/review.md",
        ".claude/hooks/preflight.py",
        ".claude/settings.json",
    ],
)
def test_claude_scoped_configuration_still_identifies_claude(tmp_path: Path, path: str) -> None:
    content = "{}" if path.endswith("json") else _SKILL
    result = AiConfigMiner().run(_context(tmp_path, {path: content}))
    assert result.data["present"] == ["claude"]


@pytest.mark.parametrize(
    ("path", "tool"),
    [
        (".claude-plugin/plugin.json", "claude"),
        (".claude-plugin/marketplace.json", "claude"),
        (".codex-plugin/plugin.json", "codex"),
        (".cursor-plugin/plugin.json", "cursor"),
        (".cursor-plugin/marketplace.json", "cursor"),
    ],
)
def test_native_plugin_manifests_have_explicit_platform_evidence(
    tmp_path: Path, path: str, tool: str
) -> None:
    manifest = {"name": "review", "version": "1.0.0", "skills": "./skills/"}
    result = AiConfigMiner().run(_context(tmp_path, {path: json.dumps(manifest)}))
    assert result.data["present"] == [tool]
    assert result.data["plugin"] == [
        {
            "path": path,
            "tool": tool,
            "parse_error": False,
            "name": "review",
            "version": "1.0.0",
            "keys": ["name", "skills", "version"],
        }
    ]
    text = _markdown(result)
    assert "## Plugin manifests" in text
    assert f"| {path} | {tool} | parsed JSON |" in text
    assert "not evidence that a plugin was installed or executed" in text


def test_mixed_platform_collection_is_deterministic(tmp_path: Path) -> None:
    content = {
        "skills/zeta/SKILL.md": _SKILL,
        ".cursor-plugin/plugin.json": "{}",
        "skills/alpha/SKILL.md": _SKILL,
        ".codex-plugin/plugin.json": "{}",
        ".claude-plugin/plugin.json": "{}",
        "agents/reviewer.md": _SKILL,
    }
    ctx = _context(tmp_path, content)
    first = AiConfigMiner().run(ctx)
    ctx.files().reverse()
    second = AiConfigMiner().run(ctx)
    assert first.data == second.data
    assert _markdown(first) == _markdown(second)
    assert first.data["present"] == ["claude", "codex", "cursor"]
    assert [s["path"] for s in first.data["skills"]] == [
        "skills/alpha/SKILL.md",
        "skills/zeta/SKILL.md",
    ]
    assert len(first.data["agents"]) == 1
    assert "BODY_SENTINEL" not in _markdown(first)


@pytest.mark.parametrize("content", ["{broken", "[]", "null", '"text"'])
def test_invalid_manifest_does_not_claim_successful_metadata(tmp_path: Path, content: str) -> None:
    path = ".codex-plugin/plugin.json"
    result = AiConfigMiner().run(_context(tmp_path, {path: content}))
    assert result.data["present"] == ["codex"]
    gist = result.data["plugin"][0]
    assert gist["parse_error"] is True
    assert gist["name"] is None
    assert gist["version"] is None
    assert gist["keys"] == []
    assert f"| {path} | codex | invalid JSON |" in _markdown(result)


@pytest.mark.parametrize(
    "path",
    [
        "plugin.json",
        "nested/.codex-plugin/plugin.json",
        ".codex-plugin-copy/plugin.json",
        ".cursor-plugin/example.json",
    ],
)
def test_unrecognized_manifest_paths_do_not_identify_a_platform(tmp_path: Path, path: str) -> None:
    result = AiConfigMiner().run(_context(tmp_path, {path: '{"name":"claude"}'}))
    assert result.data["present"] == []
    assert result.data["plugin"] == []


@pytest.mark.parametrize("excluded", ["vendored", "generated"])
def test_excluded_configuration_is_not_platform_evidence(tmp_path: Path, excluded: str) -> None:
    ctx = _context(
        tmp_path,
        {
            ".codex-plugin/plugin.json": "{}",
            ".claude/skills/review/SKILL.md": _SKILL,
        },
    )
    for info in ctx.files():
        setattr(info, excluded, True)
    result = AiConfigMiner().run(ctx)
    assert result.data["present"] == []
    assert result.data["plugin"] == []
    assert result.data["skills"] == []


def test_manifest_metadata_stays_bounded_and_does_not_replay_payloads(tmp_path: Path) -> None:
    manifest = {
        "name": "  review\n" + "x" * 1000,
        "version": {"unexpected": "BODY_SENTINEL"},
        "description": "BODY_SENTINEL",
        "hooks": {"command": "BODY_SENTINEL"},
    }
    result = AiConfigMiner().run(
        _context(tmp_path, {".cursor-plugin/plugin.json": json.dumps(manifest)})
    )
    gist = result.data["plugin"][0]
    assert len(gist["name"]) == 160
    assert "\n" not in gist["name"]
    assert gist["version"] is None
    assert "BODY_SENTINEL" not in json.dumps(result.data)
    assert "BODY_SENTINEL" not in _markdown(result)


def test_empty_configuration_keeps_the_existing_summary(tmp_path: Path) -> None:
    result = AiConfigMiner().run(_context(tmp_path, {}))
    assert result.data["present"] == []
    assert "No agent instructions, skills or MCP configuration found." in _markdown(result)


def test_existing_agent_cursor_codex_and_mcp_markers_are_preserved(tmp_path: Path) -> None:
    result = AiConfigMiner().run(
        _context(
            tmp_path,
            {
                "AGENTS.md": _SKILL,
                ".cursor/rules/review.mdc": _SKILL,
                ".codex/config.toml": 'model = "example"',
                ".mcp.json": '{"mcpServers": {}}',
            },
        )
    )
    assert result.data["present"] == ["agents", "codex", "cursor", "mcp"]
    assert result.data["plugin"] == []
