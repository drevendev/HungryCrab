"""Real HTTP parser controls at a socket seam, without TLS or external network."""

from __future__ import annotations

import http.client
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from hungry_crab.artifact_inventory_https import AttestationHTTPS
from hungry_crab.errors import CrabError

URL = "https://api.github.com/repos/owner/repo/attestations/sha256:" + "a" * 64 + "?per_page=30"
TOKEN = "synthetic_token"


def _serve(peer: socket.socket, response: bytes) -> bytes:
    request = bytearray()
    while b"\r\n\r\n" not in request:
        chunk = peer.recv(4096)
        assert chunk, "client closed before sending its request"
        request.extend(chunk)
        assert len(request) <= 8192
    peer.sendall(response)
    peer.shutdown(socket.SHUT_WR)
    return bytes(request)


@pytest.mark.parametrize(
    ("response", "accepted"),
    [
        (
            (
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                b"Link: first\r\nLink: second\r\nConnection: close\r\n\r\n{}"
            ),
            True,
        ),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: close\r\n\r\n{}", False),
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
            False,
        ),
        (
            (
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n"
                b"\r\n2\r\n{}\r\n0\r\n\r\n"
            ),
            False,
        ),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nmalformed header\r\n\r\n{}", False),
        (b"HTTP/1.1 302 Found\r\nLocation: https://elsewhere.invalid/\r\n\r\n", False),
        (b"HTTP/1.1 500 Error\r\nContent-Length: 0\r\n\r\n", False),
    ],
    ids=[
        "complete",
        "truncated",
        "duplicate-length",
        "chunked-length",
        "bad-header",
        "redirect",
        "non-200",
    ],
)
def test_wire_framing(monkeypatch: pytest.MonkeyPatch, response: bytes, accepted: bool) -> None:
    connections: list[http.client.HTTPSConnection] = []
    client, peer = socket.socketpair()
    with client, peer:
        client.settimeout(10)
        peer.settimeout(10)

        def connect(connection: http.client.HTTPSConnection) -> None:
            assert connection.host == "api.github.com"
            assert connection.port == 443
            assert not connections, "no retry or redirect is permitted"
            connections.append(connection)
            # Only the connect seam is replaced. Real http.client request
            # serialization, response parsing, framing and reads remain in use.
            connection.sock = client

        monkeypatch.setattr(http.client.HTTPSConnection, "connect", connect)
        with ThreadPoolExecutor(max_workers=1) as executor:
            server = executor.submit(_serve, peer, response)
            if accepted:
                page = AttestationHTTPS(TOKEN)(URL, 100)
                assert page.status == 200 and page.complete
                assert page.body == b"{}"
                assert page.links == ("first", "second")
            else:
                with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED") as error:
                    AttestationHTTPS(TOKEN)(URL, 100)
                assert TOKEN not in str(error.value)
            request = server.result(timeout=10)

        assert len(connections) == 1
        assert connections[0].sock is None

    target = URL.removeprefix("https://api.github.com").encode()
    assert request.startswith(b"GET " + target + b" HTTP/1.1\r\n")
    assert b"Authorization: Bearer synthetic_token\r\n" in request
    assert b"Accept-Encoding: identity\r\n" in request
