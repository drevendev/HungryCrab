"""Capture the first native Windows relay failure in an isolated Python process.

The historical managed-local 0xC0000008 is unconfirmed. This diagnostic
re-executes the exact former failing quota test, preserving its first nonzero
process exit and stderr rather than hiding failures behind a passing rerun.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

_TARGET = "tests/test_acquisition.py::test_main_and_wiki_connections_share_one_real_stream_limit"
_ATTEMPTS = 8
_TIMEOUT_SECONDS = 45
_POST_KILL_DRAIN_SECONDS = 2


def _as_text(output: bytes | str | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _drain_killed_child(
    child: subprocess.Popen[str], first_timeout: subprocess.TimeoutExpired
) -> tuple[str, str, bool]:
    """Bound pipe draining after kill even when an escaped descendant holds EOF open."""
    try:
        stdout, stderr = child.communicate(timeout=_POST_KILL_DRAIN_SECONDS)
        return stdout, stderr, False
    except subprocess.TimeoutExpired as drain_timeout:
        stdout = drain_timeout.stdout if drain_timeout.stdout is not None else first_timeout.stdout
        stderr = drain_timeout.stderr if drain_timeout.stderr is not None else first_timeout.stderr
        return _as_text(stdout), _as_text(stderr), True


def test_post_kill_pipe_drain_is_bounded_when_descendant_keeps_pipe_open() -> None:
    child = Mock(spec=subprocess.Popen)
    original_timeout = subprocess.TimeoutExpired(["pytest"], _TIMEOUT_SECONDS, output=b"early")
    child.communicate.side_effect = subprocess.TimeoutExpired(
        ["pytest"], _POST_KILL_DRAIN_SECONDS, output=b"partial", stderr=b"open stderr"
    )

    stdout, stderr, blocked = _drain_killed_child(
        cast(subprocess.Popen[str], child), original_timeout
    )

    assert (stdout, stderr, blocked) == ("partial", "open stderr", True)
    child.communicate.assert_called_once_with(timeout=_POST_KILL_DRAIN_SECONDS)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native-handle diagnostic only")
def test_relay_select_teardown_preserves_first_native_process_failure() -> None:
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-X",
        "faulthandler",
        "-m",
        "pytest",
        "-q",
        "-s",
        "--maxfail=1",
        _TARGET,
    ]
    environment = (
        f"Python={sys.version}; executable={sys.executable}; "
        f"Windows={platform.win32_ver()!r}; platform={platform.platform()}"
    )

    for attempt in range(1, _ATTEMPTS + 1):
        child = subprocess.Popen(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = child.communicate(timeout=_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as first_timeout:
            child.kill()
            stdout, stderr, pipe_open = _drain_killed_child(child, first_timeout)
            drain_status = (
                "post-kill pipe EOF unavailable; possible escaped descendant"
                if pipe_open
                else "post-kill pipe drain completed"
            )
            pytest.fail(
                f"Relay child timed out on FIRST failing attempt {attempt}/{_ATTEMPTS}; "
                f"pid={child.pid}; timeout={_TIMEOUT_SECONDS}s; "
                f"post_kill_drain_timeout={_POST_KILL_DRAIN_SECONDS}s\n"
                f"{environment}\n{drain_status}\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}",
                pytrace=False,
            )

        if child.returncode != 0 or "1 passed" not in stdout:
            exit_code = child.returncode
            native_code = (exit_code or 0) & 0xFFFFFFFF
            pytest.fail(
                f"Relay child FIRST failure at attempt {attempt}/{_ATTEMPTS}; "
                f"pid={child.pid}; exit={exit_code} (0x{native_code:08X})\n"
                f"{environment}\ncommand={command!r}\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}",
                pytrace=False,
            )
