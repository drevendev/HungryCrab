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


def test_codex_overlay_points_at_attributed_red_crab_asset() -> None:
    codex = _json(".codex-plugin/plugin.json")
    interface = codex["interface"]
    assert isinstance(interface, dict)
    assert interface["displayName"] == "Hungry Crab"
    assert interface["websiteURL"] == REPOSITORY
    assert interface["brandColor"].lower() == "#d22f27"

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
