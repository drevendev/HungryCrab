from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git
from test_attribution import MIT_TEXT
from test_attribution import _payload as materialization_receipt
from test_multifeed import fake_eat
from test_pr_serving import TRACE
from test_serve import FakeIssues

from hungry_crab.cache import Slug, Target
from hungry_crab.errors import CrabError
from hungry_crab.feeder import EatOptions, EatResult
from hungry_crab.fetch.git import GitRunner
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.multifeed import eat_many
from hungry_crab.multiserve import serve_many
from hungry_crab.nutrients import Candidate
from hungry_crab.pr_publication import (
    PreparedPullRequest,
    PullRequestPublication,
    nutrient_spec_path,
)
from hungry_crab.serve import ServeOptions, ServeReport


class _Client(FakeIssues):
    run_gh = None

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def identity(self) -> str:
        self.calls.append("identity")
        return "fixture-publisher"

    def list_marked_prs(self, slug: Slug) -> dict[str, dict[str, Any]]:
        self.calls.append("list")
        return {}


@dataclass
class _Batch:
    maw: Path
    bundle: Path
    cards: list[Candidate]
    receipts: dict[str, str]
    config: MawConfig
    ledger: Ledger
    client: _Client
    published: list[tuple[str, bool]]
    existing: set[str]

    def run(self, monkeypatch: pytest.MonkeyPatch) -> ServeReport:
        # Keep the actual multi-receipt decoder, kind/identity checks and payload preparation.
        monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(self.receipts.values())))
        return serve_many(
            self.bundle,
            self.maw,
            ServeOptions(ids=[card.id for card in self.cards], mode="pr-branch"),
            config=self.config,
            ledger=self.ledger,
            client=self.client,
        )


@pytest.fixture
def batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> _Batch:
    maw = tmp_path / "maw"
    maw.mkdir()
    git(maw, "init", "-b", "main")
    git(maw, "remote", "add", "origin", "https://github.com/example/maw.git")
    roots = [tmp_path / "a", tmp_path / "b"]
    cards: list[Candidate] = []
    receipts: dict[str, str] = {}
    second_mode = getattr(request, "param", "REIMPLEMENT")
    for root in roots:
        root.mkdir()
        (root / "LICENSE").write_text(MIT_TEXT, encoding="utf-8")
        (root / "ci").mkdir()
        (root / "ci/test.yml").write_text(f"name: {root.name}\n", encoding="utf-8")
        git(root, "init", "-b", "main")
        git(root, "add", ".")
        git(
            root,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@invalid.example",
            "commit",
            "-m",
            "fixture",
        )
        card = Candidate(
            "ci",
            f"ci.{root.name}",
            f"Improve {root.name}",
            "A reviewed configuration gap",
            license_mode=second_mode if root.name == "b" else "REIMPLEMENT",
            serve_as="pr",
            origin="licensed",
            score=0.8,
        )
        cards.append(card)
        generated = maw / "generated" / f"{root.name}.yml"
        generated.parent.mkdir(exist_ok=True)
        generated.write_text(f"name: {root.name}\n", encoding="utf-8")
        spec = maw / nutrient_spec_path(card.id)
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("# Observed behavior\nRun the project checks.\n", encoding="utf-8")
        receipts[card.id] = json.dumps(
            {
                "version": 1,
                "nutrient_id": card.id,
                "changed_paths": [f"generated/{root.name}.yml"],
                "summary": TRACE,
                "checks": ["pytest -q"],
            }
        )

    def feed(prey: Target, maw: Path, options: EatOptions, **kwargs: object) -> EatResult:
        result = fake_eat(prey, maw, options, **kwargs)
        card = cards[roots.index(prey.path)]
        path = result.out_dir / "menu.json"
        menu = json.loads(path.read_text(encoding="utf-8"))
        assert prey.path is not None
        menu["prey"]["sha"] = GitRunner(prey.path).head_sha()
        menu["candidates"] = [card.to_dict()]
        if card.license_mode in {"COPY", "COPY_FILE"}:
            receipts[card.id] = materialization_receipt(
                nutrient_id=card.id,
                source=menu["prey"],
                taken=[
                    {"prey_path": "ci/test.yml", "maw_path": "generated/b.yml", "verbatim": True}
                ],
            )
        path.write_text(json.dumps(menu), encoding="utf-8")
        return result

    monkeypatch.setattr("hungry_crab.multifeed.eat", feed)
    bundle = eat_many([Target(path=root) for root in roots], maw, EatOptions(out=tmp_path / "meal"))
    config = MawConfig.load(maw)
    config.serve.prs = "ask"
    config.serve.max_prs_per_run = 2
    state = _Batch(
        maw,
        bundle.out_dir,
        cards,
        receipts,
        config,
        Ledger(tmp_path / "ledger.json"),
        _Client(),
        [],
        set(),
    )

    def publish(
        nutrient_id: str,
        prepared: PreparedPullRequest,
        *args: object,
        allow_create: bool,
        **kwargs: Any,
    ) -> PullRequestPublication | None:
        state.published.append((nutrient_id, allow_create))
        kwargs["list_marked_prs"]()
        if nutrient_id not in state.existing and not allow_create:
            return None
        return PullRequestPublication(
            f"crab/{nutrient_id.rsplit(':', 1)[-1]}",
            f"https://github.com/example/maw/pull/{len(state.published)}",
            nutrient_id not in state.existing,
        )

    # Only the provider effect is replaced; receipts, file containment, handoff hashes,
    # generated fields and the global secret scan all run through production code.
    monkeypatch.setattr("hungry_crab.serve.publish_prepared_cleanroom_git_pull_request", publish)
    return state


@pytest.mark.parametrize(
    "defect",
    ["wrong_kind", "missing_file", "missing_spec", "file_secret", "spec_secret", "body_secret"],
)
def test_second_source_failure_happens_before_any_provider_access(
    batch: _Batch, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    second = batch.cards[1]
    token = "ghp_" + "A" * 36  # synthetic scanner fixture, never an actual credential
    if defect == "wrong_kind":
        batch.receipts[second.id] = materialization_receipt(nutrient_id=second.id)
    elif defect == "missing_file":
        (batch.maw / "generated/b.yml").unlink()
    elif defect == "missing_spec":
        (batch.maw / nutrient_spec_path(second.id)).unlink()
    elif defect in {"file_secret", "spec_secret"}:
        path = "generated/b.yml" if defect == "file_secret" else nutrient_spec_path(second.id)
        (batch.maw / path).write_text(f"credential: {token}\n", encoding="utf-8")
    else:
        payload = json.loads(batch.receipts[second.id])
        payload["summary"] = f"{TRACE}; credential: {token}"
        batch.receipts[second.id] = json.dumps(payload)
    with pytest.raises(CrabError) as error:
        batch.run(monkeypatch)
    assert token not in str(error.value)
    assert batch.published == [] and batch.client.calls == []
    assert not batch.ledger.entries
    assert batch.ledger.path is not None and not batch.ledger.path.exists()


@pytest.mark.parametrize("first_state", ["new", "ledger", "provider", "zero_budget"])
def test_all_sources_share_a_new_creation_budget(
    batch: _Batch, monkeypatch: pytest.MonkeyPatch, first_state: str
) -> None:
    first, second = batch.cards
    batch.config.serve.max_prs_per_run = 0 if first_state == "zero_budget" else 1
    if first_state == "ledger":
        batch.ledger.ensure(first)
        batch.ledger.mark(first.id, "served", url="https://github.com/example/maw/pull/9")
    elif first_state in {"provider", "zero_budget"}:
        batch.existing.add(first.id)
    report = batch.run(monkeypatch)
    assert len(report.served) == (0 if first_state == "zero_budget" else 1)
    expected_id = first.id if first_state == "new" else second.id
    if report.served:
        assert report.served[0]["id"] == expected_id
        assert batch.ledger.entries[expected_id].status == "served"
    if first_state == "ledger":
        assert batch.published == [(second.id, True)]
    elif first_state == "provider":
        assert batch.published == [(first.id, True), (second.id, True)]
        assert any("ledger reconciled" in row["reason"] for row in report.skipped)
    else:
        assert batch.published == [(first.id, first_state == "new"), (second.id, False)]


def test_valid_sources_publish_both_and_return_both_provider_receipts(
    batch: _Batch, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = batch.run(monkeypatch)
    assert [row["id"] for row in report.served] == [card.id for card in batch.cards]
    assert len({row["url"] for row in report.served}) == 2
    assert all(entry.status == "served" for entry in batch.ledger.entries.values())


@pytest.mark.parametrize("batch", ["COPY", "COPY_FILE"], indirect=True)
@pytest.mark.parametrize("bad_source", [False, True])
def test_materialization_sources_are_verified_before_any_source_is_published(
    batch: _Batch, monkeypatch: pytest.MonkeyPatch, bad_source: bool
) -> None:
    second = batch.cards[1]
    if bad_source:
        payload = json.loads(batch.receipts[second.id])
        payload["taken"][0]["prey_path"] = "ci/absent.yml"
        batch.receipts[second.id] = json.dumps(payload)
        with pytest.raises(CrabError):
            batch.run(monkeypatch)
        assert not batch.published and not batch.client.calls and not batch.ledger.entries
    else:
        report = batch.run(monkeypatch)
        assert [row["id"] for row in report.served] == [card.id for card in batch.cards]
        assert report.served[1]["working_tree_paths"] == ["generated/b.yml"]
