"""Mechanical cache deny for Hungry Crab's clean-room implementer.

The clean-room agent may work in the maw from a code-free specification, but it must not
read, search, write, or execute anything in Hungry Crab's prey cache. Claude Code plugin
subagents cannot carry path-scoped tool permissions in their own frontmatter, so the plugin
routes their tool calls through this PreToolUse hook instead.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

CLEANROOM_AGENT_TYPE = "crab:crab-cleanroom-impl"

_ENV_CACHE_REF = re.compile(
    r"(?:\$\{?CRAB_CACHE_DIR\}?|%CRAB_CACHE_DIR%|\$env:CRAB_CACHE_DIR)",
    re.IGNORECASE,
)
_DEFAULT_CACHE_REF = re.compile(
    r"(?:^|[/\\])\.cache[/\\]hungry-crab(?:[/\\]|$)",
    re.IGNORECASE,
)


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


def _configured_cache_root() -> str:
    override = os.environ.get("CRAB_CACHE_DIR")
    root = Path(override).expanduser() if override else Path.home() / ".cache" / "hungry-crab"
    return str(root).replace("\\", "/").rstrip("/").casefold()


def _mentions_cache(text: str) -> bool:
    if _ENV_CACHE_REF.search(text) or _DEFAULT_CACHE_REF.search(text):
        return True
    root = _configured_cache_root()
    normalized = text.replace("\\", "/").casefold()
    return bool(root and root in normalized)


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

    if any(_mentions_cache(text) for text in _strings(tool_input)):
        return (
            "Clean-room tool call denied: crab-cleanroom-impl may use the maw and its "
            "clean-room specification, but it may not access the Hungry Crab cache."
        )
    return None


def main() -> int:
    """Read one PreToolUse event from stdin; exit 2 when the clean-room call is denied."""
    try:
        event = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        # Without a valid event we cannot establish that this is the clean-room agent. Do not
        # break unrelated plugin sessions on hook transport/schema failure.
        return 0

    reason = cleanroom_guard_reason(event)
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
