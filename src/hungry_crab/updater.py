"""``crab update``: check the CLI and every agent plugin, and bring them up to date.

The crab cannot replace its own executable while it is running. On Windows uv fails with
``failed to remove directory ... Scripts: Access is denied`` and leaves the tool broken, so the
CLI reinstall is only executed when the running process is *not* the uv tool install that would
be replaced; otherwise the command is printed for you to paste. Plugin updates are external
processes and always safe to run.

A CLI installed from git is compared with ``master``. A CLI installed from the package index
(``uv tool install hungry-crab``, no PEP 610 ``direct_url.json``) is compared with the latest
release on PyPI, and its upgrade is always printed: it would replace the running install.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from base64 import b64decode
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .errors import CrabError, ExternalCommandError
from .fetch.bounded import MAX_JSON_BYTES, ResponseTooLargeError, read_limited
from .fetch.github import USER_AGENT, GitHubClient
from .typeutil import as_dict, as_list

REPO = "drevendev/HungryCrab"
GIT_URL = f"https://github.com/{REPO}"
DISTRIBUTION = "hungry-crab"
REQUIREMENT = f"{DISTRIBUTION} @ git+{GIT_URL}"
PYPI_JSON_URL = f"https://pypi.org/pypi/{DISTRIBUTION}/json"
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


def display_command(args: Sequence[str]) -> str:
    """A command a human can paste. The requirement contains spaces and must stay one argument.

    Double quotes rather than shell-style single quotes: cmd.exe understands only those, and
    PowerShell and POSIX shells accept them too.
    """
    parts = [f'"{arg}"' if " " in arg else arg for arg in args]
    return " ".join(parts)


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


def plugin_revision(agent: AgentSpec, payload: Any) -> str | None:
    """Read recorded install provenance; version equality is never revision evidence."""
    entries = (
        as_list(payload) if agent.key == "claude" else as_list(as_dict(payload).get("installed"))
    )
    matches = [
        as_dict(item)
        for item in entries
        if as_dict(item).get("id" if agent.key == "claude" else "pluginId") == PLUGIN_ID
    ]
    version = plugin_version(agent, payload)
    if agent.key == "codex" and version and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version):
        base = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        matches.append(
            {"installPath": str(base / "plugins" / "cache" / MARKETPLACE / PLUGIN / version)}
        )
    if agent.key == "claude":
        base = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        metadata = base / "plugins" / "installed_plugins.json"
        try:
            if metadata.stat().st_size <= 1024 * 1024:
                stored = json.loads(metadata.read_text(encoding="utf-8"))
                matches.extend(
                    as_dict(item)
                    for item in as_list(as_dict(as_dict(stored).get("plugins")).get(PLUGIN_ID))
                )
        except (OSError, ValueError):
            pass
    revisions: set[str] = set()
    for entry in matches:
        revision = entry.get("gitCommitSha", entry.get("revision"))
        if revision is None and isinstance(entry.get("installPath"), str):
            metadata = Path(entry["installPath"]) / ".codex-marketplace-install.json"
            try:
                if metadata.stat().st_size <= 1024 * 1024:
                    revision = as_dict(json.loads(metadata.read_text(encoding="utf-8"))).get(
                        "revision"
                    )
            except (OSError, ValueError):
                pass
        if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision):
            revisions.add(revision)
    return next(iter(revisions)) if len(revisions) == 1 else None


@dataclass
class Remote:
    """What master currently holds for the CLI and each agent's plugin manifest."""

    cli_version: str | None = None
    claude_plugin_version: str | None = None
    codex_plugin_version: str | None = None
    sha: str | None = None
    date: str | None = None
    error: str | None = None

    @property
    def short_sha(self) -> str | None:
        return self.sha[:7] if self.sha else None

    def plugin_version_for(self, agent: AgentSpec) -> str | None:
        if agent.key == "claude":
            return self.claude_plugin_version
        if agent.key == "codex":
            return self.codex_plugin_version
        return None

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

        claude_manifest = json.loads(_file_at_master(api, ".claude-plugin/plugin.json"))
        claude_version = as_dict(claude_manifest).get("version")
        remote.claude_plugin_version = str(claude_version) if claude_version is not None else None

        codex_manifest = json.loads(_file_at_master(api, "plugin.json"))
        codex_version = as_dict(codex_manifest).get("version")
        remote.codex_plugin_version = str(codex_version) if codex_version is not None else None

        commit = as_dict(api.get(f"repos/{REPO}/commits/{BRANCH}"))
        sha = commit.get("sha")
        remote.sha = str(sha) if sha else None
        committer = as_dict(as_dict(commit.get("commit")).get("committer"))
        date = committer.get("date")
        remote.date = str(date)[:10] if date else None
    except (CrabError, ValueError, tomllib.TOMLDecodeError, http.client.HTTPException) as exc:
        remote.error = str(getattr(exc, "message", exc)) or type(exc).__name__
    return remote


class PyPIClient:
    """Read-only access to the package index's JSON API; every call returns parsed JSON."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def get(self, url: str) -> Any:
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = read_limited(response, MAX_JSON_BYTES, label="PyPI response")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ExternalCommandError(f"GET {url} failed with HTTP {exc.code}") from exc
        except (
            ResponseTooLargeError,
            urllib.error.URLError,
            OSError,
            http.client.HTTPException,  # BadStatusLine, IncompleteRead, too many headers
        ) as exc:
            raise ExternalCommandError(f"GET {url} failed: {type(exc).__name__}: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8", errors="replace") or "null")
        except ValueError as exc:
            raise ExternalCommandError(f"GET {url} returned invalid JSON") from exc


@dataclass
class Release:
    """The latest release of the CLI on the package index."""

    version: str | None = None
    error: str | None = None


def fetch_release(client: PyPIClient | None = None) -> Release:
    """The latest release on PyPI. Never raises: a network failure becomes ``error``."""
    api = client or PyPIClient()
    try:
        info = as_dict(as_dict(api.get(PYPI_JSON_URL)).get("info"))
    except (CrabError, ValueError, OSError, http.client.HTTPException) as exc:
        return Release(error=str(getattr(exc, "message", exc)) or type(exc).__name__)
    version = info.get("version")
    if not isinstance(version, str) or not version.strip():
        return Release(error="PyPI names no release of hungry-crab")
    return Release(version=version.strip())


# PEP 440 public versions as this project writes them: a release, then an optional
# pre-release, post-release and development segment.
_VERSION_RE = re.compile(
    r"v?(\d{1,9}(?:\.\d{1,9}){0,9})(?:(a|b|rc)(\d{1,9}))?(?:\.post(\d{1,9}))?(?:\.dev(\d{1,9}))?"
)


def _version_key(version: str) -> tuple[Any, ...] | None:
    """A sortable key for ``version``, or None when it is not a version written that way."""
    match = _VERSION_RE.fullmatch(version.strip().lower())
    if match is None:
        return None
    release = [int(part) for part in match[1].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    pre_kinds = ("a", "b", "rc")
    if match[2]:
        pre: tuple[int, int] = (pre_kinds.index(match[2]), int(match[3]))
    elif match[5] is not None and match[4] is None:
        pre = (-1, 0)  # X.Y.Z.devN comes before every pre-release of X.Y.Z
    else:
        pre = (len(pre_kinds), 0)
    post = int(match[4]) if match[4] is not None else -1
    dev = int(match[5]) if match[5] is not None else float("inf")
    return (tuple(release), pre, post, dev)


def uv_tool_receipt() -> Path | None:
    """The uv receipt beside the running venv, when the CLI runs as a uv tool."""
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    return receipt if receipt.is_file() else None


def pipx_metadata() -> Path | None:
    """The pipx metadata beside the running venv, when the CLI runs as a pipx app."""
    metadata = Path(sys.prefix) / "pipx_metadata.json"
    return metadata if metadata.is_file() else None


def cli_install_kind() -> str:
    """How this crab was installed: ``uv-tool``, ``pipx``, ``editable``, ``environment``."""
    if uv_tool_receipt() is not None:
        return "uv-tool"
    if pipx_metadata() is not None:
        return "pipx"
    package = Path(__file__).resolve().parent
    if (package.parent.parent / "pyproject.toml").is_file():
        return "editable"
    return "environment"


def install_source() -> str | None:
    """Where the installed distribution came from, per PEP 610: ``vcs``, ``url`` or ``index``.

    pip and uv write ``direct_url.json`` for every install that did not come from a package
    index (a git URL, a local directory, a wheel or archive file), so a distribution without
    one was resolved from an index. None means no metadata of this crab could be found.
    """
    package = Path(__file__).resolve().parent
    infos = sorted(package.parent.glob("hungry_crab-*.dist-info"))
    current = [info for info in infos if info.name == f"hungry_crab-{__version__}.dist-info"]
    if current:
        infos = current
    if len(infos) != 1:
        return None
    direct = infos[0] / "direct_url.json"
    if not direct.is_file():
        return "index"
    try:
        data = as_dict(json.loads(direct.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    return "vcs" if "vcs_info" in data else "url"


def upgrade_command(kind: str) -> list[str]:
    """The command that upgrades an index install of the given kind to the latest release."""
    if kind == "uv-tool":
        return ["uv", "tool", "upgrade", DISTRIBUTION]
    if kind == "pipx":
        return ["pipx", "upgrade", DISTRIBUTION]
    return [sys.executable, "-m", "pip", "install", "--upgrade", DISTRIBUTION]


def installed_commit() -> str | None:
    """The commit this crab was installed from, per PEP 610.

    Versions cannot answer the question during a development series: every commit between two
    releases says ``0.3.0.dev0``, so comparing versions reported "up to date" to a crab that was
    thirty commits and one whole rename behind. pip and uv both record the resolved commit of a
    VCS install in ``direct_url.json``, and that can be compared.
    """
    package = Path(__file__).resolve().parent
    for info in sorted(package.parent.glob("hungry_crab-*.dist-info/direct_url.json")):
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        commit = as_dict(as_dict(data).get("vcs_info")).get("commit_id")
        if isinstance(commit, str) and commit:
            return commit
    return None


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
        data["commands"] = [display_command(command) for command in self.commands]
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
            "running_python": sys.executable,
            "path_cli": shutil.which("crab"),
        }


def check_index_cli(kind: str, release: Release) -> Component:
    """A CLI installed from the package index, against the latest release there."""
    installed = __version__
    command = upgrade_command(kind)
    if release.error or release.version is None:
        detail = f"cannot read PyPI: {release.error or 'no release'}"
        return Component("crab CLI", installed, None, UNKNOWN, detail, [command])
    latest = release.version
    if latest == installed:
        return Component("crab CLI", installed, latest, OK, "the latest release on PyPI")
    here, there = _version_key(installed), _version_key(latest)
    if here is None or there is None:
        detail = f"PyPI has {latest}, which cannot be compared with {installed}"
        return Component("crab CLI", installed, latest, UNKNOWN, detail, [command])
    if there > here:
        detail = f"PyPI has {latest}"
        return Component("crab CLI", installed, latest, OUTDATED, detail, [command])
    detail = f"newer than the latest release on PyPI ({latest})"
    return Component("crab CLI", installed, latest, OK, detail)


def check_cli(remote: Remote, *, index: PyPIClient | None = None) -> Component:
    """The CLI against master for a git install, or against PyPI for an index install.

    PyPI is asked only when the crab was installed from the index, through ``index``.
    """
    kind = cli_install_kind()
    installed = __version__
    command = ["uv", "tool", "install", "--force", REQUIREMENT]
    if kind == "editable":
        detail = "editable install from a local checkout; `git pull` updates it"
        return Component("crab CLI", installed, remote.cli_version, OK, detail)
    if install_source() == "index":
        return check_index_cli(kind, fetch_release(index))
    if remote.error:
        return Component("crab CLI", installed, None, UNKNOWN, remote.error, [command])
    if remote.cli_version and remote.cli_version != installed:
        detail = f"master is at {remote.cli_version}"
        return Component("crab CLI", installed, remote.cli_version, OUTDATED, detail, [command])
    here = installed_commit()
    if here and remote.sha and here != remote.sha:
        detail = (
            f"same version as master, but installed from {here[:7]} and master is at "
            f"{remote.short_sha} ({remote.date})"
        )
        return Component("crab CLI", installed, remote.cli_version, OUTDATED, detail, [command])
    detail = "same version as master"
    if remote.short_sha:
        detail += f" ({remote.short_sha}, {remote.date})"
    if here is None and remote.sha:
        # No commit is not evidence of equality. Every commit of a development series carries
        # the same version string, so without PEP 610 provenance the only honest answer is that
        # nobody knows — and `OK` is not actionable, which turned "reinstall to be sure" into
        # "Nothing to do." one line later.
        detail += "; the install records no commit, so reinstall to be sure"
        return Component("crab CLI", installed, remote.cli_version, UNKNOWN, detail, [command])
    return Component("crab CLI", installed, remote.cli_version, OK, detail, [command])


def check_agent(
    agent: AgentSpec, remote: Remote, *, runner: Callable[[Sequence[str]], tuple[bool, str]]
) -> Component:
    name = f"{agent.label} plugin"
    available = remote.plugin_version_for(agent)
    if shutil.which(agent.exe) is None:
        return Component(name, None, available, ABSENT, f"`{agent.exe}` not on PATH")
    ok, output = runner(agent.command(agent.list_args))
    if not ok:
        return Component(name, None, available, UNKNOWN, output[-200:])
    try:
        payload = json.loads(output or "null")
    except ValueError:
        return Component(name, None, available, UNKNOWN, "unreadable plugin list")
    installed = plugin_version(agent, payload)
    if installed is None:
        commands = [agent.command(agent.add_marketplace), agent.command(agent.install)]
        detail = f"`{agent.exe}` is here but the plugin is not installed"
        return Component(name, None, available, MISSING, detail, commands)
    commands = [agent.command(agent.refresh_marketplace), agent.command(agent.update)]
    if remote.error:
        return Component(name, installed, available, UNKNOWN, remote.error, commands)
    if available is None:
        return Component(
            name,
            installed,
            None,
            UNKNOWN,
            f"master's {agent.label} manifest has no version",
            commands,
        )
    if available != installed:
        detail = f"marketplace has {available}"
        return Component(name, installed, available, OUTDATED, detail, commands)
    revision = plugin_revision(agent, payload)
    if revision is None or remote.sha is None:
        return Component(
            name,
            installed,
            available,
            UNKNOWN,
            "installed revision is UNKNOWN; version equality does not verify hooks",
            commands,
        )
    if revision != remote.sha:
        if agent.key == "claude":
            commands = [
                agent.command(agent.refresh_marketplace),
                agent.command(("plugin", "uninstall", PLUGIN_ID)),
                agent.command(agent.install),
            ]
        return Component(
            name,
            installed,
            available,
            OUTDATED,
            f"installed revision {revision}; master {remote.sha}; restart the host after refresh",
            commands,
        )
    return Component(
        name, installed, available, OK, f"verified installed revision {revision}", commands
    )


def check(
    *,
    client: GitHubClient | None = None,
    runner: Callable[[Sequence[str]], tuple[bool, str]] | None = None,
    index: PyPIClient | None = None,
) -> UpdateReport:
    call = runner or (lambda args: run_command(args))
    remote = fetch_remote(client)
    components = [check_cli(remote, index=index)]
    components += [check_agent(agent, remote, runner=call) for agent in AGENTS]
    return UpdateReport(components, remote, executed=False)


def apply(
    report: UpdateReport,
    *,
    runner: Callable[[Sequence[str]], tuple[bool, str]] | None = None,
    log: Callable[[str], None] = _noop,
) -> UpdateReport:
    """Run what is safe: plugin work always, the CLI only when it is not this process.

    A uv tool or pipx app is the running process, and so is an install from the package index
    wherever it lives: its upgrade replaces the files the crab is running from.
    """
    call = runner or (lambda args: run_command(args))
    self_install = (
        uv_tool_receipt() is not None or pipx_metadata() is not None or install_source() == "index"
    )
    for component in report.components:
        if component.status not in ACTIONABLE or not component.commands:
            continue
        if component.name == "crab CLI":
            if self_install:
                component.detail = (
                    "run the command yourself: the crab cannot replace itself while it is running"
                )
                continue
            program = component.commands[0][0]
            if shutil.which(program) is None:
                component.detail = f"{program} is not on PATH"
                continue
        for command in component.commands:
            log("$ " + display_command(command))
            ok, output = call(command)
            component.ran.append(
                {"command": display_command(command), "ok": ok, "output": output[-400:]}
            )
            if not ok:
                component.detail = f"failed: {output.strip()[-200:]}"
                break
        else:
            agent = next(
                (item for item in AGENTS if component.name == f"{item.label} plugin"), None
            )
            if agent is None:
                component.status = OK
                component.detail = "updated"
            else:
                verified = check_agent(agent, report.remote, runner=call)
                component.status = verified.status
                component.detail = verified.detail
    report.executed = True
    return report


def format_report(report: UpdateReport) -> str:
    lines = [f"{'component':<22}{'installed':<12}{'available':<12}status"]
    for component in report.components:
        lines.append(
            f"{component.name:<22}{component.installed or '-':<12}"
            f"{component.available or '?':<12}{component.status}"
        )
        if component.detail:
            lines.append(f"{'':<22}{component.detail}")
        if component.name.endswith(" plugin"):
            lines.append(
                f"{'':<22}host hook activation: UNKNOWN; verify in a restarted agent session"
            )
        for entry in component.ran:
            mark = "ok" if entry["ok"] else "FAILED"
            lines.append(f"{'':<22}{mark}: {entry['command']}")
    if report.components:
        lines.append(f"\nRunning Python: {sys.executable}")
        lines.append(f"crab on PATH: {shutil.which('crab') or 'not found'}")
    if report.remote.error:
        lines.append(f"\ncould not read master: {report.remote.error}")
    pending = [c for c in report.actionable if not c.ran]
    if pending:
        lines.append("\nRun:")
        for component in pending:
            for command in component.commands:
                lines.append("  " + display_command(command))
    elif report.executed:
        lines.append("\nEverything that could be updated here was updated.")
    else:
        lines.append("\nNothing to do.")
    return "\n".join(lines)
