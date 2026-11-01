"""Host accounting survives bad semantics, interrupts and attempt changes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
import research_host as host


def result(*, status: str = "completed", answer: str = "{}", tokens: int = 13) -> dict[str, Any]:
    return {
        "usage_complete": True,
        "status": status,
        "usage": {"totalTokens": tokens},
        "seconds": 0.1,
        "budget_valid": True,
        "messages": [answer],
    }


@pytest.mark.parametrize("status", ["completed", "interrupted"])
def test_actual_usage_settles_once_before_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    def worker(directory: Path, *_: Any, **__: Any) -> dict[str, Any]:
        directory.mkdir()
        value = result(status=status)
        host.write(directory / "result.json", value)
        return value

    monkeypatch.setattr(host, "run_worker", worker)
    meter = host.Meter(tmp_path / "meter", cwd=tmp_path, model="model", effort="medium")
    value = meter.call("stage", "prompt", {}, attempt="one")
    assert value == ({} if status == "completed" else None)
    assert meter.data["tokens"] == 13
    assert meter.call("stage", "prompt", {}, attempt="one") == value
    assert meter.data["tokens"] == 13
    with pytest.raises(RuntimeError, match="owner"):
        meter.call("stage", "prompt", {}, attempt="two")


def test_malformed_semantics_retains_measured_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(host, "run_worker", lambda *_args, **_kwargs: result(answer="bad json"))
    meter = host.Meter(tmp_path, cwd=tmp_path, model="model", effort="medium")
    with pytest.raises(json.JSONDecodeError):
        meter.call("stage", "prompt", {}, attempt=None)
    assert meter.data["tokens"] == 13


def test_unknown_started_operation_cannot_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meter = host.Meter(tmp_path, cwd=tmp_path, model="model", effort="medium")
    (tmp_path / "uncertain").mkdir()
    monkeypatch.setattr(host, "run_worker", lambda *_a, **_k: pytest.fail("must not repeat"))
    with pytest.raises(RuntimeError, match="cannot be replayed"):
        meter.call("uncertain", "prompt", {}, attempt=None)
    assert meter.data["usage_complete"] is False
    assert meter.call("next", "prompt", {}, attempt=None) is None
