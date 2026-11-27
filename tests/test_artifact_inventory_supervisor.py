"""Inert supervisor controls, without network or provider credentials."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hungry_crab import artifact_inventory_supervisor as supervisor
from hungry_crab.artifact_inventory import MAX_BYTES, AttestationPage
from hungry_crab.artifact_inventory_worker import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
from hungry_crab.errors import CrabError

TOKEN = "synthetic_token"
URL = "https://api.github.com/repos/owner/repo/attestations/sha256:" + "a" * 64
GOOD = b'[200,"AP8=",["first","second"],true]'


def test_same_deadline_and_isolated_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    timeouts = []
    monkeypatch.setattr(supervisor, "monotonic", lambda: now[0])
    for key in ("GH_TOKEN", "SSLKEYLOGFILE", "SSL_CERT_FILE", "PYTHONPATH", "HTTPS_PROXY"):
        monkeypatch.setenv(key, TOKEN)

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert command == [sys.executable, "-I", "-m", "hungry_crab.artifact_inventory_worker"]
        assert TOKEN not in repr(command)
        assert kwargs["cwd"] == str(Path(sys.executable).parent)
        expected_env = (
            {"SystemRoot": supervisor.os.environ["SystemRoot"]} if sys.platform == "win32" else {}
        )
        assert kwargs["env"] == expected_env
        assert kwargs["process_group"] is True
        assert kwargs["stdout_limit"] == MAX_RESPONSE_BYTES
        assert kwargs["stderr_limit"] == 4096
        assert json.loads(kwargs["input_data"]) == [TOKEN, URL, 100, min(30, 140 - now[0])]
        timeouts.append(kwargs["timeout"])
        now[0] += 10
        return subprocess.CompletedProcess(command, 0, GOOD, b"")

    monkeypatch.setattr(supervisor, "run_bounded", run)
    fetch = supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=140)
    expected = AttestationPage(200, b"\x00\xff", ("first", "second"), True)
    assert fetch(URL, 100) == expected
    assert fetch(URL, 100) == expected
    assert timeouts == [40, 30]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not JSON",
        b"[200,1,[],true]",
        b'[200,"!!!!",[],true]',
        b'[200,"AP9=",[],true]',
        b'[200,"AP8=",[1],true]',
        b'[200,"AP8=",[],false]',
        b'[200.0,"AP8=",[],true]',
        b'[500,"AP8=",[],true]',
        b'[200,"AP8=",["\\ud800"],true]',
        b'[200,"AP8=",[],true,null]',
    ],
)
def test_rejects_invalid_worker_payload(raw: bytes) -> None:
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        supervisor._decode_page(raw, 100)


def test_response_size_and_body_budget() -> None:
    with pytest.raises(CrabError, match="response size"):
        supervisor._decode_page(b"x" * (MAX_RESPONSE_BYTES + 1), MAX_BYTES)
    with pytest.raises(CrabError, match="invalid worker response"):
        supervisor._decode_page(GOOD, 1)


@pytest.mark.parametrize("returncode,stderr", [(1, b""), (0, b"diagnostic")])
def test_no_partial_or_diagnostic_output(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stderr: bytes
) -> None:
    monkeypatch.setattr(supervisor, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        supervisor,
        "run_bounded",
        lambda *a, **k: subprocess.CompletedProcess([], returncode, GOOD, stderr),
    )
    with pytest.raises(CrabError, match="worker refused"):
        supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=101)(URL, 100)


def test_transport_exception_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "monotonic", lambda: 100.0)

    def fail(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired([TOKEN], 1)

    monkeypatch.setattr(supervisor, "run_bounded", fail)
    with pytest.raises(CrabError, match="worker failed") as exc:
        supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=101)(URL, 100)
    assert TOKEN not in str(exc.value)


def test_late_success_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(supervisor, "monotonic", lambda: now[0])

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        now[0] = 101.0
        return subprocess.CompletedProcess([], 0, GOOD, b"")

    monkeypatch.setattr(supervisor, "run_bounded", run)
    with pytest.raises(CrabError, match="deadline expired"):
        supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=101)(URL, 100)


def test_preflight_never_launches_expired_or_oversized_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor, "monotonic", lambda: 100.0)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unexpected launch")

    monkeypatch.setattr(supervisor, "run_bounded", forbidden)
    with pytest.raises(CrabError, match="deadline expired"):
        supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=100)(URL, 100)
    with pytest.raises(CrabError, match="request exceeds byte budget"):
        supervisor.IsolatedAttestationHTTPS("x" * MAX_REQUEST_BYTES, deadline=101)(URL, 100)
    with pytest.raises(CrabError, match="invalid inventory byte budget"):
        supervisor.IsolatedAttestationHTTPS(TOKEN, deadline=101)(URL, MAX_BYTES + 1)
