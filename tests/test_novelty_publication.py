"""Owned regression probes for the independent PR690 findings; Actions only."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fixture_builder import git
from test_attribution import _payload
from test_exploration import PLAN, STREAMING, observation, pair
from test_novelty_exclusions import PENDING, basis, scope_plan, setup
from test_pr_effects import FakeGh
from test_pr_serving import TRACE
from test_serve import MAW_SLUG, FakeIssues

from hungry_crab import exploration
from hungry_crab.cache import Target
from hungry_crab.compare import compare_for_maw, load_menu, menu_candidates
from hungry_crab.digest import DigestOptions
from hungry_crab.errors import CrabError
from hungry_crab.fetch.git import GitRunner
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.novelty_exclusions import BASIS_SCHEMA, reconcile
from hungry_crab.pr_publication import nutrient_branch_name, nutrient_spec_path
from hungry_crab.serve import GhIssueClient, ServeOptions, serve


class CurrentIssues(FakeIssues):
    def novelty_heads(self, slug: Any, targets: list[dict[str, Any]]) -> dict[str, str]:
        return {target["ref"]: target["sha"] for target in targets}


@pytest.mark.parametrize("disposition", ["duplicate", "already-present"])
def test_bound_exclusion_refuses_new_effect_without_later_reconciliation(
    tmp_path: Path, disposition: str
) -> None:
    directory, donor, maw, finding = setup(tmp_path)
    finding["source_behavior"] = "reads bounded chunks"
    card = exploration.register(directory, finding, source_repo=donor)["record"]["card"]
    scope = exploration._snapshot(directory)["novelty_scope"]
    exploration.record(
        directory,
        observation(
            finding, kind="decision", hypothesis="bounded reads", reviewed=True,
            disposition=disposition, candidate_id=card["id"], novelty_basis=basis(scope),
        ),
    )
    before = exploration.events(directory)
    client = CurrentIssues()
    try:
        serve(
            directory.parent.parent, maw, ServeOptions(ids=[card["id"]], mode="issue"),
            config=MawConfig.load(maw), ledger=Ledger(None), client=client,
            slug_lookup=lambda _: MAW_SLUG,
        )
    except CrabError:
        pass
    assert not client.created, "a resolved implementation exclusion created a new issue"
    assert not client.labels
    assert exploration.events(directory) == before
    known = CurrentIssues({card["id"]: {"number": 19, "state": "open"}})
    result = serve(
        directory.parent.parent, maw, ServeOptions(ids=[card["id"]], mode="issue"),
        config=MawConfig.load(maw), ledger=Ledger(None), client=known,
        slug_lookup=lambda _: MAW_SLUG,
    )
    assert result.skipped and not known.created


@pytest.mark.parametrize("existing", [False, True])
def test_indexed_candidate_later_supersession_is_not_lost(
    tmp_path: Path, npm_app: Path, pyproject_cli: Path, existing: bool
) -> None:
    maw = tmp_path / "maw"
    shutil.copytree(pyproject_cli, maw)
    (maw / ".crab.yml").write_text(
        "ledger: none\nhunger:\n  ci: issues-only\n", encoding="utf-8"
    )
    git(maw, "add", ".crab.yml")
    git(maw, "commit", "-m", "test: own indexed publication policy")
    result, _, _, _ = compare_for_maw(
        Target(path=npm_app), maw,
        digest_options=DigestOptions(cache_root=tmp_path / "cache"), record_ledger=False,
    )
    assert result.meal_dir is not None
    meal = result.meal_dir
    menu = load_menu(meal)
    assert menu is not None
    card = next(c for c in menu_candidates(menu) if c.key == "ci.cache")
    assert "exploration_snapshot" not in card.trace
    workflow = ".github/workflows/ci.yml"
    selected = git(maw, "symbolic-ref", "--short", "HEAD").strip()
    pin = git(maw, "rev-parse", "HEAD").strip()

    def plan(pending: str | None = None) -> dict[str, Any]:
        return {
            "prefixes": [workflow], "default_ref": selected, "default_sha": pin,
            "pending": [{"ref": PENDING, "sha": pending}] if pending else [],
            "pending_collection": {"complete": True, "as_of": "owned provider observation"},
        }

    directory = exploration.initialize(meal, {**PLAN, "novelty_scope": plan()}, maw=maw)
    denominator = exploration.report(directory)["denominator"]
    original = exploration.record(directory, {
        "kind": "decision", "candidate_id": card.id, "hypothesis": "dependency cache",
        "reviewed": True, "selected": True, "disposition": "verified-gap",
        "author": "owned reviewer", "tool": "owned fixture", "attempt": "indexed-1",
        "reason": "receiving pipeline lacks dependency caching",
        "source_evidence": [{"sha": menu["prey"]["sha"], "path": workflow, "start": 1, "end": 1}],
        "maw_evidence": [{"sha": pin, "path": workflow, "start": 1, "end": 1}],
    })
    git(maw, "checkout", "--detach", pin)
    target = maw / workflow
    original_text = target.read_text(encoding="utf-8")
    addition = "      - uses: actions/cache@v4\n        with:\n          path: .cache\n          key: owned-cache\n"
    target.write_text(original_text + addition, encoding="utf-8", newline="\n")
    git(maw, "add", workflow)
    git(maw, "commit", "-m", "test: separately observed cache implementation")
    pending = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "update-ref", PENDING, pending)
    git(maw, "checkout", selected)
    start = len(original_text.splitlines()) + 1
    reconcile(directory, {
        "operation": "indexed-later", "original_event": original["sha256"], "scope": plan(pending),
        "novelty_basis": {
            "schema": BASIS_SCHEMA, "role": "receiving-pending", "ref": PENDING, "sha": pending,
            "evidence": [{"path": workflow, "start": start, "end": start + 3,
                          "content_sha256": hashlib.sha256(addition.encode()).hexdigest()}],
        },
        "author": "owned reviewer", "tool": "owned fixture", "attempt": "indexed-2",
        "reason": "later cache implementation supersedes the original need now",
    })
    client = CurrentIssues({card.id: {"number": 21, "state": "open"}} if existing else {})
    try:
        served = serve(
            meal, maw, ServeOptions(ids=[card.id], mode="issue"), config=MawConfig.load(maw),
            ledger=Ledger(None), client=client, slug_lookup=lambda _: MAW_SLUG,
        )
    except CrabError:
        assert not existing, "existing provider marker must remain reconcilable"
    else:
        if existing:
            assert served.skipped and not served.served
    assert not client.created, "an initial indexed card bypassed its later publication hold"
    assert not client.labels
    report = exploration.report(directory)
    assert report["denominator"] == denominator
    assert report["decisions"][0]["candidate_id"] == card.id
    assert report["decisions"][0]["disposition"] == "verified-gap"
    assert report["novelty_reconciliations"][0]["publication_held"]


@pytest.mark.parametrize("mode", ["COPY", "REIMPLEMENT"])
@pytest.mark.parametrize("tamper", ["none", "unrelated", "payload", "policy", "ref"])
def test_scoped_pr_allows_its_exact_prepared_tracked_implementation(
    tmp_path: Path, mode: str, tamper: str
) -> None:
    initial, donor, maw, finding = pair(tmp_path, hunger="true")
    directory = exploration.initialize(
        initial.parent.parent, {**PLAN, "novelty_scope": scope_plan(maw)}, maw=maw
    )
    finding["source_behavior"] = "reads bounded chunks"
    card = exploration.register(directory, finding, source_repo=donor)["record"]["card"]
    meal = directory.parent.parent
    menu = load_menu(meal)
    assert menu is not None
    assert card["license_mode"] == "COPY"
    if mode == "REIMPLEMENT":
        # Choosing a stricter original-implementation route narrows the MIT grant.
        for row in menu["candidates"]:
            if row["id"] == card["id"]:
                row["license_mode"] = mode
        (meal / "menu.json").write_text(json.dumps(menu), encoding="utf-8")
    selected = git(maw, "symbolic-ref", "--short", "HEAD").strip()
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=" + selected)
    git(maw, "remote", "add", "origin", str(remote))
    git(maw, "push", "origin", selected)
    (maw / "app.py").write_text(STREAMING, encoding="utf-8", newline="\n")
    if mode == "COPY":
        payload = _payload(
            nutrient_id=card["id"],
            source={key: menu["prey"][key] for key in ("label", "url", "sha", "license")},
            taken=[{"maw_path": "app.py", "prey_path": "reader.py", "verbatim": False}],
        )
    else:
        spec = maw / nutrient_spec_path(card["id"])
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("# Behavior\nRead bounded chunks while preserving output.\n", encoding="utf-8")
        payload = json.dumps({
            "version": 1, "nutrient_id": card["id"], "changed_paths": ["app.py"],
            "summary": TRACE, "checks": ["owned fixture; no producer execution claimed"],
        })

    class Client(GhIssueClient):
        def __init__(self) -> None:
            self.effects = FakeGh()
            self.changed = False

        def identity(self) -> str:
            return "owned fixture"

        def list_marked_prs(self, slug: Any) -> dict[str, dict[str, Any]]:
            if not self.changed:
                self.changed = True
                if tamper == "unrelated":
                    (maw / "unrelated.txt").write_text("external work\n", encoding="utf-8")
                elif tamper == "payload":
                    (maw / "app.py").write_text("external payload\n", encoding="utf-8")
                elif tamper == "policy":
                    policy = maw / ".crab.yml"
                    policy.write_text(policy.read_text() + "serve:\n  prs: off\n", encoding="utf-8")
                elif tamper == "ref":
                    git(maw, "add", "app.py")
                    git(maw, "commit", "-m", "test: external receiving commit")
            return {}

        def novelty_heads(self, slug: Any, targets: list[dict[str, Any]]) -> dict[str, str]:
            return {target["ref"]: target["sha"] for target in targets}

        def run_gh(self, *args: str) -> str:
            if args[:2] == ("api", "repos/example/maw"):
                return selected
            return self.effects(*args)

    client = Client()
    def publish() -> Any:
        return serve(
            meal, maw, ServeOptions(ids=[card["id"]], mode="pr-branch"),
            config=MawConfig.load(maw), ledger=Ledger(None), client=client,
            receipt_payloads={card["id"]: payload}, prey_repo=donor,
            slug_lookup=lambda _: MAW_SLUG,
        )
    branch = nutrient_branch_name(card["id"])
    if tamper == "none":
        assert len(publish().served) == 1
        assert git(remote, "show", f"refs/heads/{branch}:app.py") == STREAMING
        assert (maw / "app.py").read_text(encoding="utf-8") == STREAMING
    else:
        with pytest.raises(CrabError):
            publish()
        assert GitRunner(remote).try_run("show-ref", "--verify", "refs/heads/" + branch) is None
        assert not any(call[:2] == ("pr", "create") for call in client.effects.calls)


def test_scoped_ordinary_gap_remains_publishable(tmp_path: Path) -> None:
    directory, donor, maw, finding = setup(tmp_path)
    finding["source_behavior"] = "reads bounded chunks"
    card = exploration.register(directory, finding, source_repo=donor)["record"]["card"]
    exploration.record(directory, observation(
        finding, kind="decision", hypothesis="bounded reads", reviewed=True,
        disposition="verified-gap", candidate_id=card["id"], selected=True,
    ))
    client = CurrentIssues()
    result = serve(
        directory.parent.parent, maw, ServeOptions(ids=[card["id"]], mode="issue"),
        config=MawConfig.load(maw), ledger=Ledger(None), client=client,
        slug_lookup=lambda _: MAW_SLUG,
    )
    assert len(result.served) == len(client.created) == 1


@pytest.mark.parametrize("tamper", ["none", "ledger", "source"])
def test_scoped_multi_issue_accepts_only_its_own_saved_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    from test_discovery_inputs import repository

    from hungry_crab.multiserve import serve_many
    from hungry_crab.nutrients import Candidate

    _, first, maw, initial_finding = pair(tmp_path / "initial", hunger="issues-only")
    (maw / ".crab.yml").write_text(
        "ledger: repo\nhunger:\n  code: issues-only\n", encoding="utf-8"
    )
    (maw / ".gitignore").write_text(".crab/\n", encoding="utf-8")
    git(maw, "add", ".crab.yml", ".gitignore")
    git(maw, "commit", "-m", "test: own persisted publication receipts")
    git(maw, "remote", "add", "origin", "https://github.com/example/maw.git")
    second = repository(tmp_path / "second", {"reader.py": STREAMING})
    roots = [first, second]
    paths = {}
    targets = {}
    cards = []
    source_menu = None
    for index, donor in enumerate(roots):
        result, _, _, _ = compare_for_maw(
            Target(path=donor), maw,
            digest_options=DigestOptions(cache_root=tmp_path / "cache"),
            record_ledger=False,
        )
        assert result.meal_dir is not None
        directory = exploration.initialize(
            result.meal_dir, {**PLAN, "novelty_scope": scope_plan(maw)}, maw=maw
        )
        source_menu = load_menu(result.meal_dir)
        assert source_menu is not None
        finding = {
            **initial_finding, "key": f"code.scoped-flow-{index}",
            "source_behavior": "owned bounded input",
            "source_evidence": [{
                "sha": source_menu["prey"]["sha"], "path": "reader.py", "start": 1, "end": 2,
            }],
            "maw_evidence": [{
                "sha": source_menu["maw"]["sha"], "path": "app.py", "start": 1, "end": 2,
            }],
        }
        registered = exploration.register(directory, finding, source_repo=donor)
        card = Candidate.from_dict(registered["record"]["card"])
        key = str(index)
        card.trace["primary_source"] = key
        cards.append(card)
        paths[key] = result.meal_dir
        targets[key] = Target(path=donor)
    assert source_menu is not None
    aggregate = {
        **source_menu, "candidates": [card.to_dict() for card in cards],
        "receiving_context_status": "observed",
        "receiving_context": exploration._snapshot(directory)["receiving_context"],
    }
    # The owned transport supplies actual meals; serving and freshness guards stay real.
    monkeypatch.setattr(
        "hungry_crab.multiserve.load_multi", lambda _: (aggregate, paths, targets)
    )
    config = MawConfig.load(maw)
    ledger = Ledger.load(config.ledger_path(), maw=maw.name)
    save = ledger.save
    saved = 0

    def save_and_observe(*, now: Any = None) -> Path | None:
        nonlocal saved
        result = save(now=now)
        saved += 1
        if saved == 1:
            if tamper == "ledger":
                assert ledger.path is not None
                ledger.path.write_bytes(ledger.path.read_bytes() + b"\n")
            elif tamper == "source":
                (maw / "app.py").write_text("external receiving edit\n", encoding="utf-8")
        return result

    monkeypatch.setattr(ledger, "save", save_and_observe)
    client = CurrentIssues()

    def publish() -> Any:
        return serve_many(
            tmp_path / "owned-bundle", maw, ServeOptions(ids=[c.id for c in cards], mode="issue"),
            config=config, ledger=ledger, client=client,
        )

    if tamper == "none":
        assert len(publish().served) == len(client.created) == saved == 2
    else:
        with pytest.raises(CrabError, match="receiving"):
            publish()
        assert len(client.created) == saved == 1
