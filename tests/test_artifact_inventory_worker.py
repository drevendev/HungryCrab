"""Inert controls for the one-request process protocol; no live HTTP."""

from __future__ import annotations

import base64
import io
import json

import pytest

from hungry_crab import artifact_inventory_worker as worker
from hungry_crab.artifact_inventory import MAX_BYTES, MAX_LINK_BYTES, AttestationPage

TOKEN = "synthetic_token"
URL = "https://api.github.com/repos/owner/repo/attestations/sha256:" + "a" * 64 + "?per_page=30"


def invoke(raw: bytes) -> tuple[int, bytes]:
    sink = io.BytesIO()
    code = worker.serve_one(io.BytesIO(raw), sink)
    return code, sink.getvalue()


def encode(remaining: int = 100) -> bytes:
    return json.dumps([TOKEN, URL, remaining, 5.0]).encode("utf-8")


def test_one_page_preserves_bytes_and_link_order(monkeypatch: pytest.MonkeyPatch) -> None:
    class Transport:
        def __init__(self, token: str, *, timeout: float) -> None:
            assert token == TOKEN
            assert timeout == 5.0

        def __call__(self, url: str, remaining: int) -> AttestationPage:
            assert url == URL
            assert remaining == 100
            return AttestationPage(200, b"\x00\xff", ("first", "second"), True)

    monkeypatch.setattr(worker, "AttestationHTTPS", Transport)
    code, raw = invoke(encode())
    assert code == 0
    status, body, links, complete = json.loads(raw)
    assert status == 200
    assert base64.b64decode(body, validate=True) == b"\x00\xff"
    assert links == ["first", "second"]
    assert complete is True
    assert TOKEN.encode() not in raw


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not JSON",
        b"{}",
        b"[]",
        b"[]" * worker.MAX_REQUEST_BYTES,
        json.dumps([TOKEN, URL, True, 5]).encode(),
        json.dumps([TOKEN, URL, MAX_BYTES + 1, 5]).encode(),
        json.dumps([TOKEN, URL, 100, True]).encode(),
        json.dumps([TOKEN, URL, 100, "5"]).encode(),
        json.dumps([TOKEN, URL, 100, 5, "extra"]).encode(),
    ],
)
def test_invalid_request_never_calls_transport(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    calls = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(worker, "AttestationHTTPS", forbidden)
    assert invoke(raw) == (1, b"")
    assert not calls


@pytest.mark.parametrize(
    "page",
    [
        AttestationPage(500, b"{}", (), True),
        AttestationPage(200, b"{}", (), False),
        AttestationPage(200, b"x" * 101, (), True),
        AttestationPage(200, b"{}", ("x" * (MAX_LINK_BYTES + 1),), True),
    ],
)
def test_invalid_page_has_no_output(
    monkeypatch: pytest.MonkeyPatch, page: AttestationPage
) -> None:
    monkeypatch.setattr(worker, "AttestationHTTPS", lambda *a, **k: lambda *args: page)
    assert invoke(encode()) == (1, b"")


def test_exception_does_not_echo_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(worker, "AttestationHTTPS", fail)
    assert invoke(encode()) == (1, b"")


def test_partial_write_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class PartialSink(io.BytesIO):
        def write(self, data: bytes) -> int:
            return super().write(data[:1])

    page = AttestationPage(200, b"{}", (), True)
    monkeypatch.setattr(worker, "AttestationHTTPS", lambda *a, **k: lambda *args: page)
    sink = PartialSink()
    assert worker.serve_one(io.BytesIO(encode()), sink) == 1
    assert len(sink.getvalue()) == 1
