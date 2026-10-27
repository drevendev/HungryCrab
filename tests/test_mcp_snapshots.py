"""Actual miner/compare call counts and invalidation of one shallow MCP meal."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab import digest as digest_module
from hungry_crab import mcp_service, mcp_snapshot
from hungry_crab.errors import CrabError
from hungry_crab.mcp_service import AnalysisService


def service_pair(tmp_path: Path) -> tuple[AnalysisService, Path, Path]:
    donor = repository(
        tmp_path / "donor",
        {
            "app.py": "pass\n",
            ".github/dependabot.yml": "version: 2\nupdates: []\n",
            ".editorconfig": "root = true\n",
        },
    )
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "--depth", "1", donor.as_uri(), str(shallow))
    maw = repository(tmp_path / "maw", {"app.py": "pass\n", ".crab.yml": "ledger: none\n"})
    return AnalysisService([shallow, maw], tmp_path / "cache"), shallow, maw


def test_shallow_pages_use_one_analysis_and_preserve_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, prey, maw = service_pair(tmp_path)
    miners, compares = 0, 0
    original = digest_module.run_miners
    original_compare = mcp_service.compare_for_maw

    def count_miners(*args: object, **kwargs: object) -> object:
        nonlocal miners
        miners += 1
        return original(*args, **kwargs)

    def count_compare(*args: object, **kwargs: object) -> object:
        nonlocal compares
        compares += 1
        return original_compare(*args, **kwargs)

    monkeypatch.setattr(digest_module, "run_miners", count_miners)
    monkeypatch.setattr(mcp_service, "compare_for_maw", count_compare)
    first = service.menu(str(prey), str(maw), limit=1)
    assert first["next_offset"] == 1
    # Mutating a response cannot poison the session snapshot.
    first["candidates"][0]["title"] = "client mutation"
    rest = service.menu(str(prey), str(maw), offset=1, limit=100)
    again = service.menu(str(prey), str(maw), limit=1)
    assert rest["snapshot"]["id"] == first["snapshot"]["id"]
    assert again["candidates"][0]["title"] != "client mutation"
    assert miners == 2 and compares == 1
    assert not (maw / ".crab" / "ledger.json").exists()
    assert service.snapshots.counters == {"analyses": 1, "hits": 2, "validations": 2}


@pytest.mark.parametrize(
    "change", ["source", "policy", "receiving", "detached", "refs", "shallow", "expired", "corrupt"]
)
def test_changed_inputs_refuse_continuation_and_rebuild_from_zero(
    tmp_path: Path, change: str
) -> None:
    service, prey, maw = service_pair(tmp_path)
    first = service.menu(str(prey), str(maw), limit=1)
    if change == "source":
        (prey / "app.py").write_text("pass # changed\n", encoding="utf-8")
    elif change == "policy":
        (maw / ".crab.yml").write_text(
            "ledger: none\nscoring: {categories: {ci: 0.1}}\n", encoding="utf-8"
        )
    elif change == "receiving":
        (maw / "app.py").write_text("pass # receiving change\n", encoding="utf-8")
    elif change == "detached":
        git(maw, "checkout", "--detach", "-q", "HEAD")
    elif change == "refs":
        git(prey, "branch", "new-observed-ref")
    elif change == "shallow":
        (prey / ".git" / "shallow").unlink()
    elif change == "expired":
        service.snapshots.ttl = 0
    else:
        saved = next(iter(service.snapshots.entries.values())).value
        (saved.prey_dir / "inventory.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CrabError, match="snapshot"):
        service.menu(str(prey), str(maw), offset=1)
    current = service.menu(str(prey), str(maw), limit=1)
    assert not current["snapshot"]["reused"]
    assert current["meal_dir"] is None and first["meal_dir"] is None


def test_digest_pages_reuse_snapshot_and_keep_shallow_truth(tmp_path: Path) -> None:
    service, prey, _ = service_pair(tmp_path)
    first = service.digest(str(prey))
    page = service.digest(str(prey), page="inventory.md")
    assert page["snapshot"]["reused"]
    assert page["manifest"]["prey"]["shallow"] is True
    assert first["snapshot"]["id"] == page["snapshot"]["id"]


def test_ledger_vote_invalidates_snapshot_without_counting_a_read_timestamp(tmp_path: Path) -> None:
    from hungry_crab.ledger import Ledger
    from hungry_crab.nutrients import Candidate

    service, prey, maw = service_pair(tmp_path)
    (maw / ".crab.yml").write_text("ledger: repo\n", encoding="utf-8")
    (maw / ".gitignore").write_text(".crab/\n", encoding="utf-8")
    git(maw, "add", ".crab.yml", ".gitignore")
    git(maw, "commit", "-qm", "test: synthetic ledger policy")
    first = service.menu(str(prey), str(maw), limit=1)
    assert service.menu(str(prey), str(maw), offset=1)["snapshot"]["reused"]
    ledger = Ledger(maw / ".crab" / "ledger.json")
    card = Candidate.from_dict(first["candidates"][0])
    ledger.ensure(card)
    ledger.mark(card.id, "rejected", reason="authored synthetic owner feedback")
    ledger.save()
    with pytest.raises(CrabError, match="snapshot"):
        service.menu(str(prey), str(maw), offset=1)
    assert service.menu(str(prey), str(maw))["total"] == first["total"] - 1


def test_category_filter_reuses_analysis_and_eviction_requires_restart(tmp_path: Path) -> None:
    service, prey, maw = service_pair(tmp_path)
    first = service.menu(str(prey), str(maw), limit=1)
    filtered = service.menu(str(prey), str(maw), category=first["candidates"][0]["category"])
    assert filtered["snapshot"]["reused"]
    assert service.snapshots.counters["analyses"] == 1
    service.snapshots.capacity = 1
    service.digest(str(prey))
    with pytest.raises(CrabError, match="unavailable"):
        service.menu(str(prey), str(maw), offset=1)


def test_evicted_digest_page_cannot_silently_start_another_snapshot(tmp_path: Path) -> None:
    service, prey, maw = service_pair(tmp_path)
    service.snapshots.capacity = 1
    service.digest(str(prey))
    service.menu(str(prey), str(maw))
    analyses = service.snapshots.counters["analyses"]
    (prey / "app.py").write_text("pass # changed after eviction\n", encoding="utf-8")
    with pytest.raises(CrabError, match="snapshot"):
        service.digest(str(prey), page="inventory.md")
    assert service.snapshots.counters["analyses"] == analyses
    assert not service.digest(str(prey))["snapshot"]["reused"]
    assert service.digest(str(prey), page="inventory.md")["snapshot"]["reused"]


def test_receiving_edit_during_hit_validation_refuses_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, prey, maw = service_pair(tmp_path)
    service.menu(str(prey), str(maw), limit=1)
    original = mcp_snapshot.evidence_identity

    def edit_during_validation(value: mcp_snapshot.Analysis) -> str:
        evidence = original(value)
        (maw / "app.py").write_text("pass # edit during validation\n", encoding="utf-8")
        return evidence

    monkeypatch.setattr(mcp_snapshot, "evidence_identity", edit_during_validation)
    with pytest.raises(CrabError, match="snapshot"):
        service.menu(str(prey), str(maw), offset=1)
    assert service.snapshots.counters["analyses"] == 1
