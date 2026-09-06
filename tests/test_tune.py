from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hungry_crab.compare.scoring import Scoring
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.nutrients import Candidate
from hungry_crab.tune import analyse, apply

NOW = datetime(2025, 6, 1, tzinfo=UTC)


def _ledger(decisions: dict[str, list[str]]) -> Ledger:
    """decisions: category -> list of statuses; keys are numbered per category."""
    ledger = Ledger(None, maw="h")
    cards: list[Candidate] = []
    for category, statuses in decisions.items():
        for index, _ in enumerate(statuses):
            card = Candidate(category, f"{category}.item-{index}", f"{category} {index}", "x")
            card.provenance = {"prey": "example/prey"}
            cards.append(card)
    ledger.record_meal(
        {"prey": {"label": "example/prey"}, "verdict": {"mode": "COPY"}}, cards, now=NOW
    )
    for category, statuses in decisions.items():
        for index, status in enumerate(statuses):
            if status != "proposed":
                ledger.mark(f"crab:{category}:{category}.item-{index}", status, now=NOW)
    return ledger


def test_no_decisions_means_no_suggestions() -> None:
    report = analyse(_ledger({"ci": ["proposed", "proposed"]}), Scoring.default())
    assert report.decisions == 0 and report.suggestions == []
    assert "No decisions in the ledger yet" in report.to_markdown()


def test_category_weights_move_with_acceptance() -> None:
    ledger = _ledger(
        {
            "ci": ["accepted", "served", "merged", "rejected"],
            "deps": ["rejected", "rejected", "rejected", "rejected", "rejected"],
            "docs": ["accepted", "rejected"],
            "tests": ["accepted", "rejected", "accepted", "rejected"],
        }
    )
    scoring = Scoring.default()
    report = analyse(ledger, scoring, min_decisions=3)
    assert report.decisions == 15
    by_target = {(s.kind, s.target): s for s in report.suggestions}
    ci = by_target[("category", "ci")]
    assert ci.current == 0.9 and ci.suggested == 1.0 and ci.acceptance == 0.75
    deps = by_target[("category", "deps")]
    assert deps.current == 0.45 and deps.suggested == 0.3
    assert by_target[("hunger", "deps")].suggested == "off"
    assert ("category", "docs") not in by_target, "two decisions are below the minimum"
    assert ("category", "tests") not in by_target, "50% acceptance is no signal"
    assert ("prey", "example/prey") not in by_target, "40% acceptance is not a poor match"
    poor = analyse(_ledger({"deps": ["rejected"] * 5}), scoring, min_decisions=3)
    prey = {(s.kind, s.target): s for s in poor.suggestions}[("prey", "example/prey")]
    assert prey.decisions == 5 and prey.acceptance == 0.0
    text = report.to_markdown()
    assert "| ci | 3 | 1 | 0 | 75% |" in text
    assert "category deps" in text and "crab tune --write" in text


def test_trait_level_suggestions_and_apply(tmp_path: Path) -> None:
    ledger = Ledger(None, maw="h")
    cards = []
    for prey_index in range(3):
        card = Candidate("tooling", "tooling.editorconfig", "Editorconfig", "x")
        card.provenance = {"prey": f"prey-{prey_index}"}
        cards.append(card)
    ledger.record_meal({"prey": {"label": "p"}, "verdict": {}}, cards[:1], now=NOW)
    ledger.mark("crab:tooling:tooling.editorconfig", "rejected", now=NOW)
    # the same key from another prey is the same ledger entry; simulate history via a second entry
    other = Candidate("tooling", "tooling.gitattributes", "Gitattributes", "x")
    ledger.record_meal({"prey": {"label": "p2"}, "verdict": {}}, [other], now=NOW)
    ledger.mark("crab:tooling:tooling.gitattributes", "rejected", now=NOW)
    report = analyse(ledger, Scoring.default(), min_decisions=3)
    assert report.suggestions == [], "one rejection per key is not enough"

    ledger = _ledger({"hygiene": ["accepted", "accepted", "accepted"]})
    for entry in ledger.entries.values():
        entry.key = "hygiene.security-md"
    report = analyse(ledger, Scoring.default(), min_decisions=3)
    kinds = {(s.kind, s.target): s for s in report.suggestions}
    assert kinds[("category", "hygiene")].suggested == 0.8
    assert kinds[("trait", "hygiene.security-md")].suggested == 1.0

    config = MawConfig.load(tmp_path)
    scoring = apply(report, config)
    assert scoring == {"categories": {"hygiene": 0.8}, "traits": {"hygiene.security-md": 1.0}}
    reloaded = MawConfig.load(tmp_path)
    assert reloaded.scoring == scoring
    assert Scoring.default().merged(reloaded.scoring).categories["hygiene"] == 0.8
