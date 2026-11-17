"""Owned regressions for pre-connect scope admission, PR #687 review R3."""

from __future__ import annotations

import socket
import socketserver
import threading
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_scope_exit_must_prevent_a_new_upstream_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reuse: bool, cancelled: bool
) -> None:
    from hungry_crab.fetch.acquisition import AcquisitionBudget

    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    quota = AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 4096)
    ready = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_recv = socket.socket.recv
    real_connect = socket.create_connection
    header = bytearray()
    owner = threading.current_thread()
    effects: list[tuple[str, int]] = []

    with ExitStack() as cleanup:
        upstream, peer = socket.socketpair()
        cleanup.enter_context(upstream)
        cleanup.enter_context(peer)
        peer.settimeout(3)

        def recv(stream: socket.socket, count: int, *args: Any) -> bytes:
            data = real_recv(stream, count, *args)
            if threading.current_thread() is not owner and count == 1:
                header.extend(data)
                if header.endswith(b"\r\n\r\n"):
                    ready.set()
                    assert release.wait(10)
            return data

        def connect(address: tuple[str, int], *, timeout: float) -> socket.socket:
            effects.append(address)
            return upstream

        monkeypatch.setattr(socket.socket, "recv", recv)
        monkeypatch.setattr(socketserver.BaseRequestHandler, "finish", lambda _: finished.set())
        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        try:
            expected = (
                pytest.raises(RuntimeError, match="owned cancellation")
                if cancelled else nullcontext()
            )
            with expected, quota.scope():
                assert quota._server is not None
                client = cleanup.enter_context(real_connect(quota._server.server_address, 3))
                client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
                assert ready.wait(3)
                if cancelled:
                    raise RuntimeError("owned cancellation")
            # Final header byte was already received by the handler. It resumes
            # only after the stopped scope has returned to its caller.
            with quota.scope() if reuse else nullcontext():
                release.set()
                assert finished.wait(3)
                assert effects == [], effects
        finally:
            release.set()


def test_control_live_scope_connects_and_disposes_owned_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hungry_crab.fetch.acquisition import AcquisitionBudget

    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    quota = AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 4096)
    real_connect = socket.create_connection
    calls: list[tuple[str, int]] = []
    with ExitStack() as cleanup:
        upstream, peer = socket.socketpair()
        cleanup.enter_context(upstream)
        cleanup.enter_context(peer)
        peer.settimeout(3)

        def connect(address: tuple[str, int], *, timeout: float) -> socket.socket:
            calls.append(address)
            return upstream

        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        with quota.scope():
            assert quota._server is not None
            client = cleanup.enter_context(real_connect(quota._server.server_address, 3))
            client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
            assert client.recv(4096).startswith(b"HTTP/1.1 200")
        assert peer.recv(1) == b""
    assert calls == [("github.com", 443)]
