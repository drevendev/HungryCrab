"""Mechanical enforcement of the one rule that has no exceptions: never execute prey.

Rule 3 of ``AGENTS.md`` and the ``CLAUDE.md`` line about the cache both say it in prose, and
prose is what an agent skips under pressure. This module answers one question about one shell
command — would running it execute something out of the prey cache? — and ``crab guard --hook``
wires that answer into a ``PreToolUse`` hook.

The question is asked of the command's *shape*, never by running it.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from .cache import cache_root

# Anything whose job is to run code. `git` is deliberately absent: the miners and the operator
# read the cache with it all day, and a clone carries no hooks of its own to trigger.
EXECUTION_VERBS: frozenset[str] = frozenset(
    {
        "bash", "sh", "zsh", "fish", "dash", "ksh", "csh",
        "cmd", "powershell", "pwsh", "iex",
        "python", "python2", "python3", "py", "uv", "uvx", "pip", "pip3", "pipx", "poetry",
        "node", "npm", "npx", "pnpm", "yarn", "bun", "deno", "tsx", "ts-node",
        "ruby", "gem", "bundle", "rake", "perl", "php", "composer",
        "go", "cargo", "rustc", "dotnet", "java", "javac", "gradle", "gradlew", "mvn", "mvnw",
        "make", "cmake", "ninja", "meson", "scons", "just", "task",
        "docker", "podman", "docker-compose", "vagrant", "ansible", "terraform",
        "source", "eval", "exec",
    }
)  # fmt: skip

# Programs that run whatever they are handed. What matters is the argument, not the wrapper:
# `xargs cat` reads and `xargs sh -c` executes, and calling both of them execution would refuse
# an ordinary way of reading the cache.
#
# `uv` and `uvx` are not here. They install and build as readily as they run, and `uv sync`
# inside the cache is exactly what must not happen — so they stay refused, and the price is that
# `uv run crab digest <cache path>` is refused too. Call `crab` directly, as the docs do.
WRAPPER_VERBS: frozenset[str] = frozenset({"env", "xargs", "nice", "time", "timeout", "sudo",
                                           "doas", "stdbuf", "nohup"})  # fmt: skip

# The cache directory can be moved, and a command may name it in either slash. Matching the tail
# as well means the rule still holds when the operator writes ~ or $HOME rather than a full path.
_CACHE_TAIL = re.compile(r"[\\/]?\.cache[\\/]hungry-crab\b", re.IGNORECASE)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = re.compile(r"\|\||&&|[;|&\n]")
# `timeout 5 node x.js` and `nice 10 make`: the number belongs to the wrapper, not to the shell.
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def _mentions_cache(command: str, root: Path) -> bool:
    lowered = command.replace("\\", "/").lower()
    if str(root).replace("\\", "/").lower() in lowered:
        return True
    return _CACHE_TAIL.search(command) is not None


def _program(token: str) -> str:
    """``/usr/bin/python3`` and ``PYTHON.EXE`` are both ``python3`` and ``python``."""
    return Path(token.strip("'\"")).name.lower().removesuffix(".exe")


def _verbs(command: str) -> list[str]:
    """The program each pipeline segment actually runs, wrappers looked through."""
    found: list[str] = []
    for segment in _SEPARATORS.split(command):
        text = segment.strip()
        if not text:
            continue
        try:
            tokens = shlex.split(text, posix=True)
        except ValueError:
            tokens = text.split()
        wrapped = False
        for token in tokens:
            if _ASSIGNMENT.match(token) or token.startswith("-"):
                continue  # FOO=bar python x.py, and env -i python x.py
            name = _program(token)
            if name in WRAPPER_VERBS:
                wrapped = True
                continue  # the wrapper is not the program; the next word is
            if wrapped and _DURATION.match(name):
                continue  # timeout 5 node x.js
            found.append(name)
            break
    return found


def executes_prey(command: str, *, root: Path | None = None) -> str | None:
    """The reason this command must not run, or ``None`` when it may.

    Two conditions, both required. The command names the prey cache, and something in it runs
    code. Reading the cache is the whole point of the crab, so ``rg python <cache>`` is fine and
    ``python <cache>/setup.py`` is not — and so is ``cat <cache>/x.sh | sh``, where the cache and
    the interpreter sit in different segments of the same pipeline.
    """
    if not command.strip():
        return None
    where = root or cache_root()
    if not _mentions_cache(command, where):
        return None
    verbs = _verbs(command)
    running = [verb for verb in verbs if verb in EXECUTION_VERBS]
    if not running:
        return None
    return (
        f"this command would run `{running[0]}` against the prey cache. Prey is data, never "
        "code: read it with cat, rg or git, and never install, build or execute anything in it "
        "(AGENTS.md rule 3)"
    )


def _command_from_event(event: object) -> str:
    """Dig the shell command out of a PreToolUse payload without trusting its shape."""
    if not isinstance(event, dict):
        return ""
    for key in ("tool_input", "toolInput", "input", "parameters"):
        section = event.get(key)
        if isinstance(section, dict):
            for name in ("command", "cmd", "script"):
                value = section.get(name)
                if isinstance(value, str):
                    return value
    value = event.get("command")
    return value if isinstance(value, str) else ""


def run_hook(argv: Sequence[str] | None = None, *, stdin: IO[str] | None = None) -> int:
    """Read one hook event and answer it. Exit 2 denies; exit 0 allows.

    Malformed input allows. A hook that cannot parse its own payload has to fail towards the
    operator keeping their shell, because one that blocks every command is one that gets
    uninstalled, and an uninstalled hook protects nothing.
    """
    del argv
    stream = stdin if stdin is not None else sys.stdin
    try:
        raw = stream.read()
        event = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return 0
    command = _command_from_event(event)
    # cache_root() reads CRAB_CACHE_DIR, so a moved cache is still the cache.
    reason = executes_prey(command)
    if reason is None:
        return 0
    print(f"crab: refusing to execute prey: {reason}", file=sys.stderr)
    return 2
