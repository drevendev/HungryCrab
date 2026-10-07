from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_loop import URL, Provider, artifact, force_phase
from test_loop import loop as _loop_fixture
from test_serve import FakeIssues

from hungry_crab.errors import CrabError
from hungry_crab.loop import Loop, atomic_json
from hungry_crab.loop_work import serve_phase
from hungry_crab.nutrients import Candidate
from hungry_crab.pr_publication import (
    PreparedPullRequest,
    PullRequestPublication,
    nutrient_spec_path,
)

loop = _loop_fixture


@pytest.mark.parametrize("protected", [False, True])
def test_served_issue_becomes_one_licensed_grow_pr_and_protected_payload_is_blocked(
    loop: Loop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protected: bool
) -> None:
    assert isinstance(loop.provider, Provider)
    provider = loop.provider

    class Client(FakeIssues):
        def run_gh(self, *_: str) -> str:
            pytest.fail("unexpected provider effect outside the publication stub")

        def list_marked_prs(self, *_: Any) -> dict[str, Any]:
            return {}

        def create(
            self, slug: Any, title: str, body: str, labels: list[str], assignees: list[str]
        ) -> str:
            url = super().create(slug, title, body, labels, assignees)
            provider.rows[url] = {"html_url": url, "body": body, "state": "open"}
            return url

    client = Client()
    monkeypatch.setattr("hungry_crab.loop_work.GhIssueClient", lambda **_: client)
    monkeypatch.setattr("hungry_crab.loop_work.check_push_target", lambda *_: None)
    monkeypatch.setattr("hungry_crab.serve._require_repository_root", lambda _: None)
    meal = tmp_path / "meal"
    meal.mkdir()
    card = Candidate(
        "tests",
        "tests.unit",
        "Add isolated tests",
        "Test isolation",
        serve_as="pr",
        license_mode="REIMPLEMENT",
        origin="licensed",
    )
    card.trace = {"prey": "example/prey", "sha": "b" * 40}
    atomic_json(
        meal / "menu.json", {"prey": {"label": "example/prey"}, "candidates": [card.to_dict()]}
    )
    atomic_json(meal / "notes.json", {card.id: {"why": "Catch regressions", "how": "Use fixtures"}})
    force_phase(
        loop,
        "serve",
        meals=[
            {
                "prey": "example/prey",
                "meal": str(meal / "meal.json"),
                "notes": str(meal / "notes.json"),
            }
        ],
    )
    ready = loop.next()
    issue = serve_phase(loop, ready["active"]["token"], "example/prey", card.id)
    issue_url = issue["served"][0]["url"]
    loop.record(ready["active"]["token"], "serve", "ok", receipt={"urls": [issue_url]})
    target = ".github/workflows/unsafe.yml" if protected else "tests/test_isolation.py"
    destination = loop.maw / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("def test_isolated():\n    assert True\n", encoding="utf-8")
    spec = loop.maw / nutrient_spec_path(card.id)
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("# Specification\n\nIsolate test fixtures.\n", encoding="utf-8")
    receipt = json.dumps(
        {
            "version": 1,
            "nutrient_id": card.id,
            "changed_paths": [target],
            "summary": "implemented from a specification, without access to the prey source",
            "checks": ["pytest"],
        }
    )
    publications: list[PreparedPullRequest] = []

    def publish_stub(
        nutrient: str, prepared: PreparedPullRequest, *_: Any, **__: Any
    ) -> PullRequestPublication:
        assert nutrient == card.id
        publications.append(prepared)
        provider.rows[URL] = artifact()
        provider.changed[URL] = [
            {"filename": file.path, "status": "added"} for file in prepared.files
        ]
        return PullRequestPublication("crab/tests/isolation", URL, True)

    monkeypatch.setattr(
        "hungry_crab.serve.publish_prepared_cleanroom_git_pull_request", publish_stub
    )
    ready = loop.next()
    if protected:
        before = loop.path.read_bytes()
        with pytest.raises(CrabError, match="protected"):
            serve_phase(
                loop, ready["active"]["token"], "example/prey", card.id, receipt_payload=receipt
            )
        assert loop.path.read_bytes() == before and not publications
        return
    result = serve_phase(
        loop, ready["active"]["token"], "example/prey", card.id, receipt_payload=receipt
    )
    assert result["served"][0]["url"] == URL
    assert len(publications) == 1 and loop.status()["issues"] == [issue_url]
    with pytest.raises(CrabError, match="at most one"):
        serve_phase(
            loop, ready["active"]["token"], "example/prey", card.id, receipt_payload=receipt
        )
    loop.record(ready["active"]["token"], "grow", "ok")
    ready = loop.next()
    loop.record(ready["active"]["token"], "trial", "ok", receipt={"tests_passed": True})
    assert not loop.next()["ready"]
    provider.rows[URL].update(merged_at="today", merge_commit_sha=provider.rows[URL]["head"]["sha"])
    ready = loop.next()
    assert ready["ready"] and ready["phase"] == "taste"
