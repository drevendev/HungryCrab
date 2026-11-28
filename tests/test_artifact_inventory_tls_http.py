"""Exercise the installed inventory worker against a TLS loopback server."""

from __future__ import annotations

import select
import socket
import ssl
import subprocess
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlencode

import pytest

from hungry_crab import artifact_inventory_supervisor as supervisor
from hungry_crab.artifact_inventory import AttestationPage, InventoryScope, read_inventory
from hungry_crab.errors import CrabError
from hungry_crab.fetch.bounded import run_bounded

FIXTURE = Path(__file__).resolve().parent / "data" / "attestation_tls"
TOKEN = "synthetic_token"
SCOPE = InventoryScope("owner/repo", 123, "a" * 64, "https://example.invalid/predicate")
HOST = "https://api.github.com"
QUERY = urlencode({"per_page": 30, "predicate_type": SCOPE.predicate_type})
URL = f"{HOST}/repos/{SCOPE.repository}/attestations/sha256:{SCOPE.sha256}?{QUERY}"
FIRST = f'<{URL}>; rel="first"'
NEXT = f'<{URL}&after=next%2Bpage>; rel="next"'
EMPTY = b'{"attestations":[]}'
ENTRY = b'{"attestations":[{"repository_id":123,"bundle":{"test":"first"}}]}'
FIXTURE_TIMEOUT = 20
TRICKLE_IDLE_TIMEOUT = 2.5


@dataclass
class WorkerProbe:
    processes: list[subprocess.Popen[bytes]] = field(default_factory=list)
    timeouts: list[subprocess.TimeoutExpired] = field(default_factory=list)

    def assert_reaped(self, count: int) -> None:
        assert len(self.processes) == count
        assert all(process.poll() is not None for process in self.processes)


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(FIXTURE_TIMEOUT)
        yield server


def _tls_context(certificate: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(FIXTURE / certificate, FIXTURE / "loopback-server-test-key.pem")
    return context


def _install_worker(
    monkeypatch: pytest.MonkeyPatch,
    listener: socket.socket,
    trace: Path,
    *,
    trusted: bool = True,
    socket_timeout: float | None = None,
) -> WorkerProbe:
    probe = WorkerProbe()
    original_popen = subprocess.Popen

    def capture_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original_popen(*args, **kwargs)
        probe.processes.append(process)
        return process

    def launch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert command[1:] == ["-I", "-m", "hungry_crab.artifact_inventory_worker"]
        assert kwargs["process_group"] is True
        harness = [
            command[0],
            "-I",
            str(FIXTURE / "worker.py"),
            str(listener.getsockname()[1]),
            str(FIXTURE / "test-ca.pem") if trusted else "",
            str(trace),
            str(socket_timeout) if socket_timeout is not None else "",
        ]
        try:
            return run_bounded(harness, **kwargs)
        except subprocess.TimeoutExpired as error:
            probe.timeouts.append(error)
            raise

    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    monkeypatch.setattr(supervisor, "run_bounded", launch)
    return probe


def _respond(body: bytes, *links: str) -> Callable[[ssl.SSLSocket], None]:
    def send(peer: ssl.SSLSocket) -> None:
        headers = ["HTTP/1.1 200 OK", f"Content-Length: {len(body)}", "Connection: close"]
        headers.extend(f"Link: {link}" for link in links)
        peer.sendall("\r\n".join(headers).encode("ascii") + b"\r\n\r\n" + body)

    return send


def _serve(
    listener: socket.socket,
    context: ssl.SSLContext,
    respond: Callable[[ssl.SSLSocket], None],
) -> bytes | None:
    connection, _ = listener.accept()
    with connection:
        connection.settimeout(FIXTURE_TIMEOUT)
        try:
            peer = context.wrap_socket(connection, server_side=True)
        except ssl.SSLError:
            # No request can have been read on a rejected handshake.
            return None
        with peer:
            request = bytearray()
            while b"\r\n\r\n" not in request:
                chunk = peer.recv(4096)
                assert chunk, "client closed before sending its request"
                request.extend(chunk)
                assert len(request) <= 8192
            respond(peer)
            with suppress(ConnectionResetError, ConnectionAbortedError):
                assert peer.recv(1) == b"", "client sent more bytes instead of closing"
            return bytes(request)


def _requests(
    listener: socket.socket, *responses: Callable[[ssl.SSLSocket], None]
) -> tuple[bytes | None, ...]:
    context = _tls_context("loopback-server.pem")
    return tuple(_serve(listener, context, respond) for respond in responses)


def _assert_request(request: bytes | None, url: str) -> None:
    assert request is not None
    target = url.removeprefix(HOST).encode("ascii")
    assert request.startswith(b"GET " + target + b" HTTP/1.1\r\n")
    assert b"Authorization: Bearer " + TOKEN.encode("ascii") + b"\r\n" in request


def _trace(path: Path) -> list[tuple[str, float]]:
    return [
        (event, float(at))
        for event, at in (line.split() for line in path.read_text(encoding="ascii").splitlines())
    ]


@pytest.mark.parametrize(
    ("certificate", "trusted", "verify_code"),
    [
        ("loopback-server.pem", True, None),
        ("loopback-server.pem", False, 20),
        ("wrong-host.pem", True, 62),
    ],
    ids=["trusted-host", "untrusted-ca", "wrong-host"],
)
def test_worker_verifies_tls_before_sending_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    listener: socket.socket,
    certificate: str,
    trusted: bool,
    verify_code: int | None,
) -> None:
    trace = tmp_path / "tls-trace"
    probe = _install_worker(monkeypatch, listener, trace, trusted=trusted)
    client = supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=monotonic() + 15)
    context = _tls_context(certificate)
    with ThreadPoolExecutor(max_workers=1) as executor:
        observed = executor.submit(_serve, listener, context, _respond(EMPTY, FIRST, NEXT))
        if verify_code is None:
            assert client(URL, 1024) == AttestationPage(200, EMPTY, (FIRST, NEXT), True)
            _assert_request(observed.result(timeout=FIXTURE_TIMEOUT), URL)
        else:
            with pytest.raises(
                CrabError, match="REMOTE_VERIFICATION_REQUIRED: .*worker refused"
            ) as error:
                client(URL, 1024)
            assert TOKEN not in str(error.value)
            assert observed.result(timeout=FIXTURE_TIMEOUT) is None
            assert [event for event, _ in _trace(trace)] == [f"certificate-rejected-{verify_code}"]
    probe.assert_reaped(1)
    assert not probe.timeouts
    assert not select.select([listener], [], [], 0)[0]


def test_inventory_rebuilds_next_link_over_tls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, listener: socket.socket
) -> None:
    probe = _install_worker(monkeypatch, listener, tmp_path / "tls-trace")
    with ThreadPoolExecutor(max_workers=1) as executor:
        observed = executor.submit(_requests, listener, _respond(ENTRY, FIRST, NEXT), _respond(EMPTY))
        deadline = monotonic() + 15
        client = supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=deadline)
        assert read_inventory(SCOPE, client, deadline=deadline) == (b'{"test":"first"}',)
        initial, next_page = observed.result(timeout=FIXTURE_TIMEOUT)
    _assert_request(initial, URL)
    _assert_request(next_page, URL + "&after=next%2Bpage")
    probe.assert_reaped(2)
    assert not probe.timeouts


@pytest.mark.parametrize(
    ("link", "reason"),
    [
        ("missing-angle-brackets; rel=next", "malformed Link field"),
        (NEXT, "duplicate pagination relation"),
        (NEXT.replace("api.github.com", "other.invalid"), "pagination scope changed"),
    ],
    ids=["malformed", "duplicate", "wrong-host"],
)
def test_inventory_refuses_semantic_link_errors_after_complete_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    listener: socket.socket,
    link: str,
    reason: str,
) -> None:
    probe = _install_worker(monkeypatch, listener, tmp_path / "tls-trace")
    with ThreadPoolExecutor(max_workers=1) as executor:
        observed = executor.submit(_requests, listener, _respond(ENTRY, NEXT, link))
        deadline = monotonic() + 15
        client = supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=deadline)
        with pytest.raises(CrabError, match=f"REMOTE_VERIFICATION_REQUIRED: {reason}") as error:
            read_inventory(SCOPE, client, deadline=deadline)
        (request,) = observed.result(timeout=FIXTURE_TIMEOUT)
    _assert_request(request, URL)
    assert TOKEN not in str(error.value)
    probe.assert_reaped(1)
    assert not probe.timeouts
    assert not select.select([listener], [], [], 0)[0]


def _stall_headers(peer: ssl.SSLSocket) -> None:
    peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Len")


def _stall_body(peer: ssl.SSLSocket) -> None:
    peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\nConnection: close\r\n\r\n{")


def _trickle_body(peer: ssl.SSLSocket) -> None:
    peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\nConnection: close\r\n\r\n")
    for _ in range(60):
        if select.select([peer], [], [], 0.2)[0]:
            return
        try:
            peer.sendall(b"{")
        except (ConnectionError, ssl.SSLEOFError):
            return
    pytest.fail("client left the trickling response open")


@pytest.mark.parametrize(
    ("respond", "phase", "idle_timeout"),
    [
        (_stall_headers, "headers-entered", 30),
        (_stall_body, "body-entered", 30),
        (_trickle_body, "body-entered", TRICKLE_IDLE_TIMEOUT),
    ],
    ids=["headers", "body", "trickle"],
)
def test_inventory_deadline_cancels_http_and_reaps_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    listener: socket.socket,
    respond: Callable[[ssl.SSLSocket], None],
    phase: str,
    idle_timeout: float,
) -> None:
    trace = tmp_path / "tls-trace"
    probe = _install_worker(monkeypatch, listener, trace, socket_timeout=idle_timeout)
    with ThreadPoolExecutor(max_workers=1) as executor:
        observed = executor.submit(_requests, listener, respond)
        deadline = monotonic() + 8
        client = supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=deadline)
        with pytest.raises(
            CrabError, match=r"REMOTE_VERIFICATION_REQUIRED: .*worker failed"
        ) as error:
            read_inventory(SCOPE, client, deadline=deadline)
        (request,) = observed.result(timeout=FIXTURE_TIMEOUT)
    _assert_request(request, URL)
    observations = _trace(trace)
    assert phase in [event for event, _ in observations]
    probe.assert_reaped(1)
    (timeout,) = probe.timeouts
    assert not timeout.output
    assert not timeout.stderr
    assert TOKEN not in str(error.value)
    assert not select.select([listener], [], [], 0)[0]
    if respond is _trickle_body:
        reads = [at for event, at in observations if event == "body-read"]
        assert len(reads) > 1, "worker must receive successive body fragments"
        assert reads[-1] - reads[0] >= idle_timeout
        assert all(later - earlier < idle_timeout for earlier, later in pairwise(reads))
