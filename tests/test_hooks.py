"""The hook launcher: the guards run from the plugin's own source tree.

Claude Code's execution of ``hooks/hooks.json`` cannot be reproduced here, but everything
below that line can: the launcher's exit-code contract, its independence from an installed
package, the handover from an interpreter older than 3.11, and the shell line itself under
``sh``. The contract under test is the one the launcher's docstring states — ``2`` is a
guard's refusal and nothing else, ``1`` with a reason is "could not guard".
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "hooks" / "guard.py"
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
SH = shutil.which("sh")
CLEANROOM_MATCHER = "Read|Grep|Glob|Write|Edit"


def _entry(matcher: str) -> dict[str, Any]:
    entries = [entry for entry in HOOKS["hooks"]["PreToolUse"] if entry["matcher"] == matcher]
    assert len(entries) == 1, f"one PreToolUse entry expected for {matcher!r}"
    entry: dict[str, Any] = entries[0]
    return entry


def _hook_command(matcher: str) -> str:
    handlers = _entry(matcher)["hooks"]
    assert len(handlers) == 1
    handler = handlers[0]
    assert handler["type"] == "command"
    return str(handler["command"])


def _events(cache: Path) -> dict[str, dict[str, Any]]:
    prey_file = (cache / "github" / "owner" / "repo" / "setup.py").as_posix()
    return {
        "bash-runs-prey": {"tool_name": "Bash", "tool_input": {"command": f"python {prey_file}"}},
        "bash-plain": {"tool_name": "Bash", "tool_input": {"command": "git status"}},
        "cleanroom-reads-prey": {
            "tool_name": "Read",
            "agent_type": "crab:crab-cleanroom-impl",
            "tool_input": {"file_path": prey_file},
        },
        "other-agent-reads-prey": {
            "tool_name": "Read",
            "agent_type": "general-purpose",
            "tool_input": {"file_path": prey_file},
        },
    }


def _run(
    argv: list[str], event: object, cache: Path, *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CRAB_CACHE_DIR"] = str(cache)
    env.pop("CRAB_GUARD_HANDOVER", None)
    return subprocess.run(
        argv,
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
        timeout=120,
    )


def _launcher_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("crab_hook_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- hooks.json ---------------------------------------------------------------------------


@pytest.mark.parametrize(("matcher", "which"), [("Bash", "prey"), (CLEANROOM_MATCHER, "cleanroom")])
def test_each_hook_runs_its_guard_through_the_launcher(matcher: str, which: str) -> None:
    command = _hook_command(matcher)
    assert '"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"' in command
    assert f'"$g" {which};' in command
    # Not the console scripts: those exist only where the CLI is installed, and where an
    # application-control policy refuses their unsigned launchers they exit 126, which
    # Claude Code reads as "proceed".
    assert "crab-prey-guard" not in command
    assert "crab-cleanroom-guard" not in command
    # Every exit the shell line performs itself is "could not guard", never a refusal.
    assert "exit 2" not in command
    assert "unguarded" in command
    assert _entry(matcher)["hooks"][0]["timeout"] >= 5


# --- the launcher's exit codes ------------------------------------------------------------


@pytest.mark.parametrize(
    ("which", "event", "expected", "reason"),
    [
        ("prey", "bash-runs-prey", 2, "refusing unsafe prey-cache command"),
        ("prey", "bash-plain", 0, ""),
        ("cleanroom", "cleanroom-reads-prey", 2, "Clean-room tool call denied"),
        ("cleanroom", "other-agent-reads-prey", 0, ""),
    ],
)
def test_launcher_exit_code_is_the_guard_decision(
    which: str, event: str, expected: int, reason: str, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    result = _run([sys.executable, "-I", str(LAUNCHER), which], _events(cache)[event], cache)
    assert result.returncode == expected, result.stderr
    # The guard's own reason reaches stderr unchanged; an allowed call says nothing.
    assert reason in result.stderr
    if expected == 0:
        assert result.stderr == ""


def test_launcher_needs_no_installed_package(tmp_path: Path) -> None:
    # ``-S`` leaves site-packages out, where the editable install lives; only the launcher's
    # own sys.path entry can make ``hungry_crab`` importable.
    cache = tmp_path / "cache"
    event = _events(cache)["bash-runs-prey"]
    result = _run([sys.executable, "-I", "-S", str(LAUNCHER), "prey"], event, cache, cwd=tmp_path)
    assert result.returncode == 2, result.stderr


def test_launcher_without_a_guard_name_proceeds_unguarded(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    result = _run([sys.executable, "-I", str(LAUNCHER)], _events(cache)["bash-runs-prey"], cache)
    assert result.returncode == 1
    assert "usage" in result.stderr and "unguarded" in result.stderr


def test_launcher_without_a_source_tree_proceeds_unguarded(tmp_path: Path) -> None:
    stray = tmp_path / "plugin" / "hooks" / "guard.py"
    stray.parent.mkdir(parents=True)
    shutil.copy(LAUNCHER, stray)
    cache = tmp_path / "cache"
    event = _events(cache)["bash-runs-prey"]
    result = _run([sys.executable, "-I", "-S", str(stray), "prey"], event, cache, cwd=tmp_path)
    assert result.returncode == 1, result.stderr
    assert "cannot load" in result.stderr and "unguarded" in result.stderr


# --- an interpreter older than 3.11 -------------------------------------------------------


def test_an_old_interpreter_hands_over_once(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(sys, "version_info", (3, 9, 0, "final", 0))
    monkeypatch.delenv("CRAB_GUARD_HANDOVER", raising=False)
    monkeypatch.setattr(launcher, "_newer_interpreter", lambda: "/opt/py/bin/python3.12")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_call(argv: list[str], env: dict[str, str]) -> int:
        calls.append((argv, env))
        return 2

    monkeypatch.setattr(launcher.subprocess, "call", fake_call)
    assert launcher.main(["guard.py", "prey"]) == 2
    ((argv, env),) = calls
    assert argv == ["/opt/py/bin/python3.12", "-I", str(LAUNCHER.resolve()), "prey"]
    assert env["CRAB_GUARD_HANDOVER"] == "1"


def test_a_handover_that_is_still_old_gives_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(sys, "version_info", (3, 9, 0, "final", 0))
    monkeypatch.setenv("CRAB_GUARD_HANDOVER", "1")
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **k: pytest.fail("no loop"))
    assert launcher.main(["guard.py", "cleanroom"]) == 1
    assert "unguarded" in capsys.readouterr().err


def test_no_newer_interpreter_gives_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(sys, "version_info", (3, 9, 0, "final", 0))
    monkeypatch.delenv("CRAB_GUARD_HANDOVER", raising=False)
    monkeypatch.setattr(launcher, "_newer_interpreter", lambda: None)
    assert launcher.main(["guard.py", "prey"]) == 1
    err = capsys.readouterr().err
    assert "3.9" in err and "unguarded" in err


def test_newer_interpreter_prefers_a_versioned_python_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(
        launcher.shutil,
        "which",
        lambda name: "/usr/bin/python3.12" if name == "python3.12" else None,
    )
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: pytest.fail("uv not needed"))
    assert launcher._newer_interpreter() == "/usr/bin/python3.12"


def test_newer_interpreter_asks_uv_when_path_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(
        launcher.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="/opt/py/bin/python3.12\n", stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    assert launcher._newer_interpreter() == "/opt/py/bin/python3.12"
    assert seen == [["/usr/bin/uv", "python", "find", "--no-project", ">=3.11"]]


def test_newer_interpreter_is_none_when_uv_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(
        launcher.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 2, stdout="", stderr="error: none"),
    )
    assert launcher._newer_interpreter() is None
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher._newer_interpreter() is None


# --- the shell line itself ----------------------------------------------------------------


@pytest.mark.skipif(SH is None, reason="no POSIX shell to run the hook line")
def test_hook_line_refuses_through_the_launcher(tmp_path: Path) -> None:
    assert SH is not None
    command = _hook_command("Bash").replace("${CLAUDE_PLUGIN_ROOT}", ROOT.as_posix())
    cache = tmp_path / "cache"
    result = _run([SH, "-c", command], _events(cache)["bash-runs-prey"], cache, cwd=tmp_path)
    assert result.returncode == 2, result.stderr
    assert "refusing" in result.stderr


@pytest.mark.skipif(SH is None, reason="no POSIX shell to run the hook line")
def test_hook_line_lets_an_ordinary_command_through(tmp_path: Path) -> None:
    assert SH is not None
    command = _hook_command(CLEANROOM_MATCHER).replace("${CLAUDE_PLUGIN_ROOT}", ROOT.as_posix())
    cache = tmp_path / "cache"
    event = _events(cache)["other-agent-reads-prey"]
    result = _run([SH, "-c", command], event, cache, cwd=tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(SH is None, reason="no POSIX shell to run the hook line")
def test_hook_line_without_a_launcher_proceeds_unguarded(tmp_path: Path) -> None:
    assert SH is not None
    nowhere = (tmp_path / "nowhere").as_posix()
    command = _hook_command("Bash").replace("${CLAUDE_PLUGIN_ROOT}", nowhere)
    cache = tmp_path / "cache"
    result = _run([SH, "-c", command], _events(cache)["bash-runs-prey"], cache, cwd=tmp_path)
    assert result.returncode == 1, result.stderr
    assert "missing" in result.stderr and "unguarded" in result.stderr
