"""Exercise the actual offline Feeder cohort against synthetic Git fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import copy_repo

from hungry_crab.cache import Target
from hungry_crab.errors import CrabError
from hungry_crab.feeder import EatOptions, EatResult, eat
from hungry_crab.fetch.git import GitRunner
from hungry_crab.multifeed import eat_many, load_multi


def _options(tmp_path: Path) -> EatOptions:
    # Local fixtures and --no-wiki make this a provider-free replay, not a hunt.
    return EatOptions(
        out=tmp_path / "bundle",
        cache_root=tmp_path / "cache",
        wiki=False,
        issues=0,
        discussions=0,
        reviews=0,
        runs=0,
    )


def test_real_offline_multifeed_replays_two_distinct_sources(
    npm_app: Path, dotnet_lib: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    maw = copy_repo(pyproject_cli, tmp_path / "maw", with_git=True)
    before = GitRunner(maw).try_run("status", "--porcelain")
    result = eat_many([Target(path=npm_app), Target(path=dotnet_lib)], maw, _options(tmp_path))
    aggregate, paths, targets = load_multi(result.out_dir)
    assert result.manifest["side_effects"] == {"issues_created": 0, "ledger_written": False}
    assert aggregate["receiving_context_status"] == "observed"
    assert aggregate["receiving_context"]["sha"] == GitRunner(maw).head_sha()
    assert len(paths) == len(targets) == 2
    assert {target.path for target in targets.values()} == {npm_app.resolve(), dotnet_lib.resolve()}
    for path in paths.values():
        source = json.loads((path / "menu.json").read_text(encoding="utf-8"))
        assert source["analysis_inputs"]["issue_lookup_status"] == "disabled"
    assert GitRunner(maw).try_run("status", "--porcelain") == before
    assert not (maw / ".crab" / "ledger.json").exists()


@pytest.mark.parametrize("change", ["tracked_bytes", "policy"])
def test_real_offline_multifeed_rejects_receiving_drift_between_sources(
    npm_app: Path,
    dotnet_lib: Path,
    pyproject_cli: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    maw = copy_repo(pyproject_cli, tmp_path / "maw", with_git=True)
    calls = 0

    def eat_then_change(
        prey: Target,
        receiving: Path,
        options: EatOptions,
        *,
        log: Callable[[str], None],
    ) -> EatResult:
        nonlocal calls
        result = eat(prey, receiving, options, log=log)
        calls += 1
        if calls == 1:
            if change == "tracked_bytes":
                path = receiving / "pyproject.toml"
                path.write_text(
                    path.read_text(encoding="utf-8") + "\n# changed receiving state\n",
                    encoding="utf-8",
                )
            else:
                (receiving / ".crab.yml").write_text(
                    "scoring:\n  categories:\n    deps: 0.01\n", encoding="utf-8"
                )
        return result

    monkeypatch.setattr("hungry_crab.multifeed.eat", eat_then_change)
    output = tmp_path / "bundle"
    with pytest.raises(CrabError, match="same maw snapshot and policy"):
        eat_many([Target(path=npm_app), Target(path=dotnet_lib)], maw, _options(tmp_path))
    assert calls == 2
    assert not output.exists()
