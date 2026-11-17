"""Acquisition boundaries: a capacity-limited filesystem and a shared TLS byte gate.

The caller provisions the filesystem. We never mount disks or elevate privileges. A size
sampler is not a quota: remote Feeder acquisition refuses an ordinary oversized filesystem.
Git keeps end-to-end TLS; the local CONNECT relay counts encrypted server bytes, including
reference discovery, redirects, retries and independent wiki connections.
"""

from __future__ import annotations

import contextvars
import errno
import json
import os
import select
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from ..errors import CrabError, UsageError
from ..file_lock import state_lock
from .bounded import run_bounded

CURRENT: contextvars.ContextVar[AcquisitionBudget | None] = contextvars.ContextVar(
    "crab_acquisition_budget", default=None
)


class AcquisitionLimitError(CrabError):
    """A hard boundary refused acquisition; never treat this as an absent wiki."""


class _RelayScope:
    """Own accepted sockets before blocking work, independently of byte accounting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stopped = False
        self._sockets: set[socket.socket] = set()

    def register(self, stream: socket.socket) -> bool:
        with self._lock:
            if self._stopped:
                return False
            self._sockets.add(stream)
            return True

    def discard(self, stream: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(stream)

    def admit_connect(self, client: socket.socket) -> bool:
        """Linearize upstream admission with stop, before calling the connector.

        A client accepted earlier is not a permit to start upstream work after
        teardown. Already-admitted connects retain their original scope and
        their late results still have to pass register().
        """
        with self._lock:
            return not self._stopped and client in self._sockets

    def acknowledge_connect(self, client: socket.socket, upstream: socket.socket) -> bool:
        """Linearize the short CONNECT reply with stop without blocking teardown."""
        reply = b"HTTP/1.1 200 Connection established\r\n\r\n"
        with self._lock:
            if (
                self._stopped
                or client not in self._sockets
                or upstream not in self._sockets
            ):
                return False
            # A blocking sendall while holding the stop lock could stall cancellation.
            # The new local connection has no queued response bytes: use one
            # nonblocking send and refuse a short write instead.
            timeout = client.gettimeout()
            try:
                client.setblocking(False)
                written = client.send(reply)
            finally:
                client.settimeout(timeout)
            return written == len(reply)

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            streams = tuple(self._sockets)
            self._sockets.clear()
        # A reader can hold the byte-accounting lock while waiting on recv().
        # Closing sockets must not wait for that lock, or cancellation stalls.
        for stream in streams:
            with suppress(OSError):
                stream.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                stream.close()


def volume_capacity(volume: Path) -> int:
    """Use kernel block-device capacity, not free space or metadata-excluding statvfs totals."""
    if sys.platform == "linux":
        device = volume.stat().st_dev
        descriptor = Path(f"/sys/dev/block/{os.major(device)}:{os.minor(device)}/size")
        try:
            with descriptor.open("rb") as source:
                sectors = source.read(64).strip()
            if not sectors.isdigit() or len(sectors) > 20 or int(sectors) <= 0:
                raise ValueError("invalid block-device capacity")
            return int(sectors) * 512
        except (OSError, ValueError) as exc:
            raise UsageError(
                "cannot verify the acquisition filesystem's backing-device capacity",
                hint="provide a dedicated Linux block-device filesystem, such as the fixed-size "
                "loop image provisioned by the Feeder action",
            ) from exc
    elif sys.platform == "win32":
        from .windows_boundary import volume_capacity as windows_capacity

        return windows_capacity(volume)
    else:
        raise UsageError(
            "hard acquisition capacity verification requires a Linux block-device mount"
        )


class AcquisitionBudget:
    def __init__(self, volume: Path, cache: Path, download_bytes: int, disk_bytes: int) -> None:
        for value in (download_bytes, disk_bytes):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise UsageError("acquisition byte limits must be positive integers")
        self.volume = volume.resolve(strict=True)
        if not self.volume.is_dir() or not os.path.ismount(self.volume):
            raise UsageError(
                "acquisition volume must be a dedicated fixed-capacity filesystem mount"
            )
        self.cache = cache.resolve()
        self.download_limit = download_bytes
        self.disk_limit = disk_bytes
        self.downloaded = 0
        self.failure: str | None = None
        self.staging = False
        self.recovery_required = False
        self._lock = threading.Lock()
        self._server: socketserver.ThreadingTCPServer | None = None
        self.require_path(self.cache)
        capacity = volume_capacity(self.volume)
        if capacity > disk_bytes:
            raise UsageError(
                f"acquisition volume capacity {capacity} exceeds the disk limit {disk_bytes}",
                hint="use a dedicated fixed-capacity filesystem (including filesystem metadata); "
                "an ordinary directory or free-space check is not a hard quota",
            )
        self.capacity = capacity
        if sys.platform == "win32":
            # Check assignment/resumption under the actual host's enclosing job
            # policy before any provider or Git network operation can begin.
            result = self.run_git(
                [sys.executable, "-I", "-S", "-B", "-c", "pass"], self.volume, dict(os.environ), 10
            )
            if result.returncode:
                raise UsageError("Windows acquisition process-tree preflight failed")

    def require_path(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.volume):
            raise UsageError("acquisition cache and staging must stay inside the quota volume")
        ancestor = resolved
        while not ancestor.exists():
            ancestor = ancestor.parent
        if ancestor.stat().st_dev != self.volume.stat().st_dev:
            raise UsageError("acquisition path crosses into another filesystem")

    def check(self) -> None:
        if self.failure:
            raise AcquisitionLimitError(
                self.failure,
                hint="choose smaller prey/history or raise the acquisition limits; "
                "no partial acquisition is usable",
            )

    def disk_exhausted(self) -> bool:
        # Diagnostic only: enforcement is the block-device capacity, not this observation.
        if shutil.disk_usage(self.volume).free == 0:
            return True
        if sys.platform == "linux":
            return os.statvfs(self.volume).f_favail == 0
        else:
            return False

    def consume(self, chunk: bytes) -> None:
        with self._lock:
            self.check()
            if len(chunk) > self.download_limit - self.downloaded:
                self.failure = f"acquisition download budget exceeded ({self.download_limit} bytes)"
                self.check()
            self.downloaded += len(chunk)

    def read_upstream(self, stream: socket.socket) -> bytes:
        # Serialize reservations and reads across all Git connections. Otherwise several
        # simultaneous sockets could each consume the entire remaining allowance.
        with self._lock:
            self.check()
            remaining = self.download_limit - self.downloaded
            chunk = stream.recv(min(65536, remaining + 1))
            if len(chunk) > remaining:
                self.failure = f"acquisition download budget exceeded ({self.download_limit} bytes)"
                self.check()
            self.downloaded += len(chunk)
            return chunk

    def git_env(self, base: dict[str, str]) -> dict[str, str]:
        assert self._server is not None
        env = dict(base)
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_INDEX_FILE",
            "GIT_TEMPLATE_DIR",
            "GIT_QUARANTINE_PATH",
        ):
            env.pop(key, None)
        # Fresh staged repositories have no URL-specific proxy overrides. Do not inherit
        # global/system configuration or a NO_PROXY escape from the caller's environment.
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, NO_PROXY="", no_proxy="")
        env.update(TMPDIR=str(self.volume), TEMP=str(self.volume), TMP=str(self.volume))
        env["LC_ALL"] = "C"
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
        for key, value in (
            ("http.proxy", f"http://127.0.0.1:{self._server.server_address[1]}"),
            ("http.followRedirects", "false"),
            ("protocol.allow", "never"),
            ("protocol.https.allow", "always"),
            ("gc.auto", "0"),
            ("maintenance.auto", "false"),
            ("core.hooksPath", os.devnull),
            ("init.templateDir", ""),
        ):
            env[f"GIT_CONFIG_KEY_{count}"] = key
            env[f"GIT_CONFIG_VALUE_{count}"] = value
            count += 1
        env["GIT_CONFIG_COUNT"] = str(count)
        return env

    def require_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"github.com", "gitlab.com"}
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise UsageError("budgeted acquisition requires a GitHub or GitLab HTTPS URL")

    def run_git(
        self, command: list[str], cwd: Path, env: dict[str, str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        if os.name != "posix" and sys.platform != "win32":
            raise UsageError(
                "hard acquisition requires a POSIX runner with process-group cancellation",
                hint="use the Linux Feeder job; other runners fail closed until a verified "
                "process-tree boundary is available",
            )
        return run_bounded(
            command,
            cwd=str(cwd),
            env=env,
            timeout=timeout,
            process_group=True,
        )

    def publish_cache(self, entries: list[tuple[Path, Path]]) -> None:
        """Publish a completed acquisition cohort, rolling back every earlier move on refusal.

        Callers retain their staging until this method returns. Unrelated digest generations,
        ledgers and other prey stay in place; no old API file is modified in place.
        """
        from .cache_identity import require_replacement_identity
        from .catch import rmtree_force

        self.check()
        backup = self.volume / f".acquisition-backup-{uuid.uuid4().hex}"
        backup.mkdir()
        old: list[tuple[Path, Path]] = []
        published: list[tuple[Path, Path]] = []
        preserve = False
        try:
            for source, target in entries:
                self.require_path(source)
                self.require_path(target)
                require_replacement_identity(source, target, self.cache)
            journal = [
                {"source": str(source), "target": str(target), "previous": str(backup / str(index))}
                for index, (source, target) in enumerate(entries)
            ]
            with (backup / "journal.json").open("x", encoding="utf-8") as record:
                json.dump(journal, record)
                record.flush()
                os.fsync(record.fileno())
            for index, (source, target) in enumerate(entries):
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    previous = backup / str(index)
                    target.rename(previous)
                    old.append((previous, target))
                source.rename(target)
                published.append((target, source))
        except BaseException:
            try:
                for target, source in reversed(published):
                    target.rename(source)
                for previous, target in reversed(old):
                    previous.rename(target)
            except OSError as exc:
                preserve = True
                self.recovery_required = True
                raise CrabError(
                    "acquisition cache rollback needs manual recovery",
                    hint=f"previous entries and their recovery journal remain in {backup}; "
                    "retain the acquisition staging",
                ) from exc
            raise
        finally:
            if not preserve:
                rmtree_force(backup, cache_root=self.volume)

    @contextmanager
    def scope(self) -> Iterator[AcquisitionBudget]:
        # One writer owns this finite domain until staging publication/cleanup
        # finishes. Concurrent attempts fail before opening a network connection.
        with state_lock(self.volume / ".crab-acquisition-state.json"), self._scope() as budget:
            yield budget

    @contextmanager
    def _scope(self) -> Iterator[AcquisitionBudget]:
        budget = self
        connections = _RelayScope()

        class Relay(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                client: socket.socket = self.request
                upstream: socket.socket | None = None
                try:
                    client.settimeout(30)
                    header = bytearray()
                    while not header.endswith(b"\r\n\r\n"):
                        part = client.recv(1)
                        if not part or len(header) >= 8192:
                            return
                        header.extend(part)
                    first = bytes(header).split(b"\r\n", 1)[0]
                    if first not in {
                        b"CONNECT github.com:443 HTTP/1.1",
                        b"CONNECT gitlab.com:443 HTTP/1.1",
                    }:
                        return
                    host = first.split()[1].split(b":")[0].decode("ascii")
                    if not connections.admit_connect(client):
                        return
                    upstream = socket.create_connection((host, 443), timeout=30)
                    if not connections.register(upstream):
                        return
                    if not connections.acknowledge_connect(client, upstream):
                        return
                    while True:
                        ready, _, _ = select.select([client, upstream], [], [], 30)
                        if not ready:
                            return
                        for source in ready:
                            target = upstream if source is client else client
                            if source is upstream:
                                chunk = budget.read_upstream(source)
                            else:
                                chunk = source.recv(65536)
                            if not chunk:
                                return
                            target.sendall(chunk)
                except (OSError, AcquisitionLimitError):
                    return
                except ValueError:
                    # Teardown can close a socket before select() reads its fd.
                    if not connections.stopped:
                        raise
                finally:
                    # Stay registered until closed, so concurrent teardown cannot
                    # miss a stream between unregistering and closing it.
                    for stream in (client, upstream):
                        if stream is not None:
                            with suppress(OSError):
                                stream.close()
                            connections.discard(stream)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            block_on_close = False

            def verify_request(
                self,
                request: socket.socket | tuple[bytes, socket.socket],
                client_address: tuple[str, int],
            ) -> bool:
                # TCPServer calls this on acceptance, before starting the handler.
                # Rejected requests are closed by the server without dispatch.
                return isinstance(request, socket.socket) and connections.register(request)

        with Server(("127.0.0.1", 0), Relay) as server:
            self._server = server
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            token = CURRENT.set(self)
            try:
                yield self
                self.check()
            except OSError as exc:
                if exc.errno in (errno.ENOSPC, errno.EDQUOT):
                    raise AcquisitionLimitError(
                        "acquisition disk capacity exhausted",
                        hint="raise the fixed-volume limit or choose smaller prey/history; "
                        "no partial acquisition is usable",
                    ) from exc
                raise
            finally:
                CURRENT.reset(token)
                connections.stop()
                server.shutdown()
                thread.join(timeout=2)
                self._server = None
