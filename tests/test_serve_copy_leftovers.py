"""A COPY publication must tell the maintainer which inputs remain in the checkout (#233)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_attribution import MENU, NOW, SHA, WORKFLOW, FakePrey, _card, _payload, _prey
from test_pr_effects import MAW_SLUG, FakeGh, _git_repo

from hungry_crab.cache import Slug
from hungry_crab.cli import print_serve_report
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.pr_publication import nutrient_branch_name
from hungry_crab.serve import GhIssueClient, ServeOptions, serve


class CopyClient(GhIssueClient):
    def __init__(self, nutrient_id: str, *, existing: bool) -> None:
        self.nutrient_id = nutrient_id
        self.existing = existing
        self.fake_gh = FakeGh()

    def identity(self) -> str:
        return "test-user"

    def list_marked_prs(self, slug: Slug) -> dict[str, dict[str, Any]]:
        assert slug == MAW_SLUG
        return (
            {self.nutrient_id: {"url": "https://github.com/example/maw/pull/9"}}
            if self.existing
            else {}
        )

    def run_gh(self, *args: str) -> str:
        return self.fake_gh(*args)


@pytest.mark.parametrize("existing", [False, True])
def test_copy_serving_reports_all_remaining_inputs_in_text_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], existing: bool
) -> None:
    maw, _, git, remote_git = _git_repo(tmp_path)
    tracked = ".github/workflows/ci.yml"
    new = "docs/cache notes.md"
    workflow = maw / tracked
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: original\n", encoding="utf-8")
    git.run("add", tracked)
    git.run("commit", "-m", "chore: seed existing workflow")
    git.run("push", "origin", "master")
    workflow.write_text(WORKFLOW, encoding="utf-8")
    notes = maw / new
    notes.parent.mkdir()
    notes.write_text("Cache notes.\n", encoding="utf-8")
    unrelated = maw / "mine.txt"
    unrelated.write_text("maintainer work\n", encoding="utf-8")

    card = _card()
    meal = tmp_path / "meal"
    meal.mkdir()
    (meal / "menu.json").write_text(
        json.dumps({**MENU, "candidates": [card.to_dict()]}), encoding="utf-8"
    )
    payload = _payload(
        taken=[
            {"maw_path": tracked, "prey_path": ".github/workflows/test.yml", "verbatim": False},
            {"maw_path": new, "prey_path": "docs/cache.md", "verbatim": False},
        ]
    )
    prey = FakePrey({**_prey().files, (SHA, "docs/cache.md"): "Original cache notes.\n"})
    client = CopyClient(card.id, existing=existing)
    before = git.run("status", "--porcelain")
    report = serve(
        meal,
        maw,
        ServeOptions(ids=[card.id], mode="pr-branch"),
        config=MawConfig(root=maw),
        ledger=Ledger(None),
        client=client,
        now=NOW,
        slug_lookup=lambda _: MAW_SLUG,
        receipt_payloads={card.id: payload},
        source_reader=prey,
    )

    # Serving preserves both adapted/new COPY inputs and unrelated maintainer work.
    assert git.run("status", "--porcelain") == before
    assert workflow.read_text(encoding="utf-8") == WORKFLOW
    assert notes.read_text(encoding="utf-8") == "Cache notes.\n"
    assert unrelated.read_text(encoding="utf-8") == "maintainer work\n"
    assert git.current_branch() == "master"
    assert not (maw / ".crab/attributions.json").exists()
    assert not (maw / "THIRD_PARTY_NOTICES.md").exists()

    section = "skipped" if existing else "served"
    item = json.loads(json.dumps(report.to_dict()))[section][0]
    branch = nutrient_branch_name(card.id)
    assert item["working_tree_paths"] == [tracked, new]
    assert item["branch"] == branch
    print_serve_report(report)
    output = capsys.readouterr().out
    assert "WARNING: COPY input paths remain in your working tree" in output
    assert branch in output and tracked in output and new in output
    assert "git stash push --include-untracked" in output

    if existing:
        assert client.fake_gh.calls == [], "reconciliation must not create another branch or PR"
    else:
        assert remote_git.run("show", f"refs/heads/{branch}:{tracked}") == WORKFLOW
        assert remote_git.run("show", f"refs/heads/{branch}:{new}") == "Cache notes.\n"
        notice = remote_git.run("show", f"refs/heads/{branch}:THIRD_PARTY_NOTICES.md")
        assert "Prey Owner" in notice
        assert remote_git.run("show", f"refs/heads/{branch}:.crab/attributions.json")

    # The suggested command preserves every edit, including the unrelated one, and cleans up.
    git.run("stash", "push", "--include-untracked")
    assert git.run("status", "--porcelain") == ""
    git.run("stash", "pop")
    assert git.run("status", "--porcelain") == before
