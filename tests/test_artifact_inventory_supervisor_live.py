"""Hosted, inert process controls for the private attestation supervisor."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

from hungry_crab import artifact_inventory_supervisor as supervisor
from hungry_crab.errors import CrabError
from hungry_crab.fetch.bounded import run_bounded


def test_installed_worker_rejects_invalid_request_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An invalid token is rejected before any HTTPS connection can be opened.
    # A shadow module in cwd/PYTHONPATH must not be executed.
    shadow = tmp_path / "hungry_crab"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('shadow import')", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    captured: list[subprocess.CompletedProcess[bytes]] = []

    def capture(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        result = run_bounded(command, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(supervisor, "run_bounded", capture)
    with pytest.raises(CrabError, match="worker refused"):
        supervisor.IsolatedAttestationHTTPS("", deadline=monotonic() + 15)(
            "https://invalid.example/", 100
        )
    assert len(captured) == 1
    assert captured[0].returncode == 1
    assert captured[0].stdout == captured[0].stderr == b""


def test_real_hanging_child_is_reaped_even_after_flushing_valid_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Substitute only an inert first-party test program at the launch seam.
    # It emits a valid-looking response but never exits voluntarily.
    started = tmp_path / "started"
    program = (
        "import pathlib, sys, time; "
        "sys.stdin.buffer.read(); "
        "pathlib.Path(sys.argv[1]).touch(); "
        "sys.stdout.buffer.write(b'[200,\"AP8=\",[],true]'); "
        "sys.stdout.buffer.flush(); time.sleep(60)"
    )
    processes: list[subprocess.Popen[bytes]] = []
    original_popen = subprocess.Popen

    def capture_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc = original_popen(*args, **kwargs)
        processes.append(proc)
        return proc

    def launch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert command[1:] == ["-I", "-m", "hungry_crab.artifact_inventory_worker"]
        assert kwargs["process_group"] is True
        return run_bounded([sys.executable, "-I", "-c", program, str(started)], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    monkeypatch.setattr(supervisor, "run_bounded", launch)
    before = monotonic()
    with pytest.raises(CrabError, match=r"worker failed|deadline expired"):
        supervisor.IsolatedAttestationHTTPS("synthetic_token", deadline=before + 5)(
            "https://invalid.example/", 100
        )
    assert started.is_file(), "the real synthetic child must have entered its blocking phase"
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert monotonic() - before < 20  # generous cleanup grace, not a hard deadline claim
