"""Parent boundary for the private one-page attestation worker.

Use the same absolute monotonic deadline as read_inventory. This is not an
admission path or a hard wall-clock bound on process creation/cleanup.
"""

from __future__ import annotations

import base64
import json
import math
import os
import sys
from pathlib import Path
from time import monotonic

from .artifact_inventory import MAX_BYTES, MAX_LINK_BYTES, AttestationPage
from .artifact_inventory_worker import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
from .errors import CrabError
from .fetch.bounded import run_bounded


def _hold(reason: str) -> CrabError:
    return CrabError(f"REMOTE_VERIFICATION_REQUIRED: {reason}")


def _worker_env() -> dict[str, str]:
    # Windows requires SystemRoot for some system libraries. Never inherit
    # arbitrary environment, proxy, CA, Python, token or TLS key-log settings.
    if sys.platform == "win32":
        root = os.environ.get("SYSTEMROOT")
        if not root or not Path(root).is_absolute():
            raise _hold("missing Windows system root")
        return {"SystemRoot": root}
    return {}


def _decode_page(raw: bytes, remaining: int) -> AttestationPage:
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise _hold("invalid worker response size")
    try:
        response = json.loads(raw.decode("ascii"))
        if not isinstance(response, list) or len(response) != 4:
            raise ValueError
        status, encoded, links, complete = response
        if (
            type(status) is not int
            or status != 200
            or complete is not True
            or not isinstance(encoded, str)
            or not isinstance(links, list)
            or any(not isinstance(link, str) for link in links)
            or len(encoded) > 4 * ((remaining + 2) // 3)
        ):
            raise ValueError
        body = base64.b64decode(encoded, validate=True)
        if (
            len(body) > remaining
            or base64.b64encode(body).decode("ascii") != encoded
            or sum(len(link.encode("utf-8")) for link in links) > MAX_LINK_BYTES
        ):
            raise ValueError
    except (ValueError, RecursionError):
        raise _hold("invalid worker response") from None
    return AttestationPage(200, body, tuple(links), True)


class IsolatedAttestationHTTPS:
    """Bound a trusted installed child; never execute a prey-local program."""

    def __init__(self, token: str, *, deadline: float) -> None:
        if not math.isfinite(deadline):
            raise _hold("invalid inventory deadline")
        self._token = token
        self._deadline = deadline

    def _remaining_time(self) -> float:
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            raise _hold("inventory deadline expired")
        return remaining

    def __call__(self, url: str, remaining: int) -> AttestationPage:
        if type(remaining) is not int or not 0 < remaining <= MAX_BYTES:
            raise _hold("invalid inventory byte budget")
        self._remaining_time()
        executable = Path(sys.executable)
        if not sys.executable or not executable.is_absolute():
            raise _hold("trusted Python executable unavailable")
        env = _worker_env()
        try:
            request = json.dumps(
                [self._token, url, remaining, min(30.0, self._remaining_time())],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            if len(request) > MAX_REQUEST_BYTES:
                raise _hold("worker request exceeds byte budget")
            result = run_bounded(
                [str(executable), "-I", "-m", "hungry_crab.artifact_inventory_worker"],
                timeout=self._remaining_time(),
                env=env,
                cwd=str(executable.parent),
                input_data=request,
                stdout_limit=MAX_RESPONSE_BYTES,
                stderr_limit=4096,
                process_group=True,
            )
        except CrabError:
            raise
        except Exception:
            # No exception text, subprocess output or request data escapes.
            raise _hold("isolated inventory worker failed") from None
        self._remaining_time()
        if result.returncode != 0 or result.stderr:
            raise _hold("isolated inventory worker refused")
        page = _decode_page(result.stdout, remaining)
        self._remaining_time()
        return page
