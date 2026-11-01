"""Receiving setup can select an immutable owned branch without running its code."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from qualification_receiving import pending_pins, selected_pin
from test_discovery_inputs import repository

from hungry_crab.errors import ExternalCommandError


def test_qualified_branch_pin_is_not_checked_out_and_default_drift_is_refused(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path / "owned", {"app.py": "raise RuntimeError('never execute')\n"})
    default = git(repo, "rev-parse", "HEAD").strip()
    (repo / "next.txt").write_text("owned branch fixture\n", encoding="utf-8")
    git(repo, "add", "next.txt")
    git(repo, "commit", "-m", "fixture: pending implementation")
    pending = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/remotes/origin/owned/release", pending)
    git(repo, "reset", "--hard", default)
    plan = {"sha": default, "target_ref": "owned/release", "target_sha": pending}
    assert selected_pin(repo, plan, default) == (pending, "owned/release")
    assert git(repo, "rev-parse", "HEAD").strip() == default
    assert not (repo / "next.txt").exists()
    assert selected_pin(repo, {"sha": default}, default) == (default, None)
    with pytest.raises(ValueError, match="default changed"):
        selected_pin(repo, plan, "0" * 40)
    with pytest.raises(ExternalCommandError):
        selected_pin(repo, {**plan, "target_ref": "missing"}, default)
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    unrelated = git(repo, "commit-tree", tree, "-m", "fixture: unrelated owner branch").strip()
    git(repo, "update-ref", "refs/remotes/origin/unrelated", unrelated)
    with pytest.raises(ExternalCommandError):
        selected_pin(repo, {**plan, "target_ref": "unrelated"}, default)


@pytest.mark.parametrize("ref,pin", [("-unsafe", "a" * 40), ("ok", "HEAD"), (None, "a" * 40)])
def test_partial_or_unsafe_target_never_starts_git(
    tmp_path: Path, ref: object, pin: object
) -> None:
    with pytest.raises(ValueError, match="explicit owned branch"):
        selected_pin(
            tmp_path / "not-opened", {"sha": "b", "target_ref": ref, "target_sha": pin}, "b"
        )


def test_canonical_receiving_pull_ref_requires_its_exact_declared_head(tmp_path: Path) -> None:
    repo = repository(tmp_path / "owned", {"app.py": "raise RuntimeError('never execute')\n"})
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/crab/qualification/pr-75", head)
    definition = {"sha": head, "target_pull_request": 75, "target_sha": head}
    assert selected_pin(repo, definition, head) == (head, "refs/pull/75/head")
    (repo / "next.txt").write_text("new pending head\n", encoding="utf-8")
    git(repo, "add", "next.txt")
    git(repo, "commit", "-m", "fixture: advance pull ref")
    changed = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/crab/qualification/pr-75", changed)
    with pytest.raises(ValueError, match="PR head changed"):
        selected_pin(repo, definition, head)


@pytest.mark.parametrize("value", [True, 0, "75", -1])
def test_invalid_pr_target_never_starts_git(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="explicit owned branch or PR"):
        selected_pin(
            tmp_path, {"sha": "b", "target_pull_request": value, "target_sha": "a" * 40}, "b"
        )


@pytest.mark.parametrize(
    "pending",
    [
        None,
        "75",
        [{"number": True, "sha": "a" * 40}],
        [{"number": 75, "sha": "HEAD"}],
        [{"number": 75, "sha": "a" * 40}] * 2,
    ],
)
def test_invalid_pending_collection_fails_before_acquisition(pending: object) -> None:
    with pytest.raises(ValueError, match="pending"):
        pending_pins({"pending": pending})


def test_pending_collection_retains_exact_canonical_bindings() -> None:
    rows = [{"number": 75, "sha": "a" * 40}, {"number": 81, "sha": "a" * 40}]
    assert pending_pins({"pending": rows}) == rows
    assert pending_pins({}) == []
