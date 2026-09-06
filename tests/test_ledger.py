from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hungry_crab.errors import CrabError
from hungry_crab.ledger import LEDGER_SCHEMA, Ledger, LedgerEntry
from hungry_crab.nutrients import Candidate

NOW = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2025, 6, 2, 12, 0, tzinfo=UTC)
MENU = {
    "prey": {
        "label": "example/prey",
        "sha": "a" * 40,
        "url": "https://github.com/example/prey",
        "license": "MIT",
    },
    "verdict": {"mode": "COPY"},
}


def _cards() -> list[Candidate]:
    cards = [
        Candidate("ci", "ci.cache", "Cache", "caches", artifact="pr"),
        Candidate("tooling", "tooling.dependabot", "Dependabot", "bot", artifact="pr"),
        Candidate("docs", "docs.site", "Docs site", "site", artifact="issue"),
    ]
    for index, card in enumerate(cards):
        card.score = 0.9 - index * 0.1
        card.provenance = {"prey": "example/prey", "sha": "a" * 40}
    return cards


def test_record_mark_hide_and_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / ".crab" / "ledger.json"
    ledger = Ledger.load(path, maw="maw")
    assert ledger.entries == {} and ledger.meals == []
    assert ledger.record_meal(MENU, _cards(), now=NOW) == 3
    assert ledger.record_meal(MENU, _cards()[:2], now=LATER) == 0
    assert len(ledger.meals) == 2 and ledger.meals[1].candidates == 2 and ledger.meals[1].new == 0
    entry = ledger.entries["crab:ci:ci.cache"]
    assert entry.status == "proposed"
    assert entry.first_seen.startswith("2025-06-01") and entry.last_seen.startswith("2025-06-02")
    assert entry.prey == "example/prey" and entry.score == 0.9

    ledger.mark("crab:ci:ci.cache", "rejected", reason="we use a different cache", now=LATER)
    ledger.mark(
        "crab:tooling:tooling.dependabot",
        "served",
        url="https://github.com/x/y/issues/1",
        now=LATER,
    )
    with pytest.raises(CrabError, match="unknown nutrient id"):
        ledger.mark("crab:nope:x", "accepted")
    with pytest.raises(CrabError, match="unknown status"):
        ledger.mark("crab:ci:ci.cache", "maybe")
    hidden = ledger.hidden_ids()
    assert hidden == {
        "crab:ci:ci.cache": "ledger: rejected (we use a different cache)",
        "crab:tooling:tooling.dependabot": "ledger: served https://github.com/x/y/issues/1",
    }
    assert [e.id for e in ledger.decisions()] == [
        "crab:ci:ci.cache",
        "crab:tooling:tooling.dependabot",
    ]

    saved = ledger.save(now=LATER)
    assert saved == path and path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == LEDGER_SCHEMA
    assert data["maw"] == "maw"
    assert [e["id"] for e in data["entries"]] == sorted(e["id"] for e in data["entries"])
    reloaded = Ledger.load(path)
    assert reloaded.maw == "maw"
    assert reloaded.entries["crab:ci:ci.cache"].reason == "we use a different cache"
    assert reloaded.meals[0].prey == "example/prey" and reloaded.meals[0].mode == "COPY"
    stats = reloaded.stats()
    assert stats["entries"] == 3 and stats["meals"] == 2
    assert stats["by_status"] == {"proposed": 1, "rejected": 1, "served": 1}
    assert stats["by_category"]["ci"] == {"rejected": 1}
    assert stats["by_prey"]["example/prey"]["proposed"] == 1


def test_ledger_without_a_path_stays_in_memory() -> None:
    ledger = Ledger(None, maw="h")
    ledger.record_meal(MENU, _cards(), now=NOW)
    assert ledger.save() is None
    assert ledger.ensure(_cards()[0]).id == "crab:ci:ci.cache"
    fresh = Candidate("tests", "tests.e2e", "E2E", "e2e")
    assert ledger.ensure(fresh, now=NOW).status == "proposed"
    assert "crab:tests:tests.e2e" in ledger.entries


def test_invalid_ledger_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CrabError, match="not valid JSON"):
        Ledger.load(path)
    entry = LedgerEntry.from_dict({"id": "crab:a:b", "status": "served", "extra": 1})
    assert entry.category == "" and entry.status == "served"
