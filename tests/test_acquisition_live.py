"""Real Git HTTPS/process/filesystem tests on a disposable, capacity-limited CI volume."""

from __future__ import annotations

import hashlib
import http.server
import os
import random
import socket
import ssl
import subprocess
import threading
from contextlib import suppress
from pathlib import Path

import pytest
from fixture_builder import git

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.fetch.acquisition import AcquisitionBudget, AcquisitionLimitError
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.github import GitHubClient


def _fingerprints(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_real_https_git_quota_cancellation_and_cache_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supplied = os.environ.get("CRAB_TEST_QUOTA_VOLUME")
    if not supplied:
        pytest.skip("requires the dedicated acquisition-quota CI filesystem")
    volume = Path(supplied)
    repositories = {}
    for name in ("prey.git", "prey.wiki.git"):
        repo = tmp_path / name
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "Fixture")
        git(repo, "config", "user.email", "fixture@invalid.example")
        (repo / "Home.md").write_text("# Fixture\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "docs: fixture")
        repositories["/fixture/" + name] = repo
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=github.com",
            "-addext",
            "subjectAltName=DNS:github.com",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    monkeypatch.setenv("GIT_SSL_CAINFO", str(cert))
    # An inherited per-URL proxy outranks a broad proxy setting unless stripped first.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.https://github.com/fixture/prey.git.proxy")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "http://127.0.0.1:1")

    class GitHTTP(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            repo = repositories[self.path.split("/info/refs")[0]]
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "upload-pack",
                    "--stateless-rpc",
                    "--advertise-refs",
                    str(repo),
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            body = b"001e# service=git-upload-pack\n0000" + result.stdout
            self.reply(body, "application/x-git-upload-pack-advertisement")

        def do_POST(self) -> None:
            repo = repositories[self.path.removesuffix("/git-upload-pack")]
            request = self.rfile.read(int(self.headers["Content-Length"]))
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "upload-pack",
                    "--stateless-rpc",
                    str(repo),
                ],
                input=request,
                capture_output=True,
                check=True,
                timeout=120,
            )
            self.reply(result.stdout, "application/x-git-upload-pack-result")

        def reply(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # A budget refusal deliberately disconnects this trusted fixture server.
            with suppress(OSError):
                self.wfile.write(body)

    actual_connect = socket.create_connection
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHTTP) as server:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def connect(address: tuple[str, int], **kwargs: object) -> socket.socket:
            assert address == ("github.com", 443)
            return actual_connect(server.server_address, timeout=30)

        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        slug = Slug("fixture", "prey")
        cache = volume / "cache"
        opts = CatchOptions(shallow=True, wiki=True)

        def acquire(limit: int = 200 * 1024 * 1024) -> None:
            budget = AcquisitionBudget(volume, cache, limit, 64 * 1024 * 1024)
            with budget.scope():
                catch(slug, opts, cache_root=cache, github=GitHubClient(prefer_gh=False))

        try:
            acquire()
            old = _fingerprints(prey_paths(slug, cache).root)
            with pytest.raises(AcquisitionLimitError, match="download budget"):
                acquire(1024)
            assert _fingerprints(prey_paths(slug, cache).root) == old
            # Compressible main content passes transport but cannot fit the actual checkout.
            main = repositories["/fixture/prey.git"]
            (main / "Huge.bin").write_bytes(b"0" * (80 * 1024 * 1024))
            git(main, "add", ".")
            git(main, "commit", "-qm", "test: oversized checkout")
            with pytest.raises(AcquisitionLimitError, match="disk capacity"):
                acquire()
            assert _fingerprints(prey_paths(slug, cache).root) == old
            git(main, "reset", "--hard", "HEAD~1")
            # A wiki has no checkout; its irreducible Git pack must hit the same filesystem cap.
            wiki = repositories["/fixture/prey.wiki.git"]
            (wiki / "Huge.bin").write_bytes(random.Random(236).randbytes(80 * 1024 * 1024))
            git(wiki, "add", ".")
            git(wiki, "commit", "-qm", "test: oversized wiki pack")
            with pytest.raises(AcquisitionLimitError, match="disk capacity"):
                acquire()
            assert _fingerprints(prey_paths(slug, cache).root) == old
            assert not list(volume.glob(".acquisition-*"))
            assert not list(cache.rglob("*-old-*"))
        finally:
            server.shutdown()
            thread.join(timeout=2)
