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


def test_hook_manifest_uses_the_packaged_guard_executable() -> None:
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entry = next(item for item in hooks["hooks"]["PreToolUse"] if item["matcher"] == "Bash")
    handler = entry["hooks"][0]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert handler["command"] == "crab-prey-guard"
    assert scripts[handler["command"]] == "hungry_crab.prey_guard:main"
    assert handler["timeout"] == 5
