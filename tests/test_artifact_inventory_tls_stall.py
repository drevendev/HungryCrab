"""Real loopback TLS handshake cancellation; no external network or credentials."""

from __future__ import annotations

import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from typing import Any

import pytest

from hungry_crab import artifact_inventory_supervisor as supervisor
from hungry_crab.errors import CrabError
from hungry_crab.fetch.bounded import run_bounded

URL = "https://api.github.com/repos/owner/repo/attestations/sha256:" + "a" * 64 + "?per_page=30"
TOKEN = "synthetic_token"

# Only the test child changes resolution and extends its socket timeout beyond
# the parent deadline. The installed HTTPS implementation and TLS remain real.
CHILD = """
import io
import json
import socket
import sys

from hungry_crab.artifact_inventory_worker import serve_one

port = int(sys.argv[1])
original = socket.getaddrinfo

def resolve(host, service, *args, **kwargs):
    if host != "api.github.com" or service != 443:
        raise OSError("unexpected fixture destination")
    return original("127.0.0.1", port, *args, **kwargs)

socket.getaddrinfo = resolve
request = json.loads(sys.stdin.buffer.read())
request[3] = 30
raise SystemExit(serve_one(io.BytesIO(json.dumps(request).encode()), sys.stdout.buffer))
"""


def _observe_stalled_handshake(listener: socket.socket) -> bytes:
    # Receive the actual ClientHello but never send any TLS handshake response.
    # Peer EOF/reset is evidence of socket cleanup, not just a typed HOLD.
    received = bytearray()
    peer, _ = listener.accept()
    with peer:
        peer.settimeout(20)
        while True:
            try:
                chunk = peer.recv(4096)
            except ConnectionResetError:
                break
            if not chunk:
                break
            received.extend(chunk)
            assert len(received) <= 65536
    return bytes(received)


def test_parent_reaps_worker_during_real_tls_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    original_popen = subprocess.Popen

    def capture_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc = original_popen(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(20)
        port = listener.getsockname()[1]

        def launch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            assert command[1:] == ["-I", "-m", "hungry_crab.artifact_inventory_worker"]
            assert kwargs["process_group"] is True
            return run_bounded([sys.executable, "-I", "-c", CHILD, str(port)], **kwargs)

        monkeypatch.setattr(supervisor, "run_bounded", launch)
        with ThreadPoolExecutor(max_workers=1) as executor:
            observation = executor.submit(_observe_stalled_handshake, listener)
            with pytest.raises(CrabError, match=r"worker failed|deadline expired") as error:
                supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=monotonic() + 8)(URL, 100)
            received = observation.result(timeout=20)

    # TLS handshake record (22), legacy major protocol version (3). An empty
    # observation would not prove the worker reached the actual network phase.
    assert received[:2] == bytes([22, 3])
    assert TOKEN.encode() not in received
    assert TOKEN not in str(error.value)
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].returncode != 0
