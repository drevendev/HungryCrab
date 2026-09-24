"""Fail-closed prey-cache shell guard (HungryCrab#85)."""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from hungry_crab.prey_guard import event_guard_reason, guard_reason, main

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/tmp/crab-prey")
PREY = "/tmp/crab-prey/github/acme/widget/repo"


@pytest.mark.parametrize(
    "command",
    [
        f"{PREY}/bin/tool --version",
        f"{PREY}/bin/cat {PREY}/README.md",
        f'"$CRAB_CACHE_DIR/github/acme/widget/repo/bin/cat" {PREY}/README.md',
        f"PATH={PREY}/bin:$PATH cat {PREY}/README.md",
        f"LD_PRELOAD={PREY}/evil.so cat {PREY}/README.md",
        f"LC_ALL=C cat {PREY}/README.md",
        f"cd {PREY} && ./install.sh",
        f"python {PREY}/setup.py",
        'python "$CRAB_CACHE_DIR/github/acme/widget/repo/setup.py"',
        "python ${CRAB_CACHE_DIR}/github/acme/widget/repo/setup.py",
        r"python %CRAB_CACHE_DIR%\github\acme\widget\repo\setup.py",
        r"python $env:CRAB_CACHE_DIR\github\acme\widget\repo\setup.py",
        'echo "$(python ~/.cache/hungry-crab/github/acme/widget/repo/setup.py)"',
        "cat <(python ~/.cache/hungry-crab/github/acme/widget/repo/setup.py)",
        "printf '%s\\n' `~/.cache/hungry-crab/github/acme/widget/repo/bin/tool`",
        (
            "find ~/.cache/hungry-crab/github/acme/widget/repo -type f -exec "
            "~/.cache/hungry-crab/github/acme/widget/repo/bin/tool {} \\;"
        ),
        f"git -C {PREY} difftool --extcmd={PREY}/bin/tool HEAD~ HEAD",
        f"git -C {PREY} mergetool",
        f"git -c core.pager={PREY}/bin/tool -C {PREY} log",
        f"git -C {PREY} log --ext-diff",
        f"rg --pre={PREY}/bin/tool needle {PREY}",
    ],
)
def test_cache_touching_execution_or_unknown_shapes_fail_closed(command: str) -> None:
    reason = guard_reason(command, root=CACHE)
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize(
    "command",
    [
        f"cat {PREY}/README.md",
        f"head -50 {PREY}/pyproject.toml",
        f"tail -20 {PREY}/README.md",
        f"wc -l {PREY}/README.md",
        f"rg -n TODO {PREY}",
        f"grep -R TODO {PREY}",
        f"ls -la {PREY}",
        f"stat {PREY}/README.md",
        f"git -C {PREY} log --oneline -5",
        f"git --no-pager -C {PREY} rev-parse HEAD",
        f"git -C {PREY} for-each-ref --format=%(refname)",
        f"git -C {PREY} status --short",
    ],
)
def test_small_audited_read_only_shapes_remain_allowed(command: str) -> None:
    assert guard_reason(command, root=CACHE) is None


def test_bare_reader_remains_allowed_when_cwd_is_inside_cache() -> None:
    assert guard_reason("cat README.md", root=CACHE, cwd=Path(PREY)) is None


def test_guard_has_no_opinion_without_a_cache_reference() -> None:
    assert guard_reason("python -m pytest", root=CACHE) is None
    assert guard_reason("git difftool --extcmd=./tool HEAD~ HEAD", root=CACHE) is None


def test_malformed_cache_touching_shell_fails_closed() -> None:
    command = "cat '~/.cache/hungry-crab/github/acme/widget/repo/README.md"
    assert guard_reason(command, root=CACHE) is not None


def test_hook_denies_a_valid_bash_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_CACHE_DIR", str(CACHE))
    event = {"tool_name": "Bash", "tool_input": {"command": f"python {PREY}/setup.py"}}
    assert event_guard_reason(event) is not None
    assert main(stdin=io.StringIO(json.dumps(event))) == 2


def test_hook_allows_a_read_only_bash_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_CACHE_DIR", str(CACHE))
    event = {"tool_name": "Bash", "tool_input": {"command": f"cat {PREY}/README.md"}}
    assert event_guard_reason(event) is None
    assert main(stdin=io.StringIO(json.dumps(event))) == 0


def test_hook_denies_bash_input_it_cannot_inspect() -> None:
    assert event_guard_reason({"tool_name": "Bash", "tool_input": None}) is not None
    assert event_guard_reason({"tool_name": "Bash", "tool_input": {}}) is not None


def test_non_bash_event_is_outside_this_guard() -> None:
    event = {"tool_name": "Read", "tool_input": {"command": f"python {PREY}/setup.py"}}
    assert event_guard_reason(event, root=CACHE) is None


def test_hook_manifest_runs_the_packaged_guard_through_the_launcher() -> None:
    """The Bash hook runs this module, from the plugin's own ``src/``, not a console script.

    ``hooks/guard.py`` maps ``prey`` to the same module the ``crab-prey-guard`` console script
    names, so the hook and the manual command are one guard; the launcher's own behaviour is
    covered in ``tests/test_hooks.py``.
    """
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entry = next(item for item in hooks["hooks"]["PreToolUse"] if item["matcher"] == "Bash")
    handler = entry["hooks"][0]
    assert '"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"' in handler["command"]
    assert '"$g" prey;' in handler["command"]
    assert handler["timeout"] >= 5
    launcher = (ROOT / "hooks" / "guard.py").read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert '"prey": "hungry_crab.prey_guard"' in launcher
    assert scripts["crab-prey-guard"] == "hungry_crab.prey_guard:main"


@pytest.mark.parametrize(
    "command",
    [
        f"cat {PREY}/README.md\npython {PREY}/setup.py",
        f"cat {PREY}/README.md\r\npython {PREY}/setup.py",
        f"git -C {PREY} log -1\nmake -C {PREY}",
        f"cat {PREY}/README.md\n{PREY}/bin/tool",
        f"cat {PREY}/README.md\n",
    ],
)
def test_a_line_break_is_a_command_separator(command: str) -> None:
    """`shlex` reads a newline as whitespace; the shell reads it as `;`."""
    reason = guard_reason(command, root=CACHE)
    assert reason is not None
    assert "multi-line" in reason


@pytest.mark.parametrize(
    "command",
    [
        f"git -C {PREY} log -1 --format=%B --output=/tmp/x.sh",
        f"git -C {PREY} log -1 --format=%B --output /tmp/x.sh",
        f"git -C {PREY} show --output=/tmp/x.sh HEAD",
        f"git -C {PREY} grep -O needle",
        f"git -C {PREY} grep -Ovim needle",
        f"git -C {PREY} grep --open-files-in-pager=vim needle",
        f"git -C {PREY} show --ext-diff HEAD",
        f"git -C {PREY} diff --textconv HEAD~ HEAD",
    ],
)
def test_git_options_that_write_or_run_something_fail_closed(command: str) -> None:
    assert guard_reason(command, root=CACHE) is not None


@pytest.mark.parametrize(
    "command",
    [
        f"git -C {PREY} show --stat HEAD",
        f"git -C {PREY} show HEAD:README.md",
        f"git -C {PREY} blame README.md",
        f"git -C {PREY} diff HEAD~ HEAD -- src/",
        f"git -C {PREY} shortlog -sn",
        f"git -C {PREY} describe --tags",
        f"git -C {PREY} show-ref --heads",
        f"git -C {PREY} grep -n TODO",
    ],
)
def test_the_historians_read_only_git_verbs_are_allowed(command: str) -> None:
    """`agents/crab-historian.md` tells the agent to run these in the clone."""
    assert guard_reason(command, root=CACHE) is None


@pytest.mark.parametrize(
    "command",
    [
        "python /tmp/crab-prey-other/setup.py",
        "python /tmp/crab-prey.old/setup.py",
        "python /tmp/crab-prey_backup/setup.py",
        "python ~/.cache/hungry-crab-other/setup.py",
    ],
)
def test_a_sibling_that_starts_with_the_cache_name_is_not_the_cache(command: str) -> None:
    assert guard_reason(command, root=CACHE) is None


@pytest.mark.parametrize(
    "command",
    [
        "python /tmp/crab-prey/setup.py",
        'python "/tmp/crab-prey"/setup.py',
        "ls /tmp/crab-prey",
        'ls "/tmp/crab-prey"',
    ],
)
def test_the_cache_root_itself_is_still_recognised(command: str) -> None:
    expected_allowed = command.startswith("ls")
    assert (guard_reason(command, root=CACHE) is None) is expected_allowed


def test_hook_treats_undecodable_input_like_any_other_transport_failure() -> None:
    """A traceback would exit 1, which the agent also reads as allow, only louder."""
    stream = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{"), encoding="utf-8")
    assert main(stdin=stream) == 0
