from __future__ import annotations

import json
import sys
from base64 import b64encode
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from hungry_crab import __version__, updater
from hungry_crab.updater import (
    ABSENT,
    AGENTS,
    MISSING,
    OK,
    OUTDATED,
    PLUGIN_ID,
    UNKNOWN,
    Remote,
    UpdateReport,
    check,
    check_agent,
    check_cli,
    fetch_remote,
    format_report,
    plugin_version,
)

CLAUDE, CODEX = AGENTS


class FakeGitHub:
    """Serves the three files `fetch_remote` reads, or raises."""

    def __init__(self, cli: str = "9.9.9", plugin: str = "9.9.9", fail: bool = False) -> None:
        self.cli = cli
        self.plugin = plugin
        self.fail = fail
        self.paths: list[str] = []

    def get(self, path: str, *, allow_missing: bool = False) -> Any:
        from hungry_crab.errors import ExternalCommandError

        self.paths.append(path)
        if self.fail:
            raise ExternalCommandError("no network")
        if "pyproject.toml" in path:
            body = f'[project]\nname = "hungry-crab"\nversion = "{self.cli}"\n'
            return {"content": b64encode(body.encode()).decode(), "encoding": "base64"}
        if "plugin.json" in path:
            body = json.dumps({"name": "crab", "version": self.plugin})
            return {"content": b64encode(body.encode()).decode(), "encoding": "base64"}
        return {"sha": "a" * 40, "commit": {"committer": {"date": "2026-09-06T10:00:00Z"}}}


def fake_runner(responses: dict[str, tuple[bool, str]], calls: list[list[str]] | None = None):
    def run(args: Sequence[str]) -> tuple[bool, str]:
        command = list(args)
        if calls is not None:
            calls.append(command)
        for needle, response in responses.items():
            if needle in " ".join(command):
                return response
        return True, ""

    return run


def claude_list(version: str | None) -> str:
    if version is None:
        return json.dumps([{"id": "other@market", "version": "1.0.0"}])
    return json.dumps([{"id": PLUGIN_ID, "version": version, "enabled": True}])


def codex_list(version: str | None) -> str:
    installed = [{"pluginId": "documents@openai", "version": "1", "installed": True}]
    if version is not None:
        installed.append({"pluginId": PLUGIN_ID, "version": version, "installed": True})
    return json.dumps({"installed": installed})


def test_plugin_version_reads_both_shapes() -> None:
    assert plugin_version(CLAUDE, json.loads(claude_list("0.2.0"))) == "0.2.0"
    assert plugin_version(CLAUDE, json.loads(claude_list(None))) is None
    assert plugin_version(CODEX, json.loads(codex_list("0.3.0"))) == "0.3.0"
    assert plugin_version(CODEX, json.loads(codex_list(None))) is None
    assert (
        plugin_version(CODEX, {"installed": [{"pluginId": PLUGIN_ID, "installed": False}]}) is None
    )


def test_fetch_remote_reads_master() -> None:
    client = FakeGitHub(cli="0.3.0", plugin="0.3.0")
    remote = fetch_remote(client)  # type: ignore[arg-type]
    assert remote.cli_version == "0.3.0"
    assert remote.plugin_version == "0.3.0"
    assert remote.short_sha == "aaaaaaa"
    assert remote.date == "2026-09-06"
    assert remote.error is None
    assert any("ref=master" in path for path in client.paths)


def test_fetch_remote_survives_no_network() -> None:
    remote = fetch_remote(FakeGitHub(fail=True))  # type: ignore[arg-type]
    assert remote.error == "no network"
    assert remote.cli_version is None and remote.sha is None


def test_cli_component_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "cli_install_kind", lambda: "uv-tool")
    newer = check_cli(Remote(cli_version="9.9.9", sha="b" * 40, date="2026-09-06"))
    assert newer.status == OUTDATED
    assert newer.installed == __version__ and newer.available == "9.9.9"
    assert newer.commands == [["uv", "tool", "install", "--force", updater.REQUIREMENT]]

    same = check_cli(Remote(cli_version=__version__, sha="b" * 40, date="2026-09-06"))
    assert same.status == OK
    assert "reinstall to pick up newer commits" in same.detail
    assert same.commands, "an up-to-date version can still be behind master"

    offline = check_cli(Remote(error="no network"))
    assert offline.status == UNKNOWN and offline.detail == "no network"

    monkeypatch.setattr(updater, "cli_install_kind", lambda: "editable")
    editable = check_cli(Remote(cli_version="9.9.9"))
    assert editable.status == OK and editable.commands == []
    assert "git pull" in editable.detail


def test_agent_component_states(monkeypatch: pytest.MonkeyPatch) -> None:
    remote = Remote(plugin_version="0.3.0")
    monkeypatch.setattr(updater.shutil, "which", lambda exe: None)
    absent = check_agent(CLAUDE, remote, runner=fake_runner({}))
    assert absent.status == ABSENT and absent.commands == []

    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    outdated = check_agent(
        CLAUDE, remote, runner=fake_runner({"plugin list": (True, claude_list("0.2.0"))})
    )
    assert outdated.status == OUTDATED and outdated.installed == "0.2.0"
    assert outdated.commands == [
        ["claude", "plugin", "marketplace", "update", "hungry-crab"],
        ["claude", "plugin", "update", "crab"],
    ]

    missing = check_agent(
        CODEX, remote, runner=fake_runner({"plugin list": (True, codex_list(None))})
    )
    assert missing.status == MISSING
    assert missing.commands == [
        ["codex", "plugin", "marketplace", "add", updater.REPO],
        ["codex", "plugin", "add", PLUGIN_ID],
    ]

    current = check_agent(
        CODEX, remote, runner=fake_runner({"plugin list": (True, codex_list("0.3.0"))})
    )
    assert current.status == OK

    broken = check_agent(CLAUDE, remote, runner=fake_runner({"plugin list": (False, "boom")}))
    assert broken.status == UNKNOWN and "boom" in broken.detail

    garbled = check_agent(CLAUDE, remote, runner=fake_runner({"plugin list": (True, "not json")}))
    assert garbled.status == UNKNOWN


def test_check_reports_every_component(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "cli_install_kind", lambda: "uv-tool")
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    runner = fake_runner(
        {
            "claude plugin list": (True, claude_list("0.2.0")),
            "codex plugin list": (True, codex_list(None)),
        }
    )
    report = check(client=FakeGitHub(cli="0.3.0", plugin="0.3.0"), runner=runner)  # type: ignore[arg-type]
    names = [c.name for c in report.components]
    assert names == ["crab CLI", "Claude Code plugin", "Codex plugin"]
    assert [c.status for c in report.components] == [OUTDATED, OUTDATED, MISSING]
    assert len(report.actionable) == 3
    text = format_report(report)
    assert "crab CLI" in text and "Run:" in text
    assert "uv tool install --force" in text
    assert "codex plugin add crab@hungry-crab" in text
    payload = report.to_dict()
    assert payload["remote"]["cli_version"] == "0.3.0"
    assert payload["components"][0]["commands"] == [
        f"uv tool install --force {updater.REQUIREMENT}"
    ]


def test_apply_runs_plugins_but_never_replaces_a_running_uv_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater, "cli_install_kind", lambda: "uv-tool")
    monkeypatch.setattr(updater, "uv_tool_receipt", lambda: Path("uv-receipt.toml"))
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    calls: list[list[str]] = []
    runner = fake_runner(
        {
            "claude plugin list": (True, claude_list("0.2.0")),
            "codex plugin list": (True, codex_list("0.2.0")),
        },
        calls,
    )
    report = check(client=FakeGitHub(cli="0.3.0", plugin="0.3.0"), runner=runner)  # type: ignore[arg-type]
    calls.clear()
    updater.apply(report, runner=runner)
    executed = [" ".join(call) for call in calls]
    assert not any("uv tool install" in command for command in executed), "would break itself"
    assert "claude plugin update crab" in executed
    assert "codex plugin add crab@hungry-crab" in executed
    cli, claude, codex = report.components
    assert cli.status == OUTDATED and "while it is running" in cli.detail
    assert claude.status == OK and claude.detail == "updated"
    assert codex.status == OK
    assert report.executed is True
    assert "ok: claude plugin update crab" in format_report(report)


def test_apply_updates_the_cli_when_it_is_not_the_running_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater, "cli_install_kind", lambda: "environment")
    monkeypatch.setattr(updater, "uv_tool_receipt", lambda: None)
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    calls: list[list[str]] = []
    runner = fake_runner(
        {
            "claude plugin list": (True, claude_list("0.3.0")),
            "codex plugin list": (True, codex_list("0.3.0")),
        },
        calls,
    )
    report = check(client=FakeGitHub(cli="0.3.0", plugin="0.3.0"), runner=runner)  # type: ignore[arg-type]
    calls.clear()
    updater.apply(report, runner=runner)
    executed = [" ".join(call) for call in calls]
    assert any("uv tool install --force" in command for command in executed)
    assert report.components[0].status == OK


def test_apply_reports_a_failing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "cli_install_kind", lambda: "uv-tool")
    monkeypatch.setattr(updater, "uv_tool_receipt", lambda: Path("uv-receipt.toml"))
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    runner = fake_runner(
        {
            "claude plugin list": (True, claude_list(None)),
            "codex plugin list": (True, codex_list("0.3.0")),
            "marketplace add": (False, "network is down"),
        }
    )
    report = check(client=FakeGitHub(cli=__version__, plugin="0.3.0"), runner=runner)  # type: ignore[arg-type]
    updater.apply(report, runner=runner)
    claude = report.components[1]
    assert claude.status == MISSING
    assert "network is down" in claude.detail
    assert len(claude.ran) == 1, "stops at the first failure"


def test_uv_tool_receipt_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater.sys, "prefix", str(tmp_path))
    assert updater.uv_tool_receipt() is None
    assert updater.cli_install_kind() in ("editable", "environment")
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    assert updater.uv_tool_receipt() == tmp_path / "uv-receipt.toml"
    assert updater.cli_install_kind() == "uv-tool"


def test_run_command_resolves_the_executable_and_captures_failure() -> None:
    ok, output = updater.run_command(["definitely-not-a-real-binary-xyz"])
    assert ok is False and "not on PATH" in output
    assert updater.run_command([]) == (False, "empty command")
    # a bare name must be resolved through PATH: on Windows `claude` is `claude.CMD`
    ok, output = updater.run_command([sys.executable, "-c", "print('alive')"])
    assert ok is True and output == "alive"


def test_empty_report_says_nothing_to_do() -> None:
    report = UpdateReport(components=[], remote=Remote(), executed=False)
    assert "Nothing to do." in format_report(report)
