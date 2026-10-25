from __future__ import annotations

import http.client
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


@pytest.fixture(autouse=True)
def isolated_install_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))


class FakeGitHub:
    """Serves the files `fetch_remote` reads, or raises."""

    def __init__(
        self,
        cli: str = "9.9.9",
        plugin: str = "9.9.9",
        codex_plugin: str | None = None,
        fail: bool = False,
    ) -> None:
        self.cli = cli
        self.plugin = plugin
        self.codex_plugin = codex_plugin if codex_plugin is not None else plugin
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
        if "/contents/.claude-plugin/plugin.json?" in path:
            body = json.dumps({"name": "crab", "version": self.plugin})
            return {"content": b64encode(body.encode()).decode(), "encoding": "base64"}
        if "/contents/plugin.json?" in path:
            body = json.dumps({"name": "crab", "version": self.codex_plugin})
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
    return json.dumps(
        [{"id": PLUGIN_ID, "version": version, "enabled": True, "gitCommitSha": "a" * 40}]
    )


def codex_list(version: str | None) -> str:
    installed = [{"pluginId": "documents@openai", "version": "1", "installed": True}]
    if version is not None:
        installed.append(
            {"pluginId": PLUGIN_ID, "version": version, "installed": True, "revision": "a" * 40}
        )
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
    assert remote.claude_plugin_version == "0.3.0"
    assert remote.codex_plugin_version == "0.3.0"
    assert remote.short_sha == "aaaaaaa"
    assert remote.date == "2026-09-06"
    assert remote.error is None
    assert any(".claude-plugin/plugin.json?ref=master" in path for path in client.paths)
    assert any("/contents/plugin.json?ref=master" in path for path in client.paths)


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

    # Same version string, different commit: during a dev series that is the normal case, and
    # reporting it as up to date left a crab seven commits and one rename behind.
    monkeypatch.setattr(updater, "installed_commit", lambda: "a" * 40)
    behind = check_cli(Remote(cli_version=__version__, sha="b" * 40, date="2026-09-06"))
    assert behind.status == OUTDATED
    assert "installed from aaaaaaa" in behind.detail and "master is at bbbbbbb" in behind.detail
    assert behind.commands == [["uv", "tool", "install", "--force", updater.REQUIREMENT]]

    monkeypatch.setattr(updater, "installed_commit", lambda: "b" * 40)
    same = check_cli(Remote(cli_version=__version__, sha="b" * 40, date="2026-09-06"))
    assert same.status == OK
    assert same.detail == "same version as master (bbbbbbb, 2026-09-06)"

    # No recorded commit is not evidence of equality: the version string cannot tell two
    # development snapshots apart, so the verdict has to stay actionable rather than fall back
    # to the optimistic one the commit comparison was added to replace.
    monkeypatch.setattr(updater, "installed_commit", lambda: None)
    unknown = check_cli(Remote(cli_version=__version__, sha="b" * 40, date="2026-09-06"))
    assert unknown.status == UNKNOWN
    assert "records no commit" in unknown.detail
    assert unknown.commands == [["uv", "tool", "install", "--force", updater.REQUIREMENT]]
    assert same.commands, "an up-to-date version can still be behind master"

    offline = check_cli(Remote(error="no network"))
    assert offline.status == UNKNOWN and offline.detail == "no network"

    monkeypatch.setattr(updater, "cli_install_kind", lambda: "editable")
    editable = check_cli(Remote(cli_version="9.9.9"))
    assert editable.status == OK and editable.commands == []
    assert "git pull" in editable.detail


def test_agent_component_states(monkeypatch: pytest.MonkeyPatch) -> None:
    remote = Remote(claude_plugin_version="0.3.0", codex_plugin_version="0.3.0", sha="a" * 40)
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


def test_agent_checks_use_their_own_remote_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    remote = fetch_remote(
        FakeGitHub(cli="0.3.0", plugin="0.3.0", codex_plugin="0.4.0")  # type: ignore[arg-type]
    )
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    claude = check_agent(
        CLAUDE, remote, runner=fake_runner({"plugin list": (True, claude_list("0.3.0"))})
    )
    codex = check_agent(
        CODEX, remote, runner=fake_runner({"plugin list": (True, codex_list("0.3.0"))})
    )

    assert claude.status == OK and claude.available == "0.3.0"
    assert codex.status == OUTDATED and codex.available == "0.4.0"


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
    assert payload["remote"]["claude_plugin_version"] == "0.3.0"
    assert payload["remote"]["codex_plugin_version"] == "0.3.0"
    assert payload["components"][0]["commands"] == [
        f'uv tool install --force "{updater.REQUIREMENT}"'
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
    assert claude.status == OUTDATED  # The simulated host still reports the old install.
    assert codex.status == OUTDATED
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


def _same_version_no_commit(monkeypatch: pytest.MonkeyPatch, kind: str) -> UpdateReport:
    """A crab at master's version string whose install records no commit."""
    monkeypatch.setattr(updater, "cli_install_kind", lambda: kind)
    monkeypatch.setattr(
        updater, "uv_tool_receipt", lambda: Path("uv-receipt.toml") if kind == "uv-tool" else None
    )
    monkeypatch.setattr(updater, "installed_commit", lambda: None)
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    runner = fake_runner(
        {
            "claude plugin list": (True, claude_list("0.3.0")),
            "codex plugin list": (True, codex_list("0.3.0")),
        }
    )
    return check(client=FakeGitHub(cli=__version__, plugin="0.3.0"), runner=runner)  # type: ignore[arg-type]


def test_a_missing_commit_is_repaired_where_the_crab_is_not_the_running_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _same_version_no_commit(monkeypatch, "environment")
    cli = report.components[0]
    assert cli.status == UNKNOWN and cli in report.actionable
    calls: list[list[str]] = []
    updater.apply(report, runner=fake_runner({}, calls))
    assert any("uv tool install --force" in " ".join(call) for call in calls)
    assert cli.status == OK and cli.detail == "updated"


def test_a_missing_commit_prints_the_reinstall_for_a_running_uv_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _same_version_no_commit(monkeypatch, "uv-tool")
    cli = report.components[0]
    assert cli.status == UNKNOWN
    assert "Nothing to do." not in format_report(report), "it just said to reinstall"
    calls: list[list[str]] = []
    updater.apply(report, runner=fake_runner({}, calls))
    assert not any("uv tool install" in " ".join(call) for call in calls), "would break itself"
    assert cli.status == UNKNOWN and "while it is running" in cli.detail
    text = format_report(report)
    assert "Run:" in text and "uv tool install --force" in text


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


def test_display_command_quotes_the_requirement() -> None:
    command = ["uv", "tool", "install", "--force", updater.REQUIREMENT]
    shown = updater.display_command(command)
    assert shown == (
        'uv tool install --force "hungry-crab @ git+https://github.com/drevendev/HungryCrab"'
    )
    assert updater.display_command(["claude", "plugin", "update", "crab"]) == (
        "claude plugin update crab"
    )


def test_printed_commands_are_pasteable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "cli_install_kind", lambda: "uv-tool")
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    runner = fake_runner(
        {
            "claude plugin list": (True, claude_list("0.2.0")),
            "codex plugin list": (True, codex_list("0.2.0")),
        }
    )
    report = check(client=FakeGitHub(cli="0.3.0", plugin="0.3.0"), runner=runner)  # type: ignore[arg-type]
    text = format_report(report)
    assert '"hungry-crab @ git+https://github.com/drevendev/HungryCrab"' in text
    assert "--force hungry-crab @" not in text, "an unquoted requirement would not paste"
    assert report.to_dict()["components"][0]["commands"] == [
        'uv tool install --force "hungry-crab @ git+https://github.com/drevendev/HungryCrab"'
    ]


def test_empty_report_says_nothing_to_do() -> None:
    report = UpdateReport(components=[], remote=Remote(), executed=False)
    assert "Nothing to do." in format_report(report)


class FakePyPI:
    """Serves the project page `fetch_release` reads, or raises like the real client."""

    def __init__(self, version: str | None = "9.9.9", *, fail: bool = False) -> None:
        self.version = version
        self.fail = fail
        self.urls: list[str] = []

    def get(self, url: str) -> Any:
        from hungry_crab.errors import ExternalCommandError

        self.urls.append(url)
        if self.fail:
            raise ExternalCommandError(f"GET {url} failed: no network")
        return {"info": {"name": "hungry-crab", "version": self.version}, "releases": {}}


def _index_install(monkeypatch: pytest.MonkeyPatch, kind: str = "uv-tool") -> None:
    """A crab installed from the package index: no PEP 610 record, so no commit either."""
    monkeypatch.setattr(updater, "cli_install_kind", lambda: kind)
    monkeypatch.setattr(updater, "install_source", lambda: "index")
    monkeypatch.setattr(updater, "installed_commit", lambda: None)


def test_fetch_release_reads_pypi_and_never_raises() -> None:
    index = FakePyPI("0.6.2")
    release = updater.fetch_release(index)  # type: ignore[arg-type]
    assert release.version == "0.6.2" and release.error is None
    assert index.urls == ["https://pypi.org/pypi/hungry-crab/json"]
    offline = updater.fetch_release(FakePyPI(fail=True))  # type: ignore[arg-type]
    assert offline.version is None and "no network" in str(offline.error)
    assert updater.fetch_release(FakePyPI(None)).error  # type: ignore[arg-type]


def test_the_pypi_client_turns_every_failure_into_a_crab_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.error import URLError

    from hungry_crab.errors import CrabError

    def refuse(*_: object, **__: object) -> None:
        raise URLError("offline")

    monkeypatch.setattr(updater.urllib.request, "urlopen", refuse)
    with pytest.raises(CrabError, match="offline"):
        updater.PyPIClient().get(updater.PYPI_JSON_URL)
    assert updater.fetch_release().error is not None


@pytest.mark.parametrize(
    "failure",
    [
        http.client.BadStatusLine("garbage"),
        http.client.IncompleteRead(b"{"),
        http.client.HTTPException("got more than 100 headers"),
    ],
    ids=["bad-status-line", "incomplete-read", "too-many-headers"],
)
def test_a_broken_http_response_is_unknown_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, failure: http.client.HTTPException
) -> None:
    """Review of #395: `http.client` errors are not `OSError` and used to escape."""

    class Broken:
        def get(self, *_: object, **__: object) -> Any:
            raise failure

    release = updater.fetch_release(Broken())  # type: ignore[arg-type]
    assert release.version is None and release.error
    remote = fetch_remote(Broken())  # type: ignore[arg-type]
    assert remote.cli_version is None and remote.error

    def refuse(*_: object, **__: object) -> None:
        raise failure

    monkeypatch.setattr(updater.urllib.request, "urlopen", refuse)
    from hungry_crab.errors import CrabError

    with pytest.raises(CrabError, match=type(failure).__name__):
        updater.PyPIClient().get(updater.PYPI_JSON_URL)
    _index_install(monkeypatch)
    cli = check_cli(Remote(error="no network"))
    assert cli.status == UNKNOWN and type(failure).__name__ in cli.detail


@pytest.mark.parametrize(
    ("kind", "command"),
    [
        ("uv-tool", ["uv", "tool", "upgrade", "hungry-crab"]),
        ("pipx", ["pipx", "upgrade", "hungry-crab"]),
        ("environment", [sys.executable, "-m", "pip", "install", "--upgrade", "hungry-crab"]),
    ],
)
def test_an_index_install_is_compared_with_the_latest_release_on_pypi(
    monkeypatch: pytest.MonkeyPatch, kind: str, command: list[str]
) -> None:
    """#395: an index install has no commit; asking it to reinstall from git was wrong."""
    _index_install(monkeypatch, kind)
    master = Remote(cli_version="9.9.9.dev0", sha="b" * 40, date="2026-10-23")

    newer = check_cli(master, index=FakePyPI("99.0.0"))  # type: ignore[arg-type]
    assert newer.status == OUTDATED and newer.available == "99.0.0"
    assert newer.detail == "PyPI has 99.0.0"
    assert newer.commands == [command]
    assert not any(updater.REQUIREMENT in part for part in newer.commands[0])

    current = check_cli(master, index=FakePyPI(__version__))  # type: ignore[arg-type]
    assert current.status == OK and current.commands == []
    assert current.detail == "the latest release on PyPI"

    ahead = check_cli(master, index=FakePyPI("0.0.1"))  # type: ignore[arg-type]
    assert ahead.status == OK and "newer than the latest release" in ahead.detail

    offline = check_cli(master, index=FakePyPI(fail=True))  # type: ignore[arg-type]
    assert offline.status == UNKNOWN and "no network" in offline.detail
    assert offline.commands == [command]


def test_a_git_install_still_follows_master(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "cli_install_kind", lambda: "uv-tool")
    monkeypatch.setattr(updater, "install_source", lambda: "vcs")
    monkeypatch.setattr(updater, "installed_commit", lambda: "a" * 40)
    index = FakePyPI("99.0.0")
    behind = check_cli(
        Remote(cli_version=__version__, sha="b" * 40, date="2026-10-23"),
        index=index,  # type: ignore[arg-type]
    )
    assert behind.status == OUTDATED and "master is at bbbbbbb" in behind.detail
    assert behind.commands == [["uv", "tool", "install", "--force", updater.REQUIREMENT]]
    assert index.urls == [], "a git install never asks PyPI"


def test_an_index_upgrade_is_printed_never_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The upgrade replaces the install the crab runs from, wherever that install lives."""
    _index_install(monkeypatch, "environment")
    monkeypatch.setattr(updater, "uv_tool_receipt", lambda: None)
    monkeypatch.setattr(updater, "pipx_metadata", lambda: None)
    monkeypatch.setattr(updater.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    calls: list[list[str]] = []
    runner = fake_runner(
        {
            "claude plugin list": (True, claude_list("0.3.0")),
            "codex plugin list": (True, codex_list("0.3.0")),
        },
        calls,
    )
    report = check(
        client=FakeGitHub(cli="0.3.0", plugin="0.3.0"),  # type: ignore[arg-type]
        runner=runner,
        index=FakePyPI("99.0.0"),  # type: ignore[arg-type]
    )
    calls.clear()
    updater.apply(report, runner=runner)
    assert not any("pip" in " ".join(call) for call in calls), "would replace itself"
    cli = report.components[0]
    assert cli.status == OUTDATED and "while it is running" in cli.detail
    text = format_report(report)
    assert "Run:" in text and "install --upgrade hungry-crab" in text


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("0.6.1", "0.6.2"),
        ("0.6.2.dev0", "0.6.2"),
        ("0.6.2.dev0", "0.6.2a1"),
        ("0.6.2a1", "0.6.2rc1"),
        ("0.6.2rc1", "0.6.2"),
        ("0.6.2", "0.6.2.post1"),
        ("0.6.9", "0.6.10"),
        ("0.6", "0.6.0.post1"),
    ],
)
def test_release_versions_order_as_pep_440_does(older: str, newer: str) -> None:
    low, high = updater._version_key(older), updater._version_key(newer)
    assert low is not None and high is not None and low < high
    assert updater._version_key("0.6.0") == updater._version_key("0.6")
    assert updater._version_key("not a version") is None


def test_install_source_reads_pep_610(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "site-packages" / "hungry_crab"
    package.mkdir(parents=True)
    monkeypatch.setattr(updater, "__file__", str(package / "updater.py"))
    assert updater.install_source() is None, "no metadata, no answer"
    info = package.parent / f"hungry_crab-{__version__}.dist-info"
    info.mkdir()
    assert updater.install_source() == "index"
    direct = info / "direct_url.json"
    direct.write_text(json.dumps({"url": "file:///w.whl", "archive_info": {}}), encoding="utf-8")
    assert updater.install_source() == "url"
    vcs = {"url": updater.GIT_URL, "vcs_info": {"vcs": "git", "commit_id": "c" * 40}}
    direct.write_text(json.dumps(vcs), encoding="utf-8")
    assert updater.install_source() == "vcs"
    assert updater.installed_commit() == "c" * 40


def test_pipx_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater.sys, "prefix", str(tmp_path))
    assert updater.pipx_metadata() is None
    (tmp_path / "pipx_metadata.json").write_text("{}", encoding="utf-8")
    assert updater.cli_install_kind() == "pipx"


@pytest.mark.parametrize("agent", AGENTS)
def test_same_version_different_revision_is_actionable_and_unknown_is_explicit(
    agent: updater.AgentSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updater.shutil, "which", lambda name: name)
    remote = Remote(claude_plugin_version="0.3.0", codex_plugin_version="0.3.0", sha="b" * 40)
    listing = claude_list if agent.key == "claude" else codex_list
    runner = fake_runner({"plugin list": (True, listing("0.3.0"))})
    result = check_agent(agent, remote, runner=runner)
    assert result.status == OUTDATED
    assert "installed revision" in result.detail and result.commands
    if agent.key == "claude":
        assert ["claude", "plugin", "uninstall", PLUGIN_ID] in result.commands
    monkeypatch.setattr(updater, "plugin_revision", lambda *_: None)
    assert check_agent(agent, remote, runner=runner).status == UNKNOWN


def test_successful_version_only_update_is_not_reported_as_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.shutil, "which", lambda name: name)
    remote = Remote(claude_plugin_version="0.3.0", sha="b" * 40)
    runner = fake_runner({"plugin list": (True, claude_list("0.3.0"))})
    component = check_agent(CLAUDE, remote, runner=runner)
    updater.apply(UpdateReport([component], remote, False), runner=runner)
    assert component.status == OUTDATED


def test_codex_revision_is_read_from_installed_snapshot_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    root = tmp_path / "plugins" / "cache" / "hungry-crab" / "crab" / "0.3.0"
    root.mkdir(parents=True)
    (root / ".codex-marketplace-install.json").write_text(
        json.dumps({"revision": "b" * 40}), encoding="utf-8"
    )
    payload = {"installed": [{"pluginId": PLUGIN_ID, "version": "0.3.0", "installed": True}]}
    assert updater.plugin_revision(CODEX, payload) == "b" * 40
