"""Keep native diagnostics and actual exits distinct while retaining the first anomaly."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from relay_forensics import (
    POST_KILL_DRAIN_SECONDS,
    TIMEOUT_SECONDS,
    _event_pr_head_sha,
    _pytest_command,
    capture_series,
    classify,
    drain_killed_child,
)


def test_event_pr_head_sha_is_validated_not_confused_with_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRAB_RELAY_PR_HEAD_SHA", "A" * 40)
    assert _event_pr_head_sha() == "a" * 40
    monkeypatch.setenv("CRAB_RELAY_PR_HEAD_SHA", "not-a-commit")
    with pytest.raises(ValueError, match="invalid GitHub PR source SHA"):
        _event_pr_head_sha()
    monkeypatch.delenv("CRAB_RELAY_PR_HEAD_SHA", raising=False)
    assert _event_pr_head_sha() is None


def test_full_acquisition_profile_marks_fixture_phase_and_timed_stack() -> None:
    command = _pytest_command(capture_mode="sys", profile="acquisition_file")
    assert command[-1] == "tests/test_acquisition.py"
    assert "--setup-show" in command
    option = command.index("-o")
    assert command[option + 1] == "faulthandler_timeout=30"
    assert command.index("--setup-show") < command.index("tests/test_acquisition.py")

    single = _pytest_command(capture_mode="sys", profile="quota_target")
    assert "--setup-show" not in single
    assert "faulthandler_timeout=30" not in single
    assert single[-1].endswith(
        "::test_main_and_wiki_connections_share_one_real_stream_limit"
    )


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_zero_exit_keeps_native_diagnostic_separate_from_process_exit(stream: str) -> None:
    stdout = b"1 passed in 0.1s\n"
    stderr = b""
    diagnostic = b"Windows fatal exception: code 0xc0000008\n"
    if stream == "stdout":
        stdout += diagnostic
    else:
        stderr = diagnostic
    assert classify(0, stdout, stderr, timed_out=False, pipe_open=False) == [
        "faulthandler_diagnostic"
    ]


@pytest.mark.parametrize("stdout", [b"11 passed in 0.1s\n", b"1 passed, 1 skipped\n", b""])
def test_only_the_exact_target_summary_is_accepted(stdout: bytes) -> None:
    assert classify(0, stdout, b"", timed_out=False, pipe_open=False) == [
        "expected_test_summary_missing"
    ]


def test_timeout_and_unavailable_exit_remain_incomplete_observations() -> None:
    assert classify(None, b"1 passed in 0.1s\n", b"", timed_out=True, pipe_open=True) == [
        "timeout",
        "post_kill_pipe_open",
        "exit_unavailable",
    ]


def test_file_context_summary_does_not_weaken_the_single_target_requirement() -> None:
    summary = b"========================= 15 passed in 8.0s =========================\n"
    assert classify(0, summary, b"", timed_out=False, pipe_open=False) == [
        "expected_test_summary_missing"
    ]
    assert classify(0, summary, b"", timed_out=False, pipe_open=False, single_target=False) == []
    assert classify(
        0, b"no tests ran", b"", timed_out=False, pipe_open=False, single_target=False
    ) == ["expected_test_summary_missing"]


def test_post_kill_pipe_drain_is_bounded_when_descendant_keeps_pipe_open() -> None:
    child = Mock(spec=subprocess.Popen)
    original_timeout = subprocess.TimeoutExpired(["pytest"], TIMEOUT_SECONDS, output=b"early")
    child.communicate.side_effect = subprocess.TimeoutExpired(
        ["pytest"], POST_KILL_DRAIN_SECONDS, output=b"partial", stderr=b"open stderr"
    )
    assert drain_killed_child(cast(subprocess.Popen[bytes], child), original_timeout) == (
        b"partial",
        b"open stderr",
        True,
    )
    child.communicate.assert_called_once_with(timeout=POST_KILL_DRAIN_SECONDS)


def test_post_kill_pipe_drain_keeps_first_timeout_bytes_when_drain_has_none() -> None:
    child = Mock(spec=subprocess.Popen)
    original_timeout = subprocess.TimeoutExpired(
        ["pytest"], TIMEOUT_SECONDS, output=b"first\xff", stderr=b"first stderr"
    )
    child.communicate.side_effect = subprocess.TimeoutExpired(["pytest"], POST_KILL_DRAIN_SECONDS)
    assert drain_killed_child(cast(subprocess.Popen[bytes], child), original_timeout) == (
        b"first\xff",
        b"first stderr",
        True,
    )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_first_anomaly_preserves_raw_bytes_and_never_runs_another_attempt(
    tmp_path: Path, exit_code: int
) -> None:
    # This owned child emits a synthetic message; it does not raise a native exception.
    marker = tmp_path / "executions.txt"
    script = (
        "import pathlib, sys; "
        f"p = pathlib.Path({str(marker)!r}); "
        "p.write_text(p.read_text() + 'x' if p.exists() else 'x'); "
        "sys.stdout.buffer.write(b'1 passed in 0.1s\\n'); "
        "sys.stderr.buffer.write(b'Windows fatal exception: synthetic\\xff\\r\\n'); "
        f"sys.exit({exit_code})"
    )
    directory = tmp_path / "evidence"
    report = capture_series(
        Path(__file__).resolve().parents[1],
        directory,
        command=[sys.executable, "-c", script],
    )
    assert marker.read_text() == "x"
    assert report["outcome"] == "FIRST_ANOMALY_RETAINED"
    assert report["first_anomalous_attempt"] == 1
    stored = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert len(stored["attempts"]) == 1
    attempt = stored["attempts"][0]
    assert attempt["returncode"] == exit_code
    assert attempt["returncode_u32_hex"] == f"0x{exit_code:08X}"
    assert ("nonzero_exit" in attempt["observations"]) == (exit_code != 0)
    assert "faulthandler_diagnostic" in attempt["observations"]
    assert attempt["streams_complete"] is True
    assert (directory / "attempt-01.stdout.bin").read_bytes() == b"1 passed in 0.1s\n"
    assert (directory / "attempt-01.stderr.bin").read_bytes() == (
        b"Windows fatal exception: synthetic\xff\r\n"
    )
    assert not (directory / "attempt-02.json").exists()
    assert stored["provenance"]["subject_sha"]
    assert stored["provenance"]["subject_tree_sha"]
    assert stored["provenance"]["harness_tree_sha"]
    assert stored["provenance"]["source_sha256"]["src/hungry_crab/fetch/acquisition.py"]


def test_clean_bounded_series_retains_all_streams_without_claiming_historical_fix(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "clean-evidence"
    report = capture_series(
        Path(__file__).resolve().parents[1],
        directory,
        command=[sys.executable, "-c", "print('1 passed in 0.1s')"],
    )
    assert report["outcome"] == "NOT_REPRODUCED"
    assert "first_anomalous_attempt" not in report
    stored = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert len(stored["attempts"]) == stored["attempt_limit"] == 8
    assert stored["provenance"]["harness_sha"] == stored["provenance"]["subject_sha"]
    assert stored["provenance"]["historical_failure_snapshot_reconstructed"] is False
    assert stored["provenance"]["package_versions"]["pytest"]
    for attempt in stored["attempts"]:
        assert attempt["returncode"] == 0
        assert attempt["observations"] == []
        for name in ("stdout", "stderr"):
            stream = attempt["streams"][name]
            assert len((directory / stream["file"]).read_bytes()) == stream["bytes"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native-handle diagnostic only")
def test_relay_select_teardown_preserves_first_native_process_failure(tmp_path: Path) -> None:
    parent = Path(os.environ.get("CRAB_RELAY_FORENSICS_DIR", str(tmp_path)))
    directory = parent / "relay-observations"
    report = capture_series(Path(__file__).resolve().parents[1], directory)
    if report["outcome"] != "NOT_REPRODUCED":
        pytest.fail(
            f"Relay FIRST anomaly retained at {directory}; "
            "diagnostic text and process exit are separate observations.\n"
            + json.dumps(report, indent=2),
            pytrace=False,
        )
