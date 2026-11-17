from __future__ import annotations

import socket
import socketserver
import threading
from contextlib import ExitStack, nullcontext, suppress
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.fetch.acquisition import AcquisitionBudget, _RelayScope


@pytest.fixture
def quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AcquisitionBudget:
    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    return AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 4096)


def _closed(client: socket.socket) -> None:
    with suppress(ConnectionResetError):
        assert client.recv(1) == b""


@pytest.mark.parametrize("cancelled", [False, True])
def test_partial_connect_is_disconnected_at_scope_exit(
    quota: AcquisitionBudget, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    accepted = threading.Event()
    process_request = socketserver.ThreadingTCPServer.process_request

    def observe(server: Any, request: Any, address: Any) -> None:
        accepted.set()
        process_request(server, request, address)

    monkeypatch.setattr(socketserver.ThreadingTCPServer, "process_request", observe)
    with ExitStack() as cleanup:
        expected = (
            pytest.raises(RuntimeError, match="owned cancellation") if cancelled else nullcontext()
        )
        with expected, quota.scope():
            assert quota._server is not None
            client = cleanup.enter_context(
                socket.create_connection(quota._server.server_address, 3)
            )
            client.sendall(b"CONNECT github.com")
            assert accepted.wait(3)
            if cancelled:
                raise RuntimeError("owned cancellation")
        _closed(client)
        assert quota.downloaded == 0


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_late_upstream_cannot_acknowledge_or_join_a_later_scope(
    quota: AcquisitionBudget, monkeypatch: pytest.MonkeyPatch, reuse: bool, cancelled: bool
) -> None:
    started = threading.Event()
    release = threading.Event()
    actual_connect = socket.create_connection
    with ExitStack() as cleanup:
        upstream, peer = socket.socketpair()
        cleanup.enter_context(upstream)
        cleanup.enter_context(peer)
        peer.settimeout(3)

        def connect(address: tuple[str, int], *, timeout: float) -> socket.socket:
            assert address == ("github.com", 443)
            started.set()
            assert release.wait(10)
            return upstream

        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        try:
            expected = (
                pytest.raises(RuntimeError, match="owned cancellation")
                if cancelled
                else nullcontext()
            )
            with expected, quota.scope():
                assert quota._server is not None
                client = cleanup.enter_context(actual_connect(quota._server.server_address, 3))
                client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
                assert started.wait(3)
                if cancelled:
                    raise RuntimeError("owned cancellation")
            _closed(client)
            # The old handler retains its old scope even after this budget is reused.
            with quota.scope() if reuse else nullcontext():
                release.set()
                _closed(peer)
                assert quota.downloaded == 0
        finally:
            release.set()


def test_active_tunnel_closes_without_waiting_for_a_blocked_byte_reader(
    quota: AcquisitionBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    reading = threading.Event()
    finished = threading.Event()
    timed_out = threading.Event()
    actual_connect = socket.create_connection
    with ExitStack() as cleanup:
        upstream, peer = socket.socketpair()
        cleanup.enter_context(upstream)
        cleanup.enter_context(peer)
        upstream.settimeout(3)
        peer.settimeout(3)

        def connect(address: tuple[str, int], *, timeout: float) -> socket.socket:
            assert address == ("github.com", 443)
            return upstream

        def read(stream: socket.socket) -> bytes:
            assert stream.recv(1) == b"x"
            # Own the same accounting lock as an actual blocked read. Teardown
            # must interrupt recv without first acquiring this lock itself.
            with quota._lock:
                reading.set()
                try:
                    return stream.recv(1)
                except TimeoutError:
                    timed_out.set()
                    raise
                finally:
                    finished.set()

        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        monkeypatch.setattr(quota, "read_upstream", read)
        with quota.scope():
            assert quota._server is not None
            client = cleanup.enter_context(actual_connect(quota._server.server_address, 3))
            client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
            response = b""
            while not response.endswith(b"\r\n\r\n"):
                part = client.recv(1)
                assert part
                response += part
            # The owned read adapter consumes select's byte, then blocks on recv.
            peer.sendall(b"x")
            assert reading.wait(3)
        _closed(client)
        assert finished.wait(3)
        assert not timed_out.is_set()
        assert quota.downloaded == 0


def test_scope_stop_between_registration_and_connect_ack_is_refused() -> None:
    scope = _RelayScope()
    started = threading.Event()
    resume = threading.Event()
    accepted: list[bool] = []
    with ExitStack() as cleanup:
        client, peer = socket.socketpair()
        upstream, remote = socket.socketpair()
        for stream in (client, peer, upstream, remote):
            cleanup.enter_context(stream)
        peer.settimeout(3)
        assert scope.register(client)
        assert scope.register(upstream)

        def delayed_ack() -> None:
            started.set()
            assert resume.wait(3)
            accepted.append(scope.acknowledge_connect(client, upstream))

        worker = threading.Thread(target=delayed_ack)
        worker.start()
        try:
            assert started.wait(3)
            scope.stop()
            resume.set()
            worker.join(timeout=3)
            assert not worker.is_alive()
            assert accepted == [False]
            assert peer.recv(1) == b""
        finally:
            resume.set()
            worker.join(timeout=3)
            scope.stop()


def test_live_connect_ack_preserves_client_timeout() -> None:
    scope = _RelayScope()
    with ExitStack() as cleanup:
        client, peer = socket.socketpair()
        upstream, remote = socket.socketpair()
        for stream in (client, peer, upstream, remote):
            cleanup.enter_context(stream)
        client.settimeout(3)
        peer.settimeout(3)
        try:
            assert scope.register(client)
            assert scope.register(upstream)
            assert scope.acknowledge_connect(client, upstream)
            assert peer.recv(128) == b"HTTP/1.1 200 Connection established\r\n\r\n"
            assert client.gettimeout() == 3
        finally:
            scope.stop()


@pytest.mark.parametrize("exit_mode", ["normal", "cancel", "assertion"])
def test_relay_handler_exits_after_delayed_connect_is_released(
    quota: AcquisitionBudget, monkeypatch: pytest.MonkeyPatch, exit_mode: str
) -> None:
    """Measure the handler lifetime across scope teardown without a live network."""
    entered = threading.Event()
    connecting = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    workers: list[threading.Thread] = []
    original_connect = socket.create_connection
    original_process = socketserver.ThreadingTCPServer.process_request_thread

    def observe_handler(
        server: socketserver.ThreadingTCPServer,
        request: socket.socket,
        address: tuple[str, int],
    ) -> None:
        workers.append(threading.current_thread())
        entered.set()
        try:
            original_process(server, request, address)
        finally:
            completed.set()

    monkeypatch.setattr(
        socketserver.ThreadingTCPServer, "process_request_thread", observe_handler
    )
    with ExitStack() as cleanup:
        upstream, peer = socket.socketpair()
        cleanup.enter_context(upstream)
        cleanup.enter_context(peer)
        peer.settimeout(3)

        def delayed_connect(
            address: tuple[str, int], *, timeout: float
        ) -> socket.socket:
            assert address == ("github.com", 443)
            connecting.set()
            assert release.wait(10), "test did not release the owned connector"
            return upstream

        monkeypatch.setattr(
            "hungry_crab.fetch.acquisition.socket.create_connection", delayed_connect
        )
        if exit_mode == "cancel":
            expected = pytest.raises(RuntimeError, match="owned cancellation")
        elif exit_mode == "assertion":
            expected = pytest.raises(AssertionError, match="owned assertion failure")
        else:
            expected = nullcontext()
        try:
            with expected, quota.scope():
                assert quota._server is not None
                client = cleanup.enter_context(
                    original_connect(quota._server.server_address, 3)
                )
                client.settimeout(3)
                client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
                assert entered.wait(3)
                assert connecting.wait(3)
                if exit_mode == "cancel":
                    raise RuntimeError("owned cancellation")
                if exit_mode == "assertion":
                    raise AssertionError("owned assertion failure")

            # A blocked upstream connect may legitimately outlive scope return.
            # The captured handler must still be observable, not forgotten.
            assert len(workers) == 1
            assert workers[0].is_alive()
            assert not completed.is_set()
            _closed(client)
            release.set()
            assert completed.wait(3)
            workers[0].join(timeout=3)
            assert not workers[0].is_alive()
            assert peer.recv(1) == b""
            assert quota.downloaded == 0
        finally:
            release.set()
            for worker in workers:
                worker.join(timeout=3)
