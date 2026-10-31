"""Qualification counts cached reads and never enables an unrestricted host."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qualification_native import budget_stop, native_command


def test_missing_meter_refuses_additional_work() -> None:
    assert budget_stop(None, limit=30000)


def test_cached_tokens_still_exhaust_the_actual_read_budget() -> None:
    usage = {"totalTokens": 21000, "cachedInputTokens": 20000, "lastInputTokens": 5000}
    assert budget_stop(usage, limit=30000)
    assert not budget_stop({"totalTokens": 1000, "lastInputTokens": 1000}, limit=30000)


def test_reserve_covers_delayed_usage_and_the_next_model_input() -> None:
    assert budget_stop({"totalTokens": 14000, "lastInputTokens": 5000}, limit=30000)
    assert not budget_stop({"totalTokens": 13999, "lastInputTokens": 5000}, limit=30000)


def test_native_settings_are_temporary_and_disable_other_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    original = '[mcp_servers.example]\ncommand = "unneeded-tool"\n'
    config.write_text(original, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("qualification_native.shutil.which", lambda _: "codex")
    command = native_command(30000)
    assert "mcp_servers.example.enabled=false" in command
    assert "features.goals=false" in command
    assert "features.shell_tool=false" in command
    assert "features.multi_agent=false" in command
    assert config.read_text(encoding="utf-8") == original
    for remaining in (1, 2000, 8000, 12000):
        scoped = native_command(remaining)
        reminder = next(
            value.split("=", 1)[1]
            for value in scoped
            if value.startswith("features.rollout_budget.reminder_at_remaining_tokens=")
        )
        assert all(0 < value < remaining for value in json.loads(reminder))


def test_unsupported_mcp_key_cannot_inject_scoped_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers."unsafe.key"]\ncommand = "unused"\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("qualification_native.shutil.which", lambda _: "codex")
    with pytest.raises(ValueError, match="MCP configuration key"):
        native_command(30000)
