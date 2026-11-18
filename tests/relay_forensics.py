"""Bounded, tests-only evidence capture for the historical Windows relay observation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TARGET = "tests/test_acquisition.py::test_main_and_wiki_connections_share_one_real_stream_limit"
ATTEMPTS = 8
TIMEOUT_SECONDS = 45
POST_KILL_DRAIN_SECONDS = 2


def drain_killed_child(
    child: subprocess.Popen[bytes], first_timeout: subprocess.TimeoutExpired
) -> tuple[bytes, bytes, bool]:
    """Do not wait indefinitely for EOF from an escaped pipe-owning descendant."""
    try:
        stdout, stderr = child.communicate(timeout=POST_KILL_DRAIN_SECONDS)
        return stdout, stderr, False
    except subprocess.TimeoutExpired as drain_timeout:
        stdout = drain_timeout.stdout if drain_timeout.stdout is not None else first_timeout.stdout
        stderr = drain_timeout.stderr if drain_timeout.stderr is not None else first_timeout.stderr
        return stdout or b"", stderr or b"", True


def classify(
    returncode: int | None, stdout: bytes, stderr: bytes, *, timed_out: bool, pipe_open: bool
) -> list[str]:
    """Report observations independently; a diagnostic alone is not a process exit."""
    observations: list[str] = []
    if timed_out:
        observations.append("timeout")
    if pipe_open:
        observations.append("post_kill_pipe_open")
    if returncode is None:
        observations.append("exit_unavailable")
    elif returncode != 0:
        observations.append("nonzero_exit")
    if any(
        marker in stream.lower()
        for marker in (b"windows fatal exception:", b"fatal python error:")
        for stream in (stdout, stderr)
    ):
        observations.append("faulthandler_diagnostic")
    if re.search(rb"(?m)^1 passed(?: in |\r?$)", stdout) is None:
        observations.append("expected_test_summary_missing")
    return observations


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=5,
    ).stdout.strip()


def _provenance(root: Path) -> dict[str, object]:
    tracked_changes = _git(root, "status", "--porcelain", "--untracked-files=no")
    return {
        "subject_sha": _git(root, "rev-parse", "HEAD"),
        "tracked_changes": tracked_changes,
        "source_role": "current_checkout_not_established_historical_failure_snapshot",
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "tests/relay_forensics.py",
                "tests/test_acquisition_windows_forensics.py",
                "tests/test_acquisition.py",
                "src/hungry_crab/fetch/acquisition.py",
            )
        },
        "python": sys.version,
        "python_executable": sys.executable,
        "python_implementation": platform.python_implementation(),
        "python_build": platform.python_build(),
        "platform": platform.platform(),
        "windows": platform.win32_ver(),
        "actions": {
            name: os.environ[name]
            for name in (
                "GITHUB_REPOSITORY",
                "GITHUB_SHA",
                "GITHUB_WORKFLOW_REF",
                "GITHUB_WORKFLOW_SHA",
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
                "GITHUB_JOB",
                "RUNNER_OS",
                "ImageOS",
                "ImageVersion",
            )
            if name in os.environ
        },
    }


def capture_series(
    root: Path, directory: Path, *, command: list[str] | None = None
) -> dict[str, object]:
    """Retain every observation, stop at the first anomaly, and never retry it away."""
    directory.mkdir(parents=True, exist_ok=False)
    command = command or [
        sys.executable,
        "-X",
        "faulthandler",
        "-m",
        "pytest",
        "-q",
        "-s",
        "--maxfail=1",
        TARGET,
    ]
    attempts: list[dict[str, object]] = []
    report: dict[str, object] = {
        "schema": 1,
        "provenance": _provenance(root),
        "command": command,
        "attempt_limit": ATTEMPTS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "post_kill_drain_seconds": POST_KILL_DRAIN_SECONDS,
        "outcome": "CAPTURE_IN_PROGRESS",
        "attempts": attempts,
    }
    _write_json(directory / "manifest.json", report)
    for number in range(1, ATTEMPTS + 1):
        started = datetime.now(UTC).isoformat()
        child = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        timed_out = False
        pipe_open = False
        try:
            stdout, stderr = child.communicate(timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as first_timeout:
            timed_out = True
            child.kill()
            stdout, stderr, pipe_open = drain_killed_child(child, first_timeout)
        observations = classify(
            child.returncode, stdout, stderr, timed_out=timed_out, pipe_open=pipe_open
        )
        streams: dict[str, object] = {}
        for name, data in (("stdout", stdout), ("stderr", stderr)):
            filename = f"attempt-{number:02d}.{name}.bin"
            (directory / filename).write_bytes(data)
            streams[name] = {
                "file": filename,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        record = {
            "attempt": number,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "pid": child.pid,
            "returncode": child.returncode,
            "returncode_u32_hex": (
                None if child.returncode is None else f"0x{child.returncode & 0xFFFFFFFF:08X}"
            ),
            "timed_out": timed_out,
            "pipe_open": pipe_open,
            "streams_complete": not pipe_open,
            "streams": streams,
            "observations": observations,
        }
        attempts.append(record)
        _write_json(directory / f"attempt-{number:02d}.json", record)
        if observations:
            report["outcome"] = "FIRST_ANOMALY_RETAINED"
            report["first_anomalous_attempt"] = number
            _write_json(directory / "manifest.json", report)
            return report
        _write_json(directory / "manifest.json", report)
    report["outcome"] = "NOT_REPRODUCED"
    _write_json(directory / "manifest.json", report)
    return report
