"""Behavior-led discovery must work independently of structural missing traits."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git as fixture_git
from test_hunt import Search

import hungry_crab.hunt as hunt_module
from hungry_crab.analysis_context import receiving_context
from hungry_crab.cli import main
from hungry_crab.compare.candidates import Side
from hungry_crab.compare.rules import TRAIT_RULES
from hungry_crab.digest import DigestResult, worktree_fingerprint
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.fetch.git import GitRunner
from hungry_crab.hunt import discover, hunt
from hungry_crab.hunt_config import HuntSettings
from hungry_crab.hunt_plan import PLAN_SCHEMA, read_plan
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig


@pytest.fixture
def context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    value = {
        "root": str(tmp_path),
        "sha": "a" * 40,
        "selected_ref": "main",
        "worktree": "tree",
        "policy": "policy",
        "ledger": "ledger",
    }
    monkeypatch.setattr("hungry_crab.hunt.receiving_context", lambda *_: value)
    monkeypatch.setattr("hungry_crab.hunt._require_current_context", lambda *_: None)
    return value


def plan(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": PLAN_SCHEMA,
        "receiving_context": copy.deepcopy(context),
        "hypotheses": [
            {
                "id": "strict-jsonl",
                "facet": "input-validation",
                "behavior": "reject duplicate JSONL keys",
                "queries": ["strict JSONL", "duplicate keys"],
                "priority": 0.9,
            }
        ],
    }


def test_complete_traits_do_not_suppress_a_behavior_search(
    context: dict[str, Any], tmp_path: Path
) -> None:
    side = Side("maw", "a" * 40, None, tmp_path, traits={rule.trait: True for rule in TRAIT_RULES})
    client = Search([])
    result = discover(
        side,
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(request_budget=1),
        plan=plan(context),
        client=client,
    )  # type: ignore[arg-type]
    assert result["gaps"] == [] and len(client.calls) == 1
    assert result["queries"] == ["strict JSONL in:readme archived:false fork:false stars:>=20"]
    assert result["query_plan"][1]["state"] == "budget-deferred"
    assert result["requests"] == {"budget": 1, "executed": 1, "planned_unique": 2}


@pytest.mark.parametrize("key", ["root", "sha", "selected_ref", "worktree", "policy", "ledger"])
def test_stale_receiving_context_fails_before_search(
    context: dict[str, Any], tmp_path: Path, key: str
) -> None:
    selected = plan(context)
    selected["receiving_context"][key] = "changed"
    client = Search([])
    with pytest.raises(UsageError, match="receiving context"):
        discover(
            Side("maw", "a", None, tmp_path),
            MawConfig(tmp_path),
            Ledger(None),
            HuntSettings(),
            plan=selected,
            client=client,
        )  # type: ignore[arg-type]
    assert client.calls == []


def test_mixed_first_party_runtimes_and_explicit_neutral_pool(
    context: dict[str, Any], tmp_path: Path
) -> None:
    side = Side(
        "maw",
        "a",
        None,
        tmp_path,
        traits={
            "ecosystems": ["python"],
            "runtime_ecosystems": ["npm"],
            "runtime_evidence": {"npm": ["web/main.js"]},
        },
    )
    result = discover(
        side,
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(request_budget=3, language_neutral=True),
        plan=plan(context),
        client=Search([]),
    )  # type: ignore[arg-type]
    assert {row["language"] for row in result["query_plan"] if row["state"] == "executed"} == {
        "Python",
        "JavaScript",
        None,
    }
    assert not result["deferred_languages"]
    limited = discover(
        side,
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(request_budget=1),
        plan=plan(context),
        client=Search([]),
    )  # type: ignore[arg-type]
    assert limited["deferred_languages"] == ["Python"]


def test_facets_precede_synonyms_and_duplicate_queries_do_not_spend_requests(
    context: dict[str, Any], tmp_path: Path
) -> None:
    selected = plan(context)
    selected["hypotheses"].append(
        {
            "id": "atomic-output",
            "facet": "recovery",
            "behavior": "preserve old output on failure",
            "queries": ["atomic replace", "atomic replace"],
            "priority": 0.8,
        }
    )
    result = discover(
        Side("maw", "a", None, tmp_path),
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(request_budget=2),
        plan=selected,
        client=Search([]),
    )  # type: ignore[arg-type]
    assert [row["hypothesis"] for row in result["query_plan"] if row["state"] == "executed"] == [
        "strict-jsonl",
        "atomic-output",
    ]
    assert result["requests"]["planned_unique"] == 3


@pytest.mark.parametrize("budget", [True, 0, -1, 17, "8"])
def test_invalid_request_budget(budget: object) -> None:
    with pytest.raises(UsageError):
        HuntSettings.load({"request_budget": budget})


def test_duplicate_plan_keys_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text('{"schema":"one","schema":"two"}')
    with pytest.raises(UsageError, match="duplicate"):
        read_plan(path)


@pytest.mark.parametrize("priority", [True, float("inf"), float("nan"), 10**1000, -1])
def test_invalid_priorities_fail_before_provider_access(
    context: dict[str, Any], tmp_path: Path, priority: object
) -> None:
    selected = plan(context)
    selected["hypotheses"][0]["priority"] = priority
    client = Search([])
    with pytest.raises(UsageError, match="priority"):
        discover(
            Side("maw", "a", None, tmp_path),
            MawConfig(tmp_path),
            Ledger(None),
            HuntSettings(),
            plan=selected,
            client=client,
        )  # type: ignore[arg-type]
    assert not client.calls


@pytest.mark.parametrize("change", ["tracked-file", "same-branch-commit", "selected-ref"])
def test_hunt_rechecks_live_checkout_after_digest_before_provider(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    maw = tmp_path / "maw"
    shutil.copytree(npm_app, maw)
    cache = tmp_path / "cache"
    original = hunt(maw, cache_root=cache, client=Search([]))  # type: ignore[arg-type]
    selected = plan(original["receiving_context"])
    run_digest = hunt_module.run_digest

    def change_after_digest(*args: Any, **kwargs: Any) -> DigestResult:
        result = run_digest(*args, **kwargs)
        if change == "tracked-file":
            (maw / "src" / "main.ts").write_text("export const changed = true;\n")
        elif change == "same-branch-commit":
            fixture_git(maw, "commit", "--allow-empty", "-m", "test: receiving commit changed")
        else:
            fixture_git(maw, "checkout", "-b", "changed")
        return result

    monkeypatch.setattr(hunt_module, "run_digest", change_after_digest)
    client = Search([])
    with pytest.raises(UsageError, match="receiving context"):
        hunt(maw, cache_root=cache, plan=selected, client=client)  # type: ignore[arg-type]
    assert not client.calls


@pytest.mark.parametrize("changed", ["policy", "ledger"])
def test_retained_side_rechecks_owner_inputs_before_search(tmp_path: Path, changed: str) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    (maw / ".gitignore").write_text(".crab.yml\n")
    (maw / "main.py").write_text("print('synthetic receiving code; never executed')\n")
    fixture_git(maw, "init", "-b", "main")
    fixture_git(maw, "add", ".gitignore", "main.py")
    fixture_git(maw, "commit", "-m", "test: receiving identity")
    path = maw / ".crab.yml"
    path.write_text("hunt: {request_budget: 4}\n")
    config = MawConfig.load(maw)
    ledger = Ledger.load(tmp_path / "ledger.json", maw="maw")
    ledger.save()
    git = GitRunner(maw)
    manifest = {"prey": {"sha": git.head_sha(), "worktree": "clean"}}
    context = receiving_context(maw, manifest, config.raw, ledger.to_dict())
    selected = plan(context)
    side = Side("maw", git.head_sha(), None, maw, manifest=manifest)
    if changed == "policy":
        path.write_text("hunt: {request_budget: 5}\n")
    else:
        updated = Ledger.load(ledger.path, maw="maw")
        updated.extra["review_marker"] = "changed"
        updated.save()
    assert worktree_fingerprint(git, maw) == "clean"
    client = Search([])
    with pytest.raises(UsageError, match="receiving context"):
        discover(side, config, ledger, HuntSettings(), plan=selected, client=client)  # type: ignore[arg-type]
    assert not client.calls


def test_provider_failure_retains_attempt_and_defers_remaining_requests(
    context: dict[str, Any], tmp_path: Path
) -> None:
    class Unavailable(Search):
        def get(self, path: str) -> dict[str, Any]:
            self.calls.append(path)
            raise CrabError("synthetic provider outage")

    client = Unavailable([])
    result = discover(
        Side("maw", "a", None, tmp_path),
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(),
        plan=plan(context),
        client=client,
    )  # type: ignore[arg-type]
    assert len(client.calls) == result["requests"]["executed"] == 1
    assert result["search_status"] == "provider-failed-partial" and result["truncated"]
    assert [row["state"] for row in result["query_plan"]] == ["failed", "provider-deferred"]


def test_cli_prints_partial_search_report_but_returns_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "hungry_crab.cli.hunt",
        lambda *_args, **_kwargs: {"search_status": "provider-failed-partial", "candidates": []},
    )
    assert main(["hunt", "--for", str(tmp_path), "--json"]) == 1
    assert "provider-failed-partial" in capsys.readouterr().out


def test_real_hunt_plan_cannot_reuse_a_changed_receiving_checkout(
    npm_app: Path, tmp_path: Path
) -> None:
    maw = tmp_path / "maw"
    shutil.copytree(npm_app, maw)
    cache = tmp_path / "cache"
    original = hunt(maw, cache_root=cache, client=Search([]))  # type: ignore[arg-type]
    selected = plan(original["receiving_context"])
    unchanged = hunt(maw, cache_root=cache, plan=selected, client=Search([]))  # type: ignore[arg-type]
    assert unchanged["queries"]
    (maw / "src" / "main.ts").write_text("export const changed = true;\n")
    client = Search([])
    with pytest.raises(UsageError, match="receiving context"):
        hunt(maw, cache_root=cache, plan=selected, client=client)  # type: ignore[arg-type]
    assert not client.calls
