"""One-request stdin/stdout worker for the future isolated A2 supervisor.

This is a private process protocol, not a CLI, verifier, or admission path.
The parent must enforce a shared absolute deadline, kill/reap on timeout,
and discard all output on nonzero exit. Never put the token in argv.
"""

from __future__ import annotations

import base64
import json
import sys
from typing import BinaryIO

from .artifact_inventory import MAX_BYTES, MAX_LINK_BYTES
from .artifact_inventory_https import AttestationHTTPS

MAX_REQUEST_BYTES = 16 * 1024
# Base64 body plus worst-case JSON escaping of Link field code points and framing.
MAX_RESPONSE_BYTES = 4 * ((MAX_BYTES + 2) // 3) + 12 * MAX_LINK_BYTES + 1024


def serve_one(source: BinaryIO, sink: BinaryIO) -> int:
    """Handle one bounded request, returning only a sanitized exit status on refusal."""
    try:
        raw = source.read(MAX_REQUEST_BYTES + 1)
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            return 1
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, list) or len(request) != 4:
            return 1
        token, url, remaining, timeout = request
        if (
            not isinstance(token, str)
            or not isinstance(url, str)
            or type(remaining) is not int
            or not 0 < remaining <= MAX_BYTES
            or type(timeout) not in (int, float)
        ):
            return 1
        page = AttestationHTTPS(token, timeout=timeout)(url, remaining)
        if (
            page.status != 200
            or page.complete is not True
            or len(page.body) > remaining
            or sum(len(link.encode("utf-8")) for link in page.links) > MAX_LINK_BYTES
        ):
            return 1
        payload = json.dumps(
            [page.status, base64.b64encode(page.body).decode("ascii"), page.links, True],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(payload) > MAX_RESPONSE_BYTES:
            return 1
        if sink.write(payload) != len(payload):
            return 1
        sink.flush()
    except Exception:
        # This process boundary never serializes exception details, URLs or credentials.
        # A failed write may leave partial stdout; the parent must reject nonzero exit.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(serve_one(sys.stdin.buffer, sys.stdout.buffer))
