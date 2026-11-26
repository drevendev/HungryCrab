"""Inert HTTP response controls; no real network or credentials."""

from __future__ import annotations

import http.client
from email.message import Message

import pytest

from hungry_crab.artifact_inventory_https import AttestationHTTPS
from hungry_crab.errors import CrabError

URL = "https://api.github.com/repos/owner/repo/attestations/sha256:" + "a" * 64 + "?per_page=30"
TOKEN = "synthetic_token"


class Response:
    def __init__(
        self, fields: list[tuple[str, str]], body: bytes = b"{}", status: int = 200
    ) -> None:
        self.headers = Message()
        for key, value in fields:
            self.headers[key] = value
        self.body = body
        self.status = status
        self.reads: list[int] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def read(self, amount: int) -> bytes:
        self.reads.append(amount)
        return self.body[:amount]


class Connection:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        self.requests.append((method, target, headers))

    def getresponse(self) -> Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def install(monkeypatch: pytest.MonkeyPatch, response: Response) -> Connection:
    connection = Connection(response)

    def connect(host: str, *, timeout: float) -> Connection:
        assert host == "api.github.com"
        assert timeout == 15.0
        return connection

    monkeypatch.setattr(http.client, "HTTPSConnection", connect)
    return connection


def test_complete_response_preserves_all_link_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response([("Content-Length", "2"), ("Link", "first"), ("Link", "second")])
    connection = install(monkeypatch, response)
    page = AttestationHTTPS(TOKEN)(URL, 2)
    assert page.complete and page.status == 200
    assert page.body == b"{}"
    assert page.links == ("first", "second")
    assert response.reads == [2]
    assert response.closed
    assert connection.closed
    [(method, target, headers)] = connection.requests
    assert method == "GET"
    assert target == URL.removeprefix("https://api.github.com")
    assert headers["Authorization"] == "Bearer synthetic_token"
    assert headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 404, 429, 500])
def test_no_redirect_or_retry(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    response = Response([("Location", "https://elsewhere.invalid")], status=status)
    connection = install(monkeypatch, response)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        AttestationHTTPS(TOKEN)(URL, 100)
    assert len(connection.requests) == 1
    assert response.reads == []
    assert response.closed
    assert connection.closed


@pytest.mark.parametrize(
    "fields",
    [
        [],
        [("Content-Length", "3"), ("Content-Length", "3")],
        [("Content-Length", "2, 2")],
        [("Content-Length", "-1")],
        [("Content-Length", "101")],
        [("Content-Length", "2"), ("Transfer-Encoding", "chunked")],
        [("Content-Length", "2"), ("Trailer", "Link")],
        [("Content-Length", "2"), ("Content-Encoding", "gzip")],
        [("Content-Length", "2"), ("Link", "x" * 16385)],
    ],
)
def test_bad_framing_refused_before_read(
    monkeypatch: pytest.MonkeyPatch, fields: list[tuple[str, str]]
) -> None:
    response = Response(fields)
    connection = install(monkeypatch, response)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        AttestationHTTPS(TOKEN)(URL, 100)
    assert response.reads == []
    assert response.closed
    assert connection.closed


def test_short_body_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response([("Content-Length", "3")])
    connection = install(monkeypatch, response)
    with pytest.raises(CrabError, match="incomplete inventory response"):
        AttestationHTTPS(TOKEN)(URL, 100)
    assert response.reads == [3]
    assert response.closed
    assert connection.closed


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("api.github.com", "elsewhere.invalid"),
        URL.replace("https:", "http:"),
        URL.replace("api.github.com", "api.github.com:443"),
        URL.replace("api.github.com", "user@api.github.com"),
        URL.replace("/owner/", "/../"),
        URL + "#",
        URL + "\n",
    ],
)
def test_bad_url_never_connects(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    connection = install(monkeypatch, Response([]))
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        AttestationHTTPS(TOKEN)(url, 100)
    assert connection.requests == []


@pytest.mark.parametrize("token", ["", "line\nbreak", "has space"])
def test_missing_or_bad_token(token: str) -> None:
    with pytest.raises(CrabError, match="authentication"):
        AttestationHTTPS(token)


def test_network_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = install(monkeypatch, Response([]))

    def fail() -> Response:
        raise TimeoutError(TOKEN)

    monkeypatch.setattr(connection, "getresponse", fail)
    with pytest.raises(CrabError, match="inventory HTTPS transport failed") as error:
        AttestationHTTPS(TOKEN)(URL, 100)
    assert TOKEN not in str(error.value)
    assert connection.closed


def test_interrupted_detached_response_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response([("Content-Length", "2")])
    connection = install(monkeypatch, response)

    def fail(amount: int) -> bytes:
        assert amount == 2
        raise TimeoutError(TOKEN)

    monkeypatch.setattr(response, "read", fail)
    with pytest.raises(CrabError, match="inventory HTTPS transport failed") as error:
        AttestationHTTPS(TOKEN)(URL, 100)
    assert TOKEN not in str(error.value)
    assert response.closed
    assert connection.closed
