"""Pending code is implementation evidence; unknown scope is not global absence."""

from __future__ import annotations

from pathlib import Path

from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab.novelty import implementation_scope


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
