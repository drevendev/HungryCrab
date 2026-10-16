from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def test_action_resolves_local_paths_in_the_caller_and_preserves_literal_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "feeder_action.py"
    spec = importlib.util.spec_from_file_location("feeder_action_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workspace = tmp_path / "maw with spaces"
    prey = workspace / "donor;literal"
    prey.mkdir(parents=True)
    outputs = tmp_path / "outputs"
    summary = tmp_path / "summary"
    env = {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
        "CRAB_PREY": "donor;literal",
        "CRAB_MAW": ".",
        "CRAB_SINCE": "all",
        "CRAB_ISSUES": "0",
        "CRAB_TOP": "30",
        "CRAB_MAX_REPO_KB": "307200",
        "CRAB_DEPTH": "normal",
        "CRAB_SHALLOW": "true",
        "CRAB_WIKI": "false",
        "CRAB_ALLOW_LOSS": "false",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls: list[list[str]] = []

    def fake_main(args: list[str]) -> int:
        calls.append(args)
        print(json.dumps({"out_dir": str(tmp_path / "bundle"), "counts": {"total": 2}}))
        return 0

    monkeypatch.setattr(module, "main", fake_main)
    assert module.run() == 0
    assert calls[0][calls[0].index("eat") + 1] == str(prey.resolve())
    assert calls[0][calls[0].index("--maw") + 1] == str(workspace)
    assert "candidate-count=2" in outputs.read_text()
    assert "2 candidates" in summary.read_text()
