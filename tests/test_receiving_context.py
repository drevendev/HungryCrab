"""Explicit receiving targets, post-init freshness and compatible multi-prey contexts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fixture_builder import git
from test_exploration import PLAN, STREAMING, pair
from test_multifeed import menu

from hungry_crab.errors import CrabError
from hungry_crab.exploration import initialize, register, report
from hungry_crab.multifeed import merge_menus


@pytest.mark.parametrize("change", ["content", "commit", "branch", "untracked"])
def test_registration_rechecks_receiving_identity(tmp_path: Path, change: str) -> None:
    directory, donor, maw, finding = pair(tmp_path)
    paths = [
        directory / "snapshot.json",
        directory.parent.parent / "menu.json",
        directory.parent.parent / "menu.md",
    ]
    before = [path.read_bytes() for path in paths]
    if change in {"content", "commit"}:
        (maw / "app.py").write_text(STREAMING, encoding="utf-8")
        if change == "commit":
            git(maw, "add", "app.py")
            git(maw, "commit", "-qm", "test: receiving implementation")
    elif change == "branch":
        git(maw, "checkout", "-qb", "selected-work")
    else:
        (maw / "new.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(CrabError, match="receiving"):
        register(directory, finding, source_repo=donor)
    assert [path.read_bytes() for path in paths] == before
    assert not list(directory.glob("event-*.json"))
    assert report(directory)["receiving_context"]["sha"] == finding["maw_evidence"][0]["sha"]


def test_explicit_receiving_target_must_match_observed_checkout(tmp_path: Path) -> None:
    directory, _, maw, _ = pair(tmp_path)
    plan = {**PLAN, "receiving_context": {"sha": "f" * 40}}
    with pytest.raises(CrabError, match="selected receiving sha"):
        initialize(directory.parent.parent, plan, maw=maw)
    snapshot = json.loads((directory / "snapshot.json").read_text())
    context = snapshot["receiving_context"]
    plan["receiving_context"] = {key: context[key] for key in ("sha", "selected_ref", "root")}
    assert initialize(directory.parent.parent, plan, maw=maw).is_dir()


def test_init_cannot_relabel_a_stale_comparison_with_a_new_receiving_branch(tmp_path: Path) -> None:
    directory, _, maw, _ = pair(tmp_path)
    git(maw, "checkout", "-qb", "another-target")
    with pytest.raises(CrabError, match="receiving context changed after comparison"):
        initialize(directory.parent.parent, PLAN, maw=maw)
    assert report(directory)["receiving_context"]["selected_ref"] != "another-target"


@pytest.mark.parametrize("field", ["worktree", "policy", "scoring", "ledger", "context"])
def test_merge_refuses_mixed_receiving_inputs(field: str) -> None:
    sources = {"a": menu("x/a"), "b": menu("x/b")}
    for source in sources.values():
        source.update(
            post_comparison_worktree="clean",
            analysis_inputs={"policy": {}, "ledger": {"entries": []}},
        )
    other = sources["b"]
    if field == "worktree":
        other["post_comparison_worktree"] = "dirty"
    elif field == "scoring":
        other["scoring"] = {"categories": {"ci": 0.01}}
    elif field == "context":
        other["receiving_context"] = {"sha": "m", "selected_ref": "work"}
    else:
        other["analysis_inputs"][field] = {"changed": True}
    with pytest.raises(CrabError, match="snapshot"):
        merge_menus(sources)


def test_read_timestamps_do_not_make_equivalent_contexts_different() -> None:
    sources = {"a": menu("x/a"), "b": menu("x/b")}
    for key, source in sources.items():
        source["analysis_inputs"] = {"ledger": {"entries": [], "updated_at": key}}
    before = copy.deepcopy(sources)
    merged = merge_menus(sources)
    assert merged["receiving_context_status"] == "legacy-unknown"
    assert len(merged["candidates"][0]["trace"]["sources"]) == 2
    assert sources == before


def test_explicit_unknown_receiving_state_cannot_certify_a_multi_prey_merge() -> None:
    sources = {"a": menu("x/a"), "b": menu("x/b")}
    for source in sources.values():
        source["receiving_context"] = {"worktree": "unknown"}
    with pytest.raises(CrabError, match="snapshot is unknown"):
        merge_menus(sources)


def test_bounded_walkthrough_records_selected_branch_constraints_and_deferred_work(
    tmp_path: Path,
) -> None:
    from test_discovery_inputs import repository

    from hungry_crab.cache import Target
    from hungry_crab.compare import compare_for_maw
    from hungry_crab.digest import DigestOptions
    from hungry_crab.exploration import record

    donor = repository(
        tmp_path / "donor", {"windows.py": "import win32api\n", "reader.py": STREAMING}
    )
    maw = repository(
        tmp_path / "maw",
        {
            "app.py": "def consume(stream):\n    return stream.read()\n",
            "platforms.py": "PLATFORMS = ('linux', 'darwin')\nDEPENDENCIES = ()\n",
            ".crab.yml": "ledger: none\n",
        },
    )
    old_sha = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "checkout", "-qb", "selected-work")
    (maw / "app.py").write_text(STREAMING, encoding="utf-8")
    git(maw, "add", "app.py")
    git(maw, "commit", "-qm", "test: selected newer receiving behavior")
    comparison = compare_for_maw(
        Target(path=donor),
        maw,
        digest_options=DigestOptions(cache_root=tmp_path / "cache"),
        record_ledger=False,
    )[0]
    assert comparison.meal_dir is not None
    context = comparison.menu["receiving_context"]
    assert context["sha"] != old_sha and context["selected_ref"] == "selected-work"
    plan = {
        **PLAN,
        "constraints": "macOS/Linux; stdlib only; no publication",
        "facets": ["behavior", "performance-streaming"],
        "receiving_context": {"sha": context["sha"], "selected_ref": "selected-work"},
    }
    directory = initialize(comparison.meal_dir, plan, maw=maw)

    def entry(hypothesis: str, source: str, receiving: str) -> dict[str, object]:
        return {
            "author": "synthetic analyst",
            "tool": "authored walkthrough",
            "attempt": "bounded",
            "reason": "receiving evidence inspected under frozen constraints",
            "hypothesis": hypothesis,
            "source_evidence": [
                {"sha": comparison.menu["prey"]["sha"], "path": source, "start": 1, "end": 1}
            ],
            "maw_evidence": [{"sha": context["sha"], "path": receiving, "start": 1, "end": 1}],
        }

    incompatible = entry("Windows helper", "windows.py", "platforms.py")
    record(directory, {**incompatible, "kind": "facet", "facet": "behavior", "state": "examined"})
    record(
        directory,
        {
            **incompatible,
            "kind": "decision",
            "disposition": "not-useful",
            "reviewed": True,
            "reason": "Windows-only runtime violates receiving platforms and stdlib constraint",
        },
    )
    bounded = entry("bounded reads", "reader.py", "app.py")
    record(
        directory,
        {**bounded, "kind": "facet", "facet": "performance-streaming", "state": "examined"},
    )
    record(
        directory,
        {
            **bounded,
            "kind": "decision",
            "disposition": "NO_GAP",
            "reviewed": True,
            "examined_scope": "selected app.consume already reads bounded blocks",
        },
    )
    record(
        directory,
        {
            **bounded,
            "kind": "decision",
            "hypothesis": "remaining recovery question",
            "disposition": "budget-deferred",
            "reviewed": False,
        },
    )
    result = report(directory)
    assert len(result["examined_hypotheses"]) == 2
    assert result["unresolved_hypotheses"] == ["remaining recovery question"]
    assert not result["exploration_complete"]
