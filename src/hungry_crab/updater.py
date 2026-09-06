"""``crab update``: check the CLI and every agent plugin, and bring them up to date.

The crab cannot replace its own executable while it is running. On Windows uv fails with
``failed to remove directory ... Scripts: Access is denied`` and leaves the tool broken, so the
CLI reinstall is only executed when the running process is *not* the uv tool install that would
be replaced; otherwise the command is printed for you to paste. Plugin updates are external
processes and always safe to run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from base64 import b64decode
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .errors import CrabError, ExternalCommandError
from .fetch.github import GitHubClient
from .typeutil import as_dict, as_list

REPO = "drevendev/HungryCrab"
GIT_URL = f"https://github.com/{REPO}"
REQUIREMENT = f"hungry-crab @ git+{GIT_URL}"
BRANCH = "master"
PLUGIN = "crab"
MARKETPLACE = "hungry-crab"
PLUGIN_ID = f"{PLUGIN}@{MARKETPLACE}"

OK = "up to date"
OUTDATED = "update available"
MISSING = "not installed"
ABSENT = "agent not installed"
UNKNOWN = "unknown"
ACTIONABLE = frozenset({OUTDATED, MISSING, UNKNOWN})


def _noop(_: str) -> None:
    return None


def run_command(args: Sequence[str], *, timeout: float = 300.0) -> tuple[bool, str]:
    """Run an external command; return (ok, combined output).

    The first element is resolved through ``PATH`` first: on Windows the agents ship as
    ``claude.CMD`` and a bare name is not resolved by ``CreateProcess``.
    """
    argv = list(args)
    if not argv:
        return False, "empty command"
    resolved = shutil.which(argv[0])
    if resolved is None:
        return False, f"{argv[0]} is not on PATH"
    argv[0] = resolved
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode == 0, (out + err).strip()


@dataclass(frozen=True)
class AgentSpec:
    """How one agent installs, lists and updates plugins."""

    key: str
    label: str
    exe: str
    list_args: tuple[str, ...]
    add_marketplace: tuple[str, ...]
    refresh_marketplace: tuple[str, ...]
    install: tuple[str, ...]
    update: tuple[str, ...]

    def command(self, args: Sequence[str]) -> list[str]:
        return [self.exe, *args]


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        key="claude",
        label="Claude Code",
        exe="claude",
        list_args=("plugin", "list", "--json"),
        add_marketplace=("plugin", "marketplace", "add", REPO),
        refresh_marketplace=("plugin", "marketplace", "update", MARKETPLACE),
        install=("plugin", "install", PLUGIN_ID),
        update=("plugin", "update", PLUGIN),
    ),
    AgentSpec(
        key="codex",
        label="Codex",
        exe="codex",
        list_args=("plugin", "list", "--json"),
        add_marketplace=("plugin", "marketplace", "add", REPO),
        refresh_marketplace=("plugin", "marketplace", "upgrade", MARKETPLACE),
        install=("plugin", "add", PLUGIN_ID),
        # Codex has no `plugin update`; re-adding after a marketplace refresh installs the
        # newer snapshot.
        update=("plugin", "add", PLUGIN_ID),
    ),
)


def plugin_version(agent: AgentSpec, payload: Any) -> str | None:
    """Version of the crab plugin in an agent's ``plugin list --json`` output."""
    if agent.key == "claude":
        for item in as_list(payload):
            data = as_dict(item)
            if data.get("id") == PLUGIN_ID:
                version = data.get("version")
                return str(version) if version is not None else None
        return None
    for item in as_list(as_dict(payload).get("installed")):
        data = as_dict(item)
        if data.get("pluginId") == PLUGIN_ID and data.get("installed"):
            version = data.get("version")
            return str(version) if version is not None else None
    return None


@dataclass
class Remote:
    """What master currently holds."""

    cli_version: str | None = None
    plugin_version: str | None = None
    sha: str | None = None
    date: str | None = None
    error: str | None = None

    @property
    def short_sha(self) -> str | None:
        return self.sha[:7] if self.sha else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _file_at_master(client: GitHubClient, path: str) -> str:
    payload = as_dict(client.get(f"repos/{REPO}/contents/{path}?ref={BRANCH}"))
    content = payload.get("content")
    if not isinstance(content, str):
        raise ExternalCommandError(f"unexpected response for {path}")
    return b64decode(content).decode("utf-8", errors="replace")


def fetch_remote(client: GitHubClient | None = None) -> Remote:
    """Versions on master. Never raises: a network failure becomes ``error``."""
    api = client or GitHubClient()
    remote = Remote()
    try:
        pyproject = tomllib.loads(_file_at_master(api, "pyproject.toml"))
        version = as_dict(pyproject.get("project")).get("version")
        remote.cli_version = str(version) if version is not None else None
        manifest = json.loads(_file_at_master(api, ".claude-plugin/plugin.json"))
        plugin = as_dict(manifest).get("version")
        remote.plugin_version = str(plugin) if plugin is not None else None
        commit = as_dict(api.get(f"repos/{REPO}/commits/{BRANCH}"))
        sha = commit.get("sha")
        remote.sha = str(sha) if sha else None
        committer = as_dict(as_dict(commit.get("commit")).get("committer"))
        date = committer.get("date")
        remote.date = str(date)[:10] if date else None
    except (CrabError, ValueError, tomllib.TOMLDecodeError) as exc:
        remote.error = str(getattr(exc, "message", exc))
    return remote


def uv_tool_receipt() -> Path | None:
    """The uv receipt beside the running venv, when the CLI runs as a uv tool."""
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    return receipt if receipt.is_file() else None


def cli_install_kind() -> str:
    """How this crab was installed: ``uv-tool``, ``editable``, ``environment``."""
    if uv_tool_receipt() is not None:
        return "uv-tool"
    package = Path(__file__).resolve().parent
    if (package.parent.parent / "pyproject.toml").is_file():
        return "editable"
    return "environment"


@dataclass
class Component:
    name: str
    installed: str | None
    available: str | None
    status: str
    detail: str = ""
    commands: list[list[str]] = field(default_factory=list)
    ran: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["commands"] = [" ".join(command) for command in self.commands]
        return data


@dataclass
class UpdateReport:
    components: list[Component]
    remote: Remote
    executed: bool

    @property
    def actionable(self) -> list[Component]:
        return [c for c in self.components if c.status in ACTIONABLE and c.commands]

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": [c.to_dict() for c in self.components],
            "remote": self.remote.to_dict(),
            "executed": self.executed,
            "actionable": [c.name for c in self.actionable],
        }


def check_cli(remote: Remote) -> Component:
    kind = cli_install_kind()
    installed = __version__
    command = ["uv", "tool", "install", "--force", REQUIREMENT]
    if kind == "editable":
        detail = "editable install from a local checkout; `git pull` updates it"
        return Component("crab CLI", installed, remote.cli_version, OK, detail)
    if remote.error:
        return Component("crab CLI", installed, None, UNKNOWN, remote.error, [command])
    if remote.cli_version and remote.cli_version != installed:
        detail = f"master is at {remote.cli_version}"
        return Component("crab CLI", installed, remote.cli_version, OUTDATED, detail, [command])
    detail = "same version as master"
    if remote.short_sha:
        detail += f" ({remote.short_sha}, {remote.date}); reinstall to pick up newer commits"
    return Component("crab CLI", installed, remote.cli_version, OK, detail, [command])


def check_agent(
    agent: AgentSpec, remote: Remote, *, runner: Callable[[Sequence[str]], tuple[bool, str]]
) -> Component:
    name = f"{agent.label} plugin"
    if shutil.which(agent.exe) is None:
        return Component(name, None, remote.plugin_version, ABSENT, f"`{agent.exe}` not on PATH")
    ok, output = runner(agent.command(agent.list_args))
    if not ok:
        return Component(name, None, remote.plugin_version, UNKNOWN, output[-200:])
    try:
        payload = json.loads(output or "null")
    except ValueError:
        return Component(name, None, remote.plugin_version, UNKNOWN, "unreadable plugin list")
    installed = plugin_version(agent, payload)
    if installed is None:
        commands = [agent.command(agent.add_marketplace), agent.command(agent.install)]
        detail = f"`{agent.exe}` is here but the plugin is not installed"
        return Component(name, None, remote.plugin_version, MISSING, detail, commands)
    commands = [agent.command(agent.refresh_marketplace), agent.command(agent.update)]
    if remote.plugin_version and remote.plugin_version != installed:
        detail = f"marketplace has {remote.plugin_version}"
        return Component(name, installed, remote.plugin_version, OUTDATED, detail, commands)
    detail = "same version as master" if not remote.error else remote.error
    return Component(name, installed, remote.plugin_version, OK, detail, commands)


def check(
    *,
    client: GitHubClient | None = None,
    runner: Callable[[Sequence[str]], tuple[bool, str]] | None = None,
) -> UpdateReport:
    call = runner or (lambda args: run_command(args))
    remote = fetch_remote(client)
    components = [check_cli(remote)]
    components += [check_agent(agent, remote, runner=call) for agent in AGENTS]
    return UpdateReport(components, remote, executed=False)


def apply(
    report: UpdateReport,
    *,
    runner: Callable[[Sequence[str]], tuple[bool, str]] | None = None,
    log: Callable[[str], None] = _noop,
) -> UpdateReport:
    """Run what is safe: plugin work always, the CLI only when it is not this process."""
    call = runner or (lambda args: run_command(args))
    self_install = uv_tool_receipt() is not None
    for component in report.components:
        if component.status not in ACTIONABLE or not component.commands:
            continue
        if component.name == "crab CLI":
            if self_install:
                component.detail = (
                    "run the command yourself: uv cannot replace the crab while it is running"
                )
                continue
            if shutil.which("uv") is None:
                component.detail = "uv is not on PATH"
                continue
        for command in component.commands:
            log(f"$ {' '.join(command)}")
            ok, output = call(command)
            component.ran.append({"command": " ".join(command), "ok": ok, "output": output[-400:]})
            if not ok:
                component.detail = f"failed: {output.strip()[-200:]}"
                break
        else:
            component.status = OK
            component.detail = "updated"
    report.executed = True
    return report


def format_report(report: UpdateReport) -> str:
    lines = [f"{'component':<22}{'installed':<12}{'master':<12}status"]
    for component in report.components:
        lines.append(
            f"{component.name:<22}{component.installed or '-':<12}"
            f"{component.available or '?':<12}{component.status}"
        )
        if component.detail:
            lines.append(f"{'':<22}{component.detail}")
        for entry in component.ran:
            mark = "ok" if entry["ok"] else "FAILED"
            lines.append(f"{'':<22}{mark}: {entry['command']}")
    if report.remote.error:
        lines.append(f"\ncould not read master: {report.remote.error}")
    pending = [c for c in report.actionable if not c.ran]
    if pending:
        lines.append("\nRun:")
        for component in pending:
            for command in component.commands:
                lines.append(f"  {' '.join(command)}")
    elif report.executed:
        lines.append("\nEverything that could be updated here was updated.")
    else:
        lines.append("\nNothing to do.")
    return "\n".join(lines)
