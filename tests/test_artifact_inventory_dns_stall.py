"""Supervisor cancellation at the resolver boundary; no external DNS or credentials."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

from hungry_crab import artifact_inventory_supervisor as supervisor
from hungry_crab.errors import CrabError
from hungry_crab.fetch.bounded import run_bounded

URL = "https://api.github.com/repos/owner/repo/attestations/sha256:" + "a" * 64 + "?per_page=30"
TOKEN = "synthetic_token"

# Stub only name resolution in the isolated child. The real worker and HTTPS
# request path must reach the stub; no DNS packet or connection is attempted.
CHILD = """
import io
import json
import pathlib
import socket
import sys
import threading

from hungry_crab.artifact_inventory_worker import serve_one

marker = pathlib.Path(sys.argv[1])

def resolve(host, service, *args, **kwargs):
    if host != "api.github.com" or service != 443:
        raise OSError("unexpected fixture destination")
    marker.touch()
    threading.Event().wait(60)
    raise OSError("fixture resolver wait expired")

socket.getaddrinfo = resolve
request = json.loads(sys.stdin.buffer.read())
request[3] = 30
raise SystemExit(serve_one(io.BytesIO(json.dumps(request).encode()), sys.stdout.buffer))
"""


def test_parent_reaps_worker_during_blocked_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started = tmp_path / "resolver-entered"
    processes: list[subprocess.Popen[bytes]] = []
    timeouts: list[subprocess.TimeoutExpired] = []
    original_popen = subprocess.Popen

    def capture_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc = original_popen(*args, **kwargs)
        processes.append(proc)
        return proc

    def launch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert command[1:] == ["-I", "-m", "hungry_crab.artifact_inventory_worker"]
        assert kwargs["process_group"] is True
        try:
            return run_bounded([sys.executable, "-I", "-c", CHILD, str(started)], **kwargs)
        except subprocess.TimeoutExpired as exc:
            timeouts.append(exc)
            raise

    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    monkeypatch.setattr(supervisor, "run_bounded", launch)
    before = monotonic()
    with pytest.raises(CrabError, match=r"worker failed|deadline expired") as error:
        supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=before + 8)(URL, 100)

    assert started.is_file(), "the installed HTTPS path must enter the resolver stub"
    assert len(timeouts) == 1, "the parent deadline must interrupt the blocked resolver"
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert TOKEN not in str(error.value)
    assert TOKEN.encode() not in (timeouts[0].output or b"")
    assert TOKEN.encode() not in (timeouts[0].stderr or b"")
    assert monotonic() - before < 25  # cleanup grace, not a hard deadline claim
