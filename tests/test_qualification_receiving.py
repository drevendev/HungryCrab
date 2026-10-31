"""Receiving setup can select an immutable owned branch without running its code."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from qualification_receiving import selected_pin
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
