from __future__ import annotations

import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.errors import UsageError
from hungry_crab.feeder import EatOptions, eat
from hungry_crab.fetch.acquisition import AcquisitionBudget, AcquisitionLimitError
from hungry_crab.fetch.bounded import run_bounded
from hungry_crab.fetch.catch import CatchOptions
from hungry_crab.fetch.git import git_env
from hungry_crab.sniff import build_report


@pytest.fixture
def quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AcquisitionBudget:
    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    return AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 4096)


def test_remote_feeder_refuses_an_unbounded_filesystem_before_network(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("network was reached before the quota boundary")

    monkeypatch.setattr("hungry_crab.feeder.sniff", unexpected)
    with pytest.raises(UsageError, match="acquisition-volume"):
        eat(
            Target(slug=Slug("fixture", "prey")),
            npm_app,
            EatOptions(cache_root=tmp_path / "cache", wiki=False, out=tmp_path / "out"),
        )
    assert not (tmp_path / "out").exists()


def test_capacity_and_path_are_real_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    with pytest.raises(UsageError, match="capacity"):
        AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 1)
    with pytest.raises(UsageError, match="inside"):
        AcquisitionBudget(tmp_path, tmp_path.parent / "outside", 10, 1)


@pytest.mark.parametrize("reason", ["download budget exceeded", "disk capacity exhausted"])
def test_feeder_never_exports_a_success_bundle_after_a_quota_refusal(
    quota: AcquisitionBudget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    (maw / ".crab.yml").write_text("license: MIT\n", encoding="utf-8")
    slug = Slug("fixture", "prey")
    monkeypatch.setattr("hungry_crab.feeder.relationship_for", lambda *_: "foreign")
    monkeypatch.setattr(
        "hungry_crab.feeder.sniff",
        lambda *_args, **_kwargs: build_report(slug, {"size": 1}, {}, maw_license="MIT"),
    )

    def refused(*args: object, **kwargs: object) -> None:
        raise AcquisitionLimitError(reason)

    monkeypatch.setattr("hungry_crab.feeder.catch", refused)
    out = tmp_path / "bundle"
    with pytest.raises(AcquisitionLimitError, match=reason), quota.scope():
        eat(Target(slug=slug), maw, EatOptions(cache_root=quota.cache, out=out, wiki=False))
    assert not out.exists()


def test_git_cannot_bypass_the_relay_or_write_to_inherited_directories(
    quota: AcquisitionBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "outside")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.https://github.com/fixture/prey.git.proxy")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "http://127.0.0.1:1")
    with quota.scope():
        env = quota.git_env(git_env())
        assert env["NO_PROXY"] == env["no_proxy"] == ""
        assert "GIT_OBJECT_DIRECTORY" not in env
        assert env["TEMP"] == str(quota.volume)
        values = {
            env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
            for i in range(int(env["GIT_CONFIG_COUNT"]))
        }
        assert values["http.proxy"].startswith("http://127.0.0.1:")
        assert values["protocol.allow"] == "never"
        assert values["protocol.https.allow"] == "always"
        assert values["http.followRedirects"] == "false"
        assert not any(key.startswith("http.https://") for key in values)


@pytest.mark.parametrize(
    "url",
    [
        "file:///repo",
        "ssh://github.com/a/b",
        "https://other.test/a",
        "https://user:secret@github.com/a/b",
    ],
)
def test_only_audited_https_transports_are_accepted(quota: AcquisitionBudget, url: str) -> None:
    with pytest.raises(UsageError, match="HTTPS"):
        quota.require_url(url)


def test_main_and_wiki_connections_share_one_real_stream_limit(
    quota: AcquisitionBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Remote(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.sendall(b"123456")

    actual_connect = socket.create_connection
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Remote) as remote:
        worker = threading.Thread(target=remote.serve_forever, daemon=True)
        worker.start()

        def connect(address: tuple[str, int], **kwargs: object) -> socket.socket:
            return actual_connect(
                remote.server_address if address[1] == 443 else address, timeout=3
            )

        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        with pytest.raises(AcquisitionLimitError, match="download budget"), quota.scope():
            assert quota._server is not None
            received = []
            for _ in range(2):
                with actual_connect(quota._server.server_address, timeout=3) as client:
                    client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
                    header = b""
                    while not header.endswith(b"\r\n\r\n"):
                        header += client.recv(1)
                    body = b""
                    while chunk := client.recv(64):
                        body += chunk
                    received.append(body)
            assert received == [b"123456", b""]
            assert quota.downloaded == 6
            quota.check()
        remote.shutdown()
        worker.join(timeout=2)


def test_concurrent_reads_cannot_each_spend_the_remaining_allowance(
    quota: AcquisitionBudget,
) -> None:
    received = []
    failures = []

    class Stream:
        def recv(self, size: int) -> bytes:
            chunk = b"x" * min(size, 6)
            received.append(len(chunk))
            time.sleep(0.01)
            return chunk

    def read() -> None:
        try:
            quota.read_upstream(Stream())
        except AcquisitionLimitError as exc:
            failures.append(exc)

    workers = [threading.Thread(target=read) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert sum(received) == quota.download_limit + 1
    assert quota.downloaded == 6 and len(failures) == 3


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group boundary")
def test_timeout_reaps_git_style_descendants_before_staging_cleanup(tmp_path: Path) -> None:
    spawned, escaped = tmp_path / "spawned", tmp_path / "escaped"
    program = (
        "import subprocess,sys,time;from pathlib import Path;"
        "subprocess.Popen([sys.executable,'-c',"
        "'import sys,time;from pathlib import Path;time.sleep(3);"
        'Path(sys.argv[1]).write_text("bad")\',sys.argv[2]]);'
        "Path(sys.argv[1]).write_text('spawned');time.sleep(10)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(
            [sys.executable, "-c", program, str(spawned), str(escaped)],
            timeout=1,
            process_group=True,
        )
    assert spawned.exists()
    time.sleep(3)
    assert not escaped.exists()


@pytest.mark.parametrize(
    "failure", [AcquisitionLimitError("wiki exceeded budget"), KeyboardInterrupt()]
)
def test_wiki_failure_or_cancellation_preserves_the_whole_previous_cache(
    quota: AcquisitionBudget, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    # import_module avoids the package's exported catch function.
    import importlib

    catch_module = importlib.import_module("hungry_crab.fetch.catch")
    slug = Slug("fixture", "prey")
    previous = prey_paths(slug, quota.cache)
    previous.root.mkdir(parents=True)
    old = {"slug": str(slug), "sha": "old"}
    previous.catch_file.write_text(json.dumps(old), encoding="utf-8")
    (previous.root / "sentinel").write_text("keep", encoding="utf-8")
    original = catch_module.catch

    def partial(*args: object, **kwargs: object) -> None:
        root = kwargs["cache_root"]
        assert isinstance(root, Path)
        staged = prey_paths(slug, root)
        staged.root.mkdir(parents=True)
        (staged.root / "partial-main").write_text("new main", encoding="utf-8")
        raise failure

    monkeypatch.setattr(catch_module, "catch", partial)
    with pytest.raises(type(failure)), quota.scope():
        original(slug, CatchOptions(), cache_root=quota.cache)
    assert json.loads(previous.catch_file.read_text()) == old
    assert (previous.root / "sentinel").read_text() == "keep"
    assert not list(quota.volume.glob(".acquisition-*"))
