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

import pytest

_TARGET = "tests/test_acquisition.py::test_main_and_wiki_connections_share_one_real_stream_limit"
_ATTEMPTS = 8
_TIMEOUT_SECONDS = 45


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
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate()
            pytest.fail(
                f"Relay child timed out on FIRST failing attempt {attempt}/{_ATTEMPTS}; "
                f"pid={child.pid}; timeout={_TIMEOUT_SECONDS}s\n{environment}\n"
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
