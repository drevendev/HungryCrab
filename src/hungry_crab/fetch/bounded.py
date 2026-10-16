"""Bound the bytes captured from trusted API transports, before decoding JSON."""

from __future__ import annotations

import math
import subprocess
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
from queue import Empty, Queue
from typing import IO

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
_CHUNK_BYTES = 64 * 1024


class ResponseTooLargeError(RuntimeError):
    """A transport exceeded its raw-byte budget; no partial response is usable."""


def validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("API timeout must be finite and positive")


def read_limited(stream: IO[bytes], limit: int, *, label: str) -> bytes:
    """Read through EOF, allowing at most one sentinel byte beyond the limit.

    Do not trust Content-Length or assume a short read means EOF. The caller owns
    the stream and its timeout; this function only enforces the byte budget.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("response byte limit must be a positive integer")
    body = bytearray()
    while True:
        chunk = stream.read(min(_CHUNK_BYTES, limit - len(body) + 1))
        if not chunk:
            return bytes(body)
        if len(chunk) > limit - len(body):
            raise ResponseTooLargeError(f"{label} exceeds {limit} bytes")
        body.extend(chunk)


def run_bounded(
    command: Sequence[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    stdout_limit: int = MAX_JSON_BYTES,
    stderr_limit: int = MAX_STDERR_BYTES,
) -> subprocess.CompletedProcess[bytes]:
    """Capture both API pipes concurrently, with byte limits and one deadline.

    Only the trusted transport command supplied by the caller is executed. No
    shell, temporary output file, or communicate() buffer is involved. Failure
    kills and reaps the direct child. This is not a process-tree sandbox.

    Pipe readers own their unbuffered streams. If a descendant retains a pipe
    after the direct child exits, the caller still times out: cleanup never waits
    indefinitely for EOF or closes a buffered reader locked by another thread.
    """
    if not command:
        raise ValueError("API transport command must not be empty")
    validate_timeout(timeout)
    for limit in (stdout_limit, stderr_limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("response byte limit must be a positive integer")

    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=env,
    )
    assert proc.stdout is not None and proc.stderr is not None
    streams = (proc.stdout, proc.stderr)
    results: list[bytes | None] = [None, None]
    failures: Queue[Exception] = Queue(maxsize=2)
    changed = threading.Event()
    stopped = threading.Event()
    finished = (threading.Event(), threading.Event())

    def drain(index: int, limit: int, label: str) -> None:
        stream = streams[index]
        try:
            # Check cancellation between reads; a blocked OS read can outlive the
            # caller only when a descendant still owns the other end of its pipe.
            data = bytearray()
            while not stopped.is_set():
                chunk = stream.read(min(_CHUNK_BYTES, limit - len(data) + 1))
                if not chunk:
                    results[index] = bytes(data)
                    return
                if len(chunk) > limit - len(data):
                    raise ResponseTooLargeError(f"{label} exceeds {limit} bytes")
                data.extend(chunk)
        except (OSError, ValueError, ResponseTooLargeError) as exc:
            failures.put_nowait(exc)
        finally:
            stream.close()
            finished[index].set()
            changed.set()

    readers = [
        threading.Thread(target=drain, args=(0, stdout_limit, "gh stdout"), daemon=True),
        threading.Thread(target=drain, args=(1, stderr_limit, "gh stderr"), daemon=True),
    ]
    started: list[threading.Thread] = []
    try:
        for reader in readers:
            reader.start()
            started.append(reader)
        while True:
            try:
                failure = failures.get_nowait()
            except Empty:
                pass
            else:
                raise failure
            if all(event.is_set() for event in finished) and proc.poll() is not None:
                # A reader may have failed between the queue check and finished.
                try:
                    failure = failures.get_nowait()
                except Empty:
                    break
                raise failure
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(list(command), timeout)
            changed.wait(min(0.05, remaining))
            changed.clear()
        stdout, stderr = results
        assert stdout is not None and stderr is not None
        return subprocess.CompletedProcess(list(command), proc.wait(timeout=1), stdout, stderr)
    finally:
        stopped.set()
        if proc.poll() is None:
            with suppress(ProcessLookupError):
                proc.kill()
        proc.wait(timeout=5)
        join_deadline = time.monotonic() + 0.2
        for reader in started:
            reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
        for index, reader in enumerate(readers):
            if reader not in started:
                streams[index].close()
