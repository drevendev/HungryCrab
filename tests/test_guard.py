"""Never execute prey, asked of one shell command at a time (HungryCrab#72)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from hungry_crab.cli import main
from hungry_crab.guard import EXECUTION_VERBS, executes_prey, run_hook

CACHE = Path("C:/Users/someone/.cache/hungry-crab")
PREY = f"{CACHE}/github/acme/widget/repo"


@pytest.mark.parametrize(
    "command",
    [
        f"python {PREY}/setup.py install",
        f"cd {PREY} && npm install",
        f"bash {PREY}/scripts/build.sh",
        f"cat {PREY}/install.sh | sh",
        f"FOO=1 python3 {PREY}/x.py",
        f"uv run --directory {PREY} pytest",
        f"make -C {PREY} all",
        f"docker build {PREY}",
        f"/usr/bin/python {PREY}/x.py",
    ],
)
def test_running_something_out_of_the_cache_is_refused(command: str) -> None:
    reason = executes_prey(command, root=CACHE)
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize(
    "command",
    [
        f"cat {PREY}/README.md",
        f"rg python {PREY}",
        f"rg -n 'npm install' {PREY}",
        f"git -C {PREY} log --oneline -5",
        f"ls -la {PREY}",
        f"wc -l {PREY}/package.json",
        f"head -50 {PREY}/pyproject.toml",
        "python -m pytest tests/",
        "npm install",
        "",
        "   ",
    ],
)
def test_reading_the_cache_and_working_elsewhere_are_allowed(command: str) -> None:
    """Reading the cache is the whole point of the crab, and the maw is not the cache."""
    assert executes_prey(command, root=CACHE) is None


def test_the_cache_is_recognised_by_its_tail_as_well() -> None:
    """An operator writes ~ or $HOME; the rule has to survive both."""
    assert executes_prey("python ~/.cache/hungry-crab/x/repo/setup.py", root=CACHE)
    assert executes_prey(r"python C:\Users\other\.cache\hungry-crab\x\repo\s.py", root=CACHE)


def test_git_is_not_an_execution_verb() -> None:
    """The miners and the operator read the cache with it all day."""
    assert "git" not in EXECUTION_VERBS


def test_the_hook_denies_with_exit_2() -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": f"python {PREY}/setup.py"}}
    assert run_hook(stdin=io.StringIO(json.dumps(event))) == 2


def test_the_hook_allows_what_it_should() -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": f"cat {PREY}/README.md"}}
    assert run_hook(stdin=io.StringIO(json.dumps(event))) == 0


@pytest.mark.parametrize("payload", ["", "not json at all", "[]", '{"tool_input": 7}', "null"])
def test_a_payload_it_cannot_read_allows(payload: str) -> None:
    """A hook that blocks every command is a hook that gets uninstalled."""
    assert run_hook(stdin=io.StringIO(payload)) == 0


def test_the_command_line_says_what_the_rule_would_do(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_CACHE_DIR", str(CACHE))
    assert main(["guard", f"cat {PREY}/README.md"]) == 0
    assert capsys.readouterr().out.strip() == "allowed"
    assert main(["guard", f"python {PREY}/setup.py"]) == 2
    assert "denied" in capsys.readouterr().out


def test_the_hook_manifest_is_well_formed() -> None:
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entries = data["hooks"]["PreToolUse"]
    assert entries[0]["matcher"] == "Bash"
    assert entries[0]["hooks"][0]["command"] == "crab guard --hook"


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        (f"rg -l TODO {PREY} | xargs cat", False),
        (f"rg -l x {PREY} | xargs sh -c 'echo'", True),
        (f"env FOO=1 python {PREY}/s.py", True),
        (f"env | grep {PREY}", False),
        (f"sudo make -C {PREY}", True),
        (f"timeout 5 node {PREY}/index.js", True),
        (f"nice 10 make -C {PREY}", True),
    ],
)
def test_a_wrapper_is_judged_by_what_it_wraps(command: str, denied: bool) -> None:
    """`xargs cat` reads and `xargs sh -c` executes.

    Calling every wrapper an execution verb refused an ordinary way of reading the cache; calling
    none of them left a hole a pipe could walk through.
    """
    assert (executes_prey(command, root=CACHE) is not None) is denied


def test_uv_stays_refused_and_the_price_is_named() -> None:
    """`uv sync` inside the cache is exactly what must not happen.

    The cost is that `uv run crab digest <cache path>` is refused too. Calling `crab` directly is
    what the documentation does everywhere, so the trade is cheap — but it is a trade, and it
    belongs in a test rather than in someone's surprise.
    """
    assert executes_prey(f"uv sync --directory {PREY}", root=CACHE)
    assert executes_prey(f"uv run crab digest {PREY}", root=CACHE)
    assert executes_prey(f"crab digest {PREY}", root=CACHE) is None
