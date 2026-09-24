from __future__ import annotations

import json
from pathlib import Path

from hungry_crab import __version__

ROOT = Path(__file__).resolve().parents[1]
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
REPOSITORY = "https://github.com/drevendev/HungryCrab"


def _json(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_portable_agent_manifest_matches_cli_and_claude_plugin() -> None:
    portable = _json("plugin.json")
    claude = _json(".claude-plugin/plugin.json")
    expected = __version__.replace(".dev", "-dev.")

    assert portable["$schema"] == AGENT_PLUGIN_SCHEMA
    assert portable["name"] == claude["name"] == "crab"
    assert portable["version"] == claude["version"] == expected
    assert portable["repository"] == portable["homepage"] == REPOSITORY
    assert portable["license"] == claude["license"] == "MIT"


def test_codex_reads_its_presentation_from_the_portable_manifest() -> None:
    """The Codex presentation lives under ``extensions.com.openai`` in the root manifest.

    When ``.codex-plugin/plugin.json`` exists, Codex takes the plugin's name and version from
    it instead of from the root ``plugin.json``: a missing name falls back to the directory
    the marketplace snapshot was checked out into (``hungry-crab``, not ``crab``) and a missing
    version becomes ``local``. An overlay that carried only the presentation therefore made
    ``codex plugin add crab@hungry-crab`` and every marketplace upgrade fail with
    "plugin.json name `hungry-crab` does not match marketplace plugin name `crab`", and an
    overlay that carried the version would be a seventh place to bump. So there is no overlay:
    Codex's documentation names ``extensions.com.openai`` as its replacement, and the identity
    stays in the one file ``crab update`` already reads. Hooks would not be wired by either
    file: a plugin with a root manifest goes through Codex's Agent Plugins loader, which has no
    hook support (openai/codex#39895).
    """
    assert not (ROOT / ".codex-plugin" / "plugin.json").exists(), (
        "a .codex-plugin/plugin.json overrides the portable name and version; keep the Codex "
        "presentation under extensions.com.openai in plugin.json"
    )

    portable = _json("plugin.json")
    extensions = portable["extensions"]
    assert isinstance(extensions, dict)
    codex = extensions["com.openai"]
    assert isinstance(codex, dict)
    interface = codex["interface"]
    assert isinstance(interface, dict)
    assert interface["displayName"] == "Hungry Crab"
    assert interface["websiteURL"] == REPOSITORY
    assert interface["brandColor"].lower() == "#d22f27"
    prompts = interface["defaultPrompt"]
    assert isinstance(prompts, list) and prompts, "Codex requires interface.defaultPrompt"

    plugin_root = ROOT.resolve()
    for key in ("composerIcon", "logo"):
        relative = interface[key]
        assert isinstance(relative, str)
        asset = (ROOT / relative).resolve()
        assert asset.is_relative_to(plugin_root), f"{key} escapes the plugin root"
        assert asset.is_file(), f"missing {key}: {relative}"
        assert "#d22f27" in asset.read_text(encoding="utf-8").lower()

    attribution = ROOT / ".codex-plugin" / "assets" / "ATTRIBUTION.md"
    text = attribution.read_text(encoding="utf-8")
    assert "OpenMoji" in text
    assert "CC BY-SA 4.0" in text
    assert "1F980.svg" in text


def test_codex_marketplace_installs_the_repository_root() -> None:
    marketplace = _json(".agents/plugins/marketplace.json")
    assert marketplace["name"] == "hungry-crab"
    entries = marketplace["plugins"]
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    assert entry["name"] == "crab"
    assert entry["source"] == {"source": "local", "path": "./"}
    assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
