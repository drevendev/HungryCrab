"""Both hook entry points survive input they cannot decode.

Claude Code blocks a tool call on exit code 2 only. A hook that dies with a traceback exits 1,
which is read as "allow" like a clean 0, so an undecodable event must take the same documented
transport-failure path as malformed JSON instead of crashing.
"""

from __future__ import annotations

import io
import sys

import pytest

from hungry_crab import cleanroom_guard, prey_guard


def _undecodable() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(b'\xff\xfe{"tool_name": "Bash"}'), encoding="utf-8")


def test_prey_guard_main_returns_zero_on_undecodable_stdin() -> None:
    assert prey_guard.main(stdin=_undecodable()) == 0


def test_cleanroom_guard_main_returns_zero_on_undecodable_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", _undecodable())
    assert cleanroom_guard.main() == 0
