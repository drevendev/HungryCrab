"""AI-config miner: agent instructions, skills, subagents, hooks and MCP configuration.

The gist of each file is structural (headings, names, key counts). The body of an instruction
file is exactly the kind of text that must never be replayed to the model as if it were ours.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..mdutil import MdDoc
from ..safety import is_suspicious, suspicious_fragments
from ..typeutil import as_dict
from .base import FileInfo, MineContext, MinerResult

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
_FM_KEY_RE = re.compile(r"^(name|description|tools|model|allowed-tools):[ \t]*(.*)$")
_FM_BLOCK_RE = re.compile(r"^[|>][+-]?\d*$")

INSTRUCTION_FILES: dict[str, str] = {
    "CLAUDE.md": "claude",
    "CLAUDE.local.md": "claude",
    "AGENTS.md": "agents",
    "GEMINI.md": "gemini",
    ".cursorrules": "cursor",
    ".windsurfrules": "windsurf",
    ".clinerules": "cline",
    ".github/copilot-instructions.md": "copilot",
    "llms.txt": "llms",
    "llms-full.txt": "llms",
    ".aider.conf.yml": "aider",
    "CONVENTIONS.md": "aider",
}


def _headings(text: str) -> list[str]:
    out: list[str] = []
    for match in _HEADING_RE.finditer(text):
        title = match.group(2).strip()[:80]
        out.append("[heading omitted: instruction-like]" if is_suspicious(title) else title)
        if len(out) >= 20:
            break
    return out


def _frontmatter(text: str) -> dict[str, str]:
    """The known scalar fields of a YAML frontmatter block, folding block scalars.

    Not a YAML parser on purpose: prey frontmatter is untrusted, and ``safe_load`` still
    expands aliases. A skill whose ``description: >`` spans five lines used to be recorded as
    the literal ``>``.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    lines = match.group(1).splitlines()
    fields: dict[str, str] = {}
    index = 0
    while index < len(lines):
        found = _FM_KEY_RE.match(lines[index])
        index += 1
        if found is None:
            continue
        key, value = found.group(1), found.group(2).strip()
        if not value or _FM_BLOCK_RE.match(value):
            block: list[str] = []
            while index < len(lines) and (not lines[index].strip() or lines[index][:1] in " \t"):
                block.append(lines[index].strip())
                index += 1
            items = [line for line in block if line]
            if items and all(item.startswith("- ") for item in items):
                value = ", ".join(item[2:].strip() for item in items)
            else:
                value = " ".join(items)
        cleaned = " ".join(value.strip().strip("\"'").split())[:160]
        fields[key] = "[omitted: instruction-like]" if is_suspicious(cleaned) else cleaned
    return fields


def _gist_markdown(ctx: MineContext, info: FileInfo) -> dict[str, Any]:
    text = ctx.read(info.path, limit=300_000)
    return {
        "path": info.path,
        "bytes": info.size,
        "lines": info.loc,
        "words": len(text.split()),
        "headings": _headings(text),
        "frontmatter": _frontmatter(text),
        "imports": len(re.findall(r"^@[\w./~-]+", text, re.MULTILINE)),
        "suspicious": suspicious_fragments(text, limit=5),
    }


def _load_json(ctx: MineContext, rel: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(ctx.read(rel, limit=200_000))
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _settings_gist(ctx: MineContext, rel: str) -> dict[str, Any]:
    data = _load_json(ctx, rel)
    if data is None:
        return {"path": rel, "parse_error": True}
    permissions = as_dict(data.get("permissions"))
    hooks = as_dict(data.get("hooks"))
    return {
        "path": rel,
        "keys": sorted(str(k) for k in data)[:30],
        "permissions": {
            key: len(value) if isinstance(value, list) else 0
            for key, value in permissions.items()
            if key in ("allow", "deny", "ask")
        },
        "default_mode": permissions.get("defaultMode"),
        "hook_events": sorted(str(k) for k in hooks),
        "enabled_plugins": len(data["enabledPlugins"])
        if isinstance(data.get("enabledPlugins"), dict | list)
        else 0,
    }


def _mcp_gist(ctx: MineContext, rel: str) -> dict[str, Any]:
    data = _load_json(ctx, rel)
    servers = as_dict(data.get("mcpServers")) if data else {}
    return {
        "path": rel,
        "servers": sorted(str(k) for k in servers)[:20],
        "transports": sorted(
            {
                str(v.get("type") or ("stdio" if "command" in v else "http"))
                for v in servers.values()
                if isinstance(v, dict)
            }
        ),
    }


def _plugin_gist(ctx: MineContext, rel: str) -> dict[str, Any]:
    data = _load_json(ctx, rel) or {}
    return {
        "path": rel,
        "name": data.get("name"),
        "version": data.get("version"),
        "keys": sorted(str(k) for k in data)[:20],
    }


class AiConfigMiner:
    name = "ai_config"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "ai.json"
    md_file = "ai.md"

    def run(self, ctx: MineContext) -> MinerResult:
        files = [f for f in ctx.files() if not f.vendored and not f.generated]
        by_path = {f.path: f for f in files}

        instructions: list[dict[str, Any]] = []
        for rel, tool in INSTRUCTION_FILES.items():
            info = by_path.get(rel)
            if info is not None:
                gist = _gist_markdown(ctx, info)
                gist["tool"] = tool
                instructions.append(gist)
        nested_claude = [f for f in files if f.name == "CLAUDE.md" and f.depth >= 1][:20]
        for info in nested_claude:
            gist = _gist_markdown(ctx, info)
            gist["tool"] = "claude (nested)"
            instructions.append(gist)
        cursor_rules = [f.path for f in files if f.path.startswith(".cursor/rules/")][:20]
        copilot_instructions = [
            f.path for f in files if f.path.startswith(".github/instructions/")
        ][:20]
        copilot_prompts = [f.path for f in files if f.path.startswith(".github/prompts/")][:20]

        skills = []
        for info in files:
            if info.name == "SKILL.md" and info.depth >= 1:
                gist = _gist_markdown(ctx, info)
                gist["location"] = (
                    "plugin"
                    if info.path.startswith("skills/")
                    else "project"
                    if info.path.startswith(".claude/skills/")
                    else "other"
                )
                skills.append(gist)
        agents = []
        for info in files:
            if info.ext == ".md" and (
                info.path.startswith(".claude/agents/") or info.path.startswith("agents/")
            ):
                agents.append(_gist_markdown(ctx, info))
        commands = sorted(
            f.path for f in files if f.path.startswith(".claude/commands/") and f.ext == ".md"
        )
        hook_files = sorted(
            f.path for f in files if f.path.startswith((".claude/hooks/", "hooks/"))
        )
        settings = [
            _settings_gist(ctx, rel)
            for rel in (".claude/settings.json", ".claude/settings.local.json")
            if rel in by_path
        ]
        mcp = [
            _mcp_gist(ctx, rel)
            for rel in (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json")
            if rel in by_path
        ]
        plugin = [
            _plugin_gist(ctx, rel)
            for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json")
            if rel in by_path
        ]
        codex = sorted(f.path for f in files if f.path.startswith(".codex/"))[:10]
        devcontainer = any(
            f.path.startswith(".devcontainer/") or f.name == ".devcontainer.json" for f in files
        )

        suspicious_total = sum(len(g["suspicious"]) for g in instructions + skills + agents)
        present = sorted(
            {g["tool"] for g in instructions}
            | ({"cursor"} if cursor_rules else set())
            | ({"copilot"} if copilot_instructions or copilot_prompts else set())
            | ({"claude"} if skills or agents or commands or settings or plugin else set())
            | ({"mcp"} if mcp else set())
            | ({"codex"} if codex else set())
        )
        data: dict[str, Any] = {
            "present": present,
            "instruction_files": instructions,
            "cursor_rules": cursor_rules,
            "copilot_instructions": copilot_instructions,
            "copilot_prompts": copilot_prompts,
            "skills": skills,
            "agents": agents,
            "commands": commands,
            "hooks": hook_files,
            "settings": settings,
            "mcp": mcp,
            "plugin": plugin,
            "codex": codex,
            "devcontainer": devcontainer,
            "suspicious_fragments": suspicious_total,
        }
        warnings = []
        if suspicious_total:
            warnings.append(f"{suspicious_total} instruction-like fragments in AI config files")
        return MinerResult(self.name, data, doc=self._doc(ctx, data), warnings=warnings)

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"AI configuration: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        if not data["present"]:
            summary.para("No agent instructions, skills or MCP configuration found.")
        summary.kv(
            [
                ("Tools configured", ", ".join(data["present"]) or "none"),
                (
                    "Instruction files",
                    ", ".join(g["path"] for g in data["instruction_files"]) or "none",
                ),
                ("Skills", len(data["skills"])),
                ("Subagents", len(data["agents"])),
                ("Commands", len(data["commands"])),
                ("Hook files", len(data["hooks"])),
                ("MCP servers", ", ".join(s for m in data["mcp"] for s in m["servers"]) or "none"),
                ("Cursor rules", len(data["cursor_rules"])),
                (
                    "Copilot instructions",
                    len(data["copilot_instructions"]) + len(data["copilot_prompts"]),
                ),
                ("Plugin manifest", data["plugin"][0]["name"] if data["plugin"] else "none"),
                ("Devcontainer", data["devcontainer"]),
                ("Instruction-like fragments flagged", data["suspicious_fragments"]),
            ]
        )
        for gist in data["instruction_files"]:
            section = doc.section(f"{gist['path']} ({gist['tool']})", priority=2)
            section.kv(
                [
                    ("Size", f"{gist['lines']} lines, ~{gist['words']} words"),
                    ("Imports (@file)", gist["imports"]),
                ]
            )
            if gist["headings"]:
                section.line("Headings:")
                section.bullets(gist["headings"], max_items=20)
        if data["skills"]:
            skills = doc.section("Skills", priority=2)
            skills.table(
                ["Path", "Name", "Description"],
                (
                    [
                        s["path"],
                        s["frontmatter"].get("name", ""),
                        s["frontmatter"].get("description", "")[:120],
                    ]
                    for s in data["skills"]
                ),
                max_rows=25,
            )
        if data["agents"]:
            agents = doc.section("Subagents", priority=3)
            agents.table(
                ["Path", "Name", "Tools"],
                (
                    [a["path"], a["frontmatter"].get("name", ""), a["frontmatter"].get("tools", "")]
                    for a in data["agents"]
                ),
                max_rows=20,
            )
        if data["settings"]:
            settings = doc.section("Settings", priority=3)
            for gist in data["settings"]:
                if gist.get("parse_error"):
                    settings.bullets([f"{gist['path']}: not valid JSON"])
                    continue
                settings.kv(
                    [
                        ("File", gist["path"]),
                        ("Keys", ", ".join(gist["keys"])),
                        (
                            "Permissions",
                            ", ".join(f"{k}: {v}" for k, v in gist["permissions"].items())
                            or "none",
                        ),
                        ("Hook events", ", ".join(gist["hook_events"]) or "none"),
                    ]
                )
        if data["commands"] or data["hooks"] or data["cursor_rules"]:
            other = doc.section("Other files", priority=4)
            other.bullets(
                data["commands"]
                + data["hooks"]
                + data["cursor_rules"]
                + data["copilot_instructions"],
                max_items=30,
            )
        return doc
