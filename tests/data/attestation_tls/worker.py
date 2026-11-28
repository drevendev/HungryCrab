"""Test-only launch seam for the installed attestation page worker."""

from __future__ import annotations

import http.client
import io
import json
import socket
import ssl
import sys
from pathlib import Path
from time import monotonic
from typing import Any

from hungry_crab.artifact_inventory_worker import serve_one


def main() -> int:
    port_arg, ca_file, trace_file, socket_timeout = sys.argv[1:]
    port = int(port_arg)
    trace = Path(trace_file)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if ca_file:
        context.load_verify_locations(ca_file)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

    def record(event: str) -> None:
        with trace.open("a", encoding="ascii") as stream:
            stream.write(f"{event} {monotonic()}\n")

    original_resolve = socket.getaddrinfo
    original_wrap = ssl.SSLContext.wrap_socket
    original_begin = http.client.HTTPResponse.begin
    original_body_read = http.client.HTTPResponse.read
    original_tls_read = ssl.SSLSocket.read
    reading_body = False

    def resolve(host: str, service: int, *args: Any, **kwargs: Any) -> Any:
        if host != "api.github.com" or service != 443:
            raise OSError("unexpected fixture destination")
        return original_resolve("127.0.0.1", port, *args, **kwargs)

    def wrap(client_context: ssl.SSLContext, *args: Any, **kwargs: Any) -> ssl.SSLSocket:
        assert client_context is context and kwargs["server_hostname"] == "api.github.com"
        try:
            return original_wrap(client_context, *args, **kwargs)
        except ssl.SSLCertVerificationError as error:
            record(f"certificate-rejected-{error.verify_code}")
            raise

    def begin(response: http.client.HTTPResponse) -> None:
        record("headers-entered")
        original_begin(response)

    def read_body(response: http.client.HTTPResponse, amount: int | None = None) -> bytes:
        nonlocal reading_body
        reading_body = True
        record("body-entered")
        try:
            return original_body_read(response, amount)
        finally:
            reading_body = False

    def read_tls(peer: ssl.SSLSocket, length: int = 1024, buffer: Any = None) -> bytes | int:
        data = original_tls_read(peer, length, buffer)
        if reading_body and data:
            record("body-read")
        return data

    socket.getaddrinfo = resolve
    ssl._create_default_https_context = lambda: context
    ssl.SSLContext.wrap_socket = wrap
    http.client.HTTPResponse.begin = begin
    http.client.HTTPResponse.read = read_body
    ssl.SSLSocket.read = read_tls
    request = json.loads(sys.stdin.buffer.read())
    if socket_timeout:
        request[3] = float(socket_timeout)
    return serve_one(io.BytesIO(json.dumps(request).encode()), sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
