"""Run one of Hungry Crab's ``PreToolUse`` guards from the plugin's own source tree.

Claude Code starts this file through the command in ``hooks/hooks.json`` with the first Python
the shell finds; it needs nothing installed. The guards import only the standard library and
the plugin ships ``src/``, so ``python hooks/guard.py prey`` works from a plugin-only install,
from a machine whose application-control policy refuses the unsigned console-script launchers,
and from a checkout of this repository alike — and it always runs the guard that shipped with
the plugin, not whatever version ``PATH`` happens to hold.

The exit code is the hook contract. ``2`` refuses the tool call, and the guard writes the
reason to stderr. ``0`` lets it through. ``1`` means the guard could not run — an interpreter
older than 3.11 with no newer one to hand over to, or a source tree that does not import — and
Claude Code shows that message to the user and proceeds. Nothing in this file exits ``2`` for
any reason other than a guard's decision: a hook that blocked every tool call over its own
environment would be switched off, not fixed.

Usage: ``guard.py {prey|cleanroom}``
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

GUARDS = {"prey": "hungry_crab.prey_guard", "cleanroom": "hungry_crab.cleanroom_guard"}
MINIMUM = (3, 11)
HANDOVER_ENV = "CRAB_GUARD_HANDOVER"
SOURCE = Path(__file__).resolve().parent.parent / "src"


def _complain(message: str) -> int:
    sys.stderr.write(f"crab: {message}; the tool call proceeds unguarded\n")
    return 1


def _newer_interpreter() -> str | None:
    """A Python that satisfies ``MINIMUM``, located without running anything from the prey."""
    for name in ("python3.14", "python3.13", "python3.12", "python3.11"):
        found = shutil.which(name)
        if found:
            return found
    uv = shutil.which("uv")
    if uv is None:
        return None
    try:
        result = subprocess.run(
            [uv, "python", "find", "--no-project", ">=3.11"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if result.returncode == 0 and lines else None


def _hand_over(which: str) -> int:
    """Re-run this launcher under a newer interpreter, with stdin still unread."""
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if os.environ.get(HANDOVER_ENV):
        return _complain(f"the {which} guard needs Python 3.11+ and the handover found {version}")
    newer = _newer_interpreter()
    if newer is None:
        return _complain(
            f"the {which} guard needs Python 3.11+, this is {version}, and no newer interpreter "
            "is on PATH or known to uv"
        )
    env = dict(os.environ)
    env[HANDOVER_ENV] = "1"
    try:
        return subprocess.call([newer, "-I", str(Path(__file__).resolve()), which], env=env)
    except OSError as exc:
        return _complain(f"could not start {newer} for the {which} guard: {exc}")


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else ""
    module_name = GUARDS.get(which)
    if module_name is None:
        return _complain("usage: guard.py {prey|cleanroom}")
    if sys.version_info < MINIMUM:
        return _hand_over(which)
    sys.path.insert(0, str(SOURCE))
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # any import failure means "cannot guard", never "refuse"
        return _complain(f"cannot load {module_name} from {SOURCE}: {exc}")
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
