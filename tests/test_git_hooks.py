"""Hook isolation is checked without executing repository hooks or provider effects (#440)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from hungry_crab.errors import ExternalCommandError
from hungry_crab.fetch.acquisition import CURRENT, AcquisitionBudget
from hungry_crab.fetch.bounded import ResponseTooLargeError
from hungry_crab.fetch.git import GitRunner


class StubBudget:
    def __init__(
        self, volume: Path, execute: Callable[..., subprocess.CompletedProcess[bytes]]
    ) -> None:
        self.volume = volume
        self.execute = execute
        self.calls = 0

    def git_env(self, base: dict[str, str]) -> dict[str, str]:
        return {
            **base,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "scoped-inherited-hooks",
        }

    def run_git(
        self, command: list[str], where: Path, env: dict[str, str], limit: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls += 1
        return self.execute(command, cwd=where, env=env, timeout=limit)

    def check(self) -> None:
        pass

    def disk_exhausted(self) -> bool:
        return False


@pytest.mark.parametrize("command", ["fetch", "push", "update-ref"])
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("outcome", ["success", "nonzero", "timeout", "os-error", "too-large"])
def test_hook_override_wins_and_lives_only_during_process_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    scoped: bool,
    outcome: str,
) -> None:
    runner = GitRunner(tmp_path)
    monkeypatch.setattr(runner, "_filter_drivers", lambda *_: [])
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "inherited-hooks")
    volume = tmp_path / "quota-volume"
    volume.mkdir()
    observed: list[Path] = []

    def execute(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        index = argv.index(command)
        assert argv[index - 2] == "-c"
        assert argv[index - 1].startswith("core.hooksPath=")
        assert argv.index("core.hooksPath=caller-hooks") < index - 2
        hooks = Path(argv[index - 1].split("=", 1)[1])
        assert hooks.is_dir() and list(hooks.iterdir()) == []
        assert kwargs["env"]["GIT_CONFIG_KEY_0"] == "core.hooksPath"
        if scoped:
            assert hooks.parent == volume
            assert kwargs["env"]["GIT_CONFIG_VALUE_0"] == "scoped-inherited-hooks"
        else:
            assert kwargs["env"]["GIT_CONFIG_VALUE_0"] == "inherited-hooks"
        observed.append(hooks)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        if outcome == "os-error":
            raise OSError("synthetic process failure")
        if outcome == "too-large":
            raise ResponseTooLargeError("synthetic output limit")
        return subprocess.CompletedProcess(argv, int(outcome == "nonzero"), b"ok", b"failure")

    budget = StubBudget(volume, execute)
    monkeypatch.setattr("hungry_crab.fetch.git.subprocess.run", execute)
    token = CURRENT.set(cast(AcquisitionBudget, budget) if scoped else None)
    try:
        args = ("-c", "core.hooksPath=caller-hooks", "-C", str(tmp_path), command, "fixture")
        if outcome == "success":
            assert runner.run(*args) == "ok"
        else:
            with pytest.raises(ExternalCommandError):
                runner.run(*args)
    finally:
        CURRENT.reset(token)
    assert len(observed) == 1 and not observed[0].exists()
    assert budget.calls == int(scoped)
    assert list(volume.iterdir()) == []


def test_read_only_git_does_not_allocate_an_effect_hook_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = GitRunner(tmp_path)
    monkeypatch.setattr(runner, "_filter_drivers", lambda *_: [])
    monkeypatch.setattr(
        "hungry_crab.fetch.git.tempfile.TemporaryDirectory",
        lambda **_: pytest.fail("read-only command allocated effect hooks"),
    )

    def execute(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert not any(arg.startswith("core.hooksPath=") for arg in argv)
        return subprocess.CompletedProcess(argv, 0, b"fixture", b"")

    monkeypatch.setattr("hungry_crab.fetch.git.subprocess.run", execute)
    assert runner.run("rev-parse", "HEAD") == "fixture"
