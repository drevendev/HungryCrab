"""Isolated authenticated A2 page transport; no admission or verifier authority.

Only explicit fixed-length identity-encoded responses are supported initially.
Chunked/trailer/close-delimited framing requires separate qualification.
No redirects, proxies, cache, alternate transport, or automatic retries.
"""

from __future__ import annotations

import http.client
import math
import re
from urllib.parse import urlsplit

from .artifact_inventory import MAX_BYTES, MAX_LINK_BYTES, AttestationPage
from .errors import CrabError

_HOST = "api.github.com"
_PATH = re.compile(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/attestations/sha256:[0-9a-f]{64}")
_LENGTH = re.compile(r"[0-9]{1,10}")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]+")


def _hold(reason: str) -> CrabError:
    return CrabError(f"REMOTE_VERIFICATION_REQUIRED: {reason}")


class AttestationHTTPS:
    """Callable page reader with explicit caller-provided authentication."""

    def __init__(self, token: str, *, timeout: float = 15.0) -> None:
        if not _TOKEN.fullmatch(token) or len(token) > 4096:
            raise _hold("missing or unsupported authentication")
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise _hold("invalid socket timeout")
        self._token = token
        self._timeout = timeout

    def __call__(self, url: str, remaining: int) -> AttestationPage:
        if (
            type(remaining) is not int
            or not 0 < remaining <= MAX_BYTES
            or not url.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
        ):
            raise _hold("invalid request or byte budget")
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise _hold("invalid request URL") from None
        if (
            parsed.scheme != "https"
            or parsed.netloc != _HOST
            or not _PATH.fullmatch(parsed.path)
            or any(part in {".", ".."} for part in parsed.path.split("/"))
            or not parsed.query
            or "#" in url
        ):
            raise _hold("invalid request scope")

        connection = http.client.HTTPSConnection(_HOST, timeout=self._timeout)
        try:
            connection.request(
                "GET",
                f"{parsed.path}?{parsed.query}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "Accept-Encoding": "identity",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "hungry-crab",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            try:
                if response.status != 200:
                    raise _hold("unsuccessful inventory response")
                headers = response.headers
                if (
                    headers.defects
                    or headers.get_all("Transfer-Encoding")
                    or headers.get_all("Trailer")
                ):
                    raise _hold("unsupported response framing")
                encodings = headers.get_all("Content-Encoding") or []
                if encodings not in ([], ["identity"]):
                    raise _hold("unsupported response encoding")
                lengths = headers.get_all("Content-Length") or []
                if len(lengths) != 1 or not _LENGTH.fullmatch(lengths[0]):
                    raise _hold("missing or ambiguous response length")
                length = int(lengths[0])
                if length > remaining:
                    raise _hold("inventory response exceeds byte budget")
                links = tuple(headers.get_all("Link") or [])
                if sum(len(link.encode("utf-8")) for link in links) > MAX_LINK_BYTES:
                    raise _hold("oversized Link fields")
                body = response.read(length)
                if len(body) != length:
                    raise _hold("incomplete inventory response")
                return AttestationPage(200, body, links, True)
            finally:
                # A closing response may own the socket independently of the connection.
                response.close()
        except (OSError, http.client.HTTPException, ValueError):
            # Do not surface remote exception strings or request credentials.
            raise _hold("inventory HTTPS transport failed") from None
        finally:
            connection.close()
