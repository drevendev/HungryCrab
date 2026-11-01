"""Pending code is implementation evidence; unknown scope is not global absence."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab.errors import CrabError
from hungry_crab.novelty import implementation_scope


def test_declaration_projection_retains_decorators_and_helper_uncertainty(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving",
        {"app.py": "def helper(fn): return fn\n@helper\ndef flow():\n    return 1\n"},
    )
    before = git(maw, "rev-parse", "HEAD").strip()
    (maw / "app.py").write_text(
        "def helper(fn): return None\n@helper\ndef flow():\n    return 1\n",
        encoding="utf-8",
        newline="\n",
    )
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: changed helper behind identical declaration")
    plan = {
        "prefixes": ["app.py"],
        "default_sha": before,
        "symbols": [{"path": "app.py", "qualified_name": "flow"}],
        "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
    }
    result = implementation_scope(maw, plan)
    selected, default = result["targets"]
    declaration = selected["declarations"][0]
    assert declaration["status"] == "declaration-available"
    assert declaration["start"] == 2 and declaration["end"] == 4
    assert declaration["content_sha256"] == default["declarations"][0]["content_sha256"]
    assert selected["files"] != default["files"]
    assert "never runtime equivalence" in result["declaration_scope"]
    assert result["semantic_novelty"] == "requires-agent-comparison"
    assert len(result["sources"]) == 2
    (maw / "app.py").write_text(
        "@other\ndef flow():\n    return 1\n", encoding="utf-8", newline="\n"
    )
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: different decorator")
    changed = implementation_scope(maw, plan)
    assert (
        changed["targets"][0]["declarations"][0]["content_sha256"] != declaration["content_sha256"]
    )


def test_declaration_absence_ambiguity_and_scope_are_explicit(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving",
        {"app.py": "def flow(): return 1\ndef flow(): return 2\n"},
    )
    pin = git(maw, "rev-parse", "HEAD").strip()
    plan = {
        "prefixes": ["app.py"],
        "default_sha": pin,
        "symbols": [{"path": "app.py", "qualified_name": n} for n in ("flow", "missing")],
        "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
    }
    result = implementation_scope(maw, plan)
    assert [d["status"] for d in result["targets"][0]["declarations"]] == [
        "declaration-ambiguous",
        "declaration-absent",
    ]
    plan["symbols"] = [{"path": "elsewhere.py", "qualified_name": "flow"}]
    with pytest.raises(CrabError, match="in scope"):
        implementation_scope(maw, plan)


def test_scope_retains_selected_default_and_pending_without_conflating_roles(
    tmp_path: Path,
) -> None:
    maw = repository(
        tmp_path / "receiving", {"app.py": "def consume(stream):\n    return stream.read()\n"}
    )
    before = git(maw, "rev-parse", "HEAD").strip()
    (maw / "app.py").write_text(
        "def consume(stream):\n    return stream.read(4)\n", encoding="utf-8"
    )
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: pending implementation")
    after = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "update-ref", "refs/crab/qualification/pr-1", after)
    git(maw, "checkout", "--detach", before)
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": before,
            "pending": [{"sha": after, "ref": "refs/crab/qualification/pr-1"}],
            "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
        },
    )
    assert result["implementation_scope_complete"] and len(result["sources"]) == 2
    assert [r["role"] for r in result["targets"]] == [
        "receiving-selected",
        "receiving-default",
        "receiving-pending",
    ]
    assert not result["global_historical_absence_proven"]
    assert result["semantic_novelty"] == "requires-agent-comparison"
    assert not result["donor_executed"] and result["provider_requests"] == 0


def test_unacquired_pending_pin_stays_unknown_without_implicit_fetch(tmp_path: Path) -> None:
    maw = repository(tmp_path / "receiving", {"app.py": "def correct():\n    return True\n"})
    pin = git(maw, "rev-parse", "HEAD").strip()
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": pin,
            "pending": [{"sha": "f" * 40, "ref": "refs/crab/qualification/pr-2"}],
            "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
        },
    )
    assert not result["implementation_scope_complete"]
    assert result["omissions"][0]["reason"] == "immutable-head-unavailable"


def test_unrelated_nonregular_tree_does_not_poison_checked_module_scope(tmp_path: Path) -> None:
    maw = repository(tmp_path / "receiving", {"app.py": "def correct():\n    return True\n"})
    first = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "update-index", "--add", "--cacheinfo", "160000," + first + ",vendor/component")
    git(maw, "commit", "-m", "test: unrelated submodule metadata")
    pin = git(maw, "rev-parse", "HEAD").strip()
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": pin,
            "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
        },
    )
    assert result["implementation_scope_complete"]
    assert len(result["sources"]) == 1 and not result["omissions"]
