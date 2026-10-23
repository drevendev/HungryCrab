"""Mechanical cache deny for Hungry Crab's clean-room implementer.

The clean-room agent may work in the maw from a code-free specification, but it must not
read, search, write, or execute anything in Hungry Crab's prey cache. Claude Code plugin
subagents cannot carry path-scoped tool permissions in their own frontmatter, so the plugin
routes their tool calls through this PreToolUse hook instead.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from .cache import cache_root
from .prey_guard import (
    canonical_path,
    glob_reaches,
    is_drive_path,
    names_root,
    read_hook_event,
)

CLEANROOM_AGENT_TYPE = "crab:crab-cleanroom-impl"

_ENV_CACHE_REF = re.compile(
    r"(?:\$\{?CRAB_CACHE_DIR\}?|%CRAB_CACHE_DIR%|\$env:CRAB_CACHE_DIR)",
    re.IGNORECASE,
)
_DEFAULT_CACHE_REF = re.compile(
    r"(?:^|[/\\])\.cache[/\\]hungry-crab(?:[/\\]|$)",
    re.IGNORECASE,
)
_DEFAULT_ROOT = Path("~") / ".cache" / "hungry-crab"


def _strings(value: object) -> Iterable[str]:
    """Yield every string nested in one tool input without assuming a tool schema."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _canonical(path: Path, *, cwd: Path) -> Path:
    # The prey guard's normaliser: a `\\?\` or `\\.\` device prefix and an 8.3 short name are
    # reduced to the spelling the cache root has before anything is compared.
    return canonical_path(path, cwd=cwd)


def _event_cwd(event: dict[object, object]) -> Path:
    process_cwd = Path.cwd().resolve(strict=False)
    raw = event.get("cwd")
    if not isinstance(raw, str):
        return process_cwd
    try:
        return _canonical(Path(raw), cwd=process_cwd)
    except (OSError, RuntimeError, ValueError):
        return process_cwd


def _configured_cache_root(*, cwd: Path) -> Path:
    return _canonical(cache_root(), cwd=cwd)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _mentions_cache(text: str, *, cwd: Path) -> bool:
    if _ENV_CACHE_REF.search(text) or _DEFAULT_CACHE_REF.search(text):
        return True

    try:
        root = _configured_cache_root(cwd=cwd)
        if names_root(text, root):
            return True
        candidate = _canonical(Path(text), cwd=cwd)
    except (OSError, RuntimeError, ValueError):
        # For this agent a path the guard cannot canonicalise is a refusal, like the tool
        # input it cannot inspect: allowing it would make canonicalisation the whole defence.
        return True
    return _is_within(candidate, root)


_PATH_KEYS = ("file_path", "path", "notebook_path")


def _off_drive_path(tool: object, tool_input: dict[object, object], *, cwd: Path) -> bool:
    """A share or device path that does not come down to a drive path is refused (Windows).

    `\\\\localhost\\C$\\...`, `\\\\?\\GLOBALROOT\\...` and `\\\\?\\Volume{...}\\...` all open
    files on a local drive, and Read, Grep and Glob accept them. The guard reduces the forms
    it knows to the drive path and compares that; it does not ask the network about any other
    share, so it cannot tell where one leads, and this agent has no need of shares.
    """
    if os.name != "nt":
        return False
    keys = (*_PATH_KEYS, "pattern") if tool == "Glob" else _PATH_KEYS
    for key in keys:
        value = tool_input.get(key)
        if not isinstance(value, str) or not value.startswith(("\\\\", "//", "\\??\\")):
            continue
        try:
            if not is_drive_path(_canonical(Path(value), cwd=cwd)):
                return True
        except (OSError, RuntimeError, ValueError):
            return True
    return False


def _search_reaches_cache(tool: object, tool_input: dict[object, object], *, cwd: Path) -> bool:
    """Grep and Glob search a scope; one that holds the cache searches the cache.

    Grep walks its ``path`` (the working directory without one), so a scope that is the cache
    root or an ancestor of it is refused. A Glob ``path`` is refused the same way, and its
    ``pattern`` is matched component by component from that scope: ``.cache/hungry-*/**`` from
    the home directory reaches a default cache. Both tools descend into dot directories, so the
    default root's place under ``~/.cache`` hides nothing.
    """
    if tool not in {"Grep", "Glob"}:
        return False
    path = tool_input.get("path")
    pattern = tool_input.get("pattern")
    try:
        # The default location counts even under a custom root, as the literal check above
        # does: a cache made before `CRAB_CACHE_DIR` was set is still prey.
        roots = {_configured_cache_root(cwd=cwd), _canonical(_DEFAULT_ROOT, cwd=cwd)}
        if path is None or path == "":
            base = cwd
        elif isinstance(path, str):
            base = _canonical(Path(path), cwd=cwd)
        else:
            return True
        for root in roots:
            if (path or tool == "Grep") and _is_within(root, base):
                return True
            if (
                tool == "Glob"
                and isinstance(pattern, str)
                and glob_reaches(pattern, base=base, root=root)
            ):
                return True
    except (OSError, RuntimeError, ValueError):
        return True
    return False


def cleanroom_guard_reason(event: object) -> str | None:
    """Return a denial reason for one clean-room tool event, otherwise ``None``.

    The hook deliberately ignores other agents. Once the event identifies the clean-room
    implementer, malformed tool input fails closed because an uninspectable tool call must not
    become a cache-access bypass.
    """
    if not isinstance(event, dict) or event.get("agent_type") != CLEANROOM_AGENT_TYPE:
        return None

    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return "Clean-room tool call denied: the tool input could not be inspected."

    cwd = _event_cwd(event)
    tool = event.get("tool_name")
    if (
        any(_mentions_cache(text, cwd=cwd) for text in _strings(tool_input))
        or _off_drive_path(tool, tool_input, cwd=cwd)
        or _search_reaches_cache(tool, tool_input, cwd=cwd)
    ):
        return (
            "Clean-room tool call denied: crab-cleanroom-impl may use the maw and its "
            "clean-room specification, but it may not access the Hungry Crab cache."
        )
    return None


def main() -> int:
    """Read one PreToolUse event from stdin; exit 2 when the clean-room call is denied."""
    try:
        event = read_hook_event()  # UTF-8 bytes, whatever the console code page is
    except (OSError, ValueError):
        # Without a valid event — unreadable, undecodable or malformed — we cannot establish
        # that this is the clean-room agent. Do not break unrelated plugin sessions on hook
        # transport/schema failure; a traceback would exit 1, which is also "allow".
        return 0

    reason = cleanroom_guard_reason(event)
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
