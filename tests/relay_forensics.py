"""Bounded, tests-only evidence capture for the historical Windows relay observation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
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
SOURCE_ROLES = ("current_checkout", "historical_acquisition_reference")
PROFILES = ("quota_target", "acquisition_file")


def _event_pr_head_sha() -> str | None:
    """Keep the validated PR source revision distinct from a synthetic merge checkout."""
    value = os.environ.get("CRAB_RELAY_PR_HEAD_SHA", "")
    if not value:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        raise ValueError("invalid GitHub PR source SHA")
    return value.lower()


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
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    *,
    timed_out: bool,
    pipe_open: bool,
    single_target: bool = True,
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
    summary = (
        rb"(?m)^1 passed(?: in |\r?$)" if single_target else rb"(?m)^(?:=+ )?[1-9][0-9]* passed\b"
    )
    if re.search(summary, stdout) is None:
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


def _provenance(root: Path, source_role: str) -> dict[str, object]:
    harness = Path(__file__).resolve().parents[1]
    tracked_changes = _git(root, "status", "--porcelain", "--untracked-files=no")
    return {
        "subject_sha": _git(root, "rev-parse", "HEAD"),
        "subject_tree_sha": _git(root, "rev-parse", "HEAD^{tree}"),
        "event_pr_head_sha": _event_pr_head_sha(),
        "tracked_changes": tracked_changes,
        "source_role": source_role,
        "historical_failure_snapshot_reconstructed": False,
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "tests/test_acquisition.py",
                "src/hungry_crab/fetch/acquisition.py",
            )
        },
        "harness_sha": _git(harness, "rev-parse", "HEAD"),
        "harness_tree_sha": _git(harness, "rev-parse", "HEAD^{tree}"),
        "harness_tracked_changes": _git(harness, "status", "--porcelain", "--untracked-files=no"),
        "harness_source_sha256": {
            name: hashlib.sha256((harness / name).read_bytes()).hexdigest()
            for name in (
                "tests/relay_forensics.py",
                "tests/test_acquisition_windows_forensics.py",
            )
        },
        "uv_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "package_versions": {
            name: importlib.metadata.version(name) for name in ("pytest", "pluggy", "pytest-cov")
        },
        "python": sys.version,
        "python_executable": sys.executable,
        "python_executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        "python_implementation": platform.python_implementation(),
        "python_build": platform.python_build(),
        "python_compiler": platform.python_compiler(),
        "platform": platform.platform(),
        "windows": platform.win32_ver(),
        "actions": {
            name: os.environ[name]
            for name in (
                "GITHUB_REPOSITORY",
                "GITHUB_EVENT_NAME",
                "GITHUB_HEAD_REF",
                "GITHUB_BASE_REF",
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


def _pytest_command(*, capture_mode: str, profile: str) -> list[str]:
    """Keep fixture-phase evidence and a Python stack before the outer child timeout."""
    phase_evidence = (
        ["--setup-show", "-o", "faulthandler_timeout=30"] if profile == "acquisition_file" else []
    )
    return [
        sys.executable,
        "-X",
        "faulthandler",
        "-m",
        "pytest",
        "-q" if profile == "quota_target" else "-vv",
        f"--capture={capture_mode}",
        "--color=no",
        "--maxfail=1",
        "--tb=short",
        *phase_evidence,
        TARGET if profile == "quota_target" else "tests/test_acquisition.py",
    ]


def capture_series(
    root: Path,
    directory: Path,
    *,
    command: list[str] | None = None,
    capture_mode: str = "no",
    source_role: str = "current_checkout",
    profile: str = "quota_target",
) -> dict[str, object]:
    """Retain every observation, stop at the first anomaly, and never retry it away."""
    if (
        capture_mode not in ("no", "sys")
        or source_role not in SOURCE_ROLES
        or profile not in PROFILES
    ):
        raise ValueError("unsupported bounded relay diagnostic profile")
    directory.mkdir(parents=True, exist_ok=False)
    command = command or _pytest_command(capture_mode=capture_mode, profile=profile)
    attempts: list[dict[str, object]] = []
    report: dict[str, object] = {
        "schema": 2,
        "provenance": _provenance(root, source_role),
        "command": command,
        "capture_mode": capture_mode,
        "profile": profile,
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
            child.returncode,
            stdout,
            stderr,
            timed_out=timed_out,
            pipe_open=pipe_open,
            single_target=profile == "quota_target",
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


def main(argv: list[str] | None = None) -> int:
    """Run only the owned quota target against an explicit own-repository checkout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--capture", choices=("no", "sys"), default="sys")
    parser.add_argument("--source-role", choices=SOURCE_ROLES, required=True)
    parser.add_argument("--profile", choices=PROFILES, default="quota_target")
    arguments = parser.parse_args(argv)
    report = capture_series(
        arguments.subject.resolve(strict=True),
        arguments.evidence.resolve(),
        capture_mode=arguments.capture,
        source_role=arguments.source_role,
        profile=arguments.profile,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["outcome"] == "NOT_REPRODUCED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
