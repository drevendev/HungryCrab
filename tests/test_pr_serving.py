from __future__ import annotations

import json
from pathlib import Path

import pytest

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.nutrients import Candidate
from hungry_crab.pr_publication import PullRequestPublication
from hungry_crab.pr_serve import publish_cleanroom_git_pull_request
from hungry_crab.pr_serving import serve_cleanroom_pull_requests

TRACE = "implemented from a specification, without access to the prey source"


def _card(key: str, *, mode: str = "REIMPLEMENT") -> Candidate:
    return Candidate(
        "ci",
        key,
        f"Carry {key}",
        "The prey has the behaviour.",
        license_mode=mode,
        serve_as="pr",
        score=0.8,
    )


def _receipt(card: Candidate) -> str:
    return json.dumps(
        {
            "version": 1,
            "nutrient_id": card.id,
            "changed_paths": [f"generated/{card.key}.yml"],
            "summary": TRACE,
            "checks": ["pytest -q"],
        }
    )


def test_ask_requires_explicit_selection_before_provider_access(tmp_path: Path) -> None:
    card = _card("cache")
    config = MawConfig(root=tmp_path)
    config.serve.prs = "ask"
    calls: list[str] = []

    with pytest.raises(CrabError, match=r"serve\.prs is ask"):
        serve_cleanroom_pull_requests(
            [card],
            {card.id: _receipt(card)},
            config=config,
            ledger=Ledger(None),
            explicit_selection=False,
            publisher=lambda *_: calls.append("publish") or None,
        )

    assert calls == []


def test_all_receipts_preflight_before_first_provider_effect(tmp_path: Path) -> None:
    first = _card("cache")
    second = _card("matrix")
    config = MawConfig(root=tmp_path)
    config.serve.prs = "auto"
    calls: list[str] = []

    with pytest.raises(CrabError, match="invalid clean-room implementation receipt"):
        serve_cleanroom_pull_requests(
            [first, second],
            {first.id: _receipt(first), second.id: "not-json"},
            config=config,
            ledger=Ledger(None),
            explicit_selection=False,
            publisher=lambda *_: calls.append("publish") or None,
        )

    assert calls == []


def test_non_reimplement_mode_fails_closed_before_provider_effects(tmp_path: Path) -> None:
    card = _card("cache", mode="COPY")
    config = MawConfig(root=tmp_path)
    config.serve.prs = "auto"
    calls: list[str] = []

    with pytest.raises(CrabError, match="license mode COPY"):
        serve_cleanroom_pull_requests(
            [card],
            {},
            config=config,
            ledger=Ledger(None),
            explicit_selection=False,
            publisher=lambda *_: calls.append("publish") or None,
        )

    assert calls == []


def test_creation_limit_counts_only_new_prs_and_commits_provider_receipts(tmp_path: Path) -> None:
    existing = _card("existing")
    created = _card("created")
    limited = _card("limited")
    cards = [existing, created, limited]
    config = MawConfig(root=tmp_path)
    config.serve.prs = "auto"
    config.serve.max_prs_per_run = 1
    ledger_path = tmp_path / "ledger.json"
    ledger = Ledger(ledger_path, maw="maw")
    calls: list[tuple[str, bool]] = []

    def publisher(
        card: Candidate, receipt_payload: str, allow_create: bool
    ) -> PullRequestPublication | None:
        assert json.loads(receipt_payload)["nutrient_id"] == card.id
        calls.append((card.id, allow_create))
        if card is existing:
            return PullRequestPublication(
                branch="crab/existing", url="https://example.test/pr/7", created=False
            )
        if card is created:
            return PullRequestPublication(
                branch="crab/created", url="https://example.test/pr/8", created=True
            )
        assert allow_create is False
        return None

    report = serve_cleanroom_pull_requests(
        cards,
        {card.id: _receipt(card) for card in cards},
        config=config,
        ledger=ledger,
        explicit_selection=False,
        publisher=publisher,
    )

    assert calls == [
        (existing.id, True),
        (created.id, True),
        (limited.id, False),
    ]
    assert [(item["id"], item["created"]) for item in report.served] == [
        (existing.id, False),
        (created.id, True),
    ]
    assert report.skipped == [
        {"id": limited.id, "reason": "serve.max_prs_per_run reached (1)"}
    ]
    assert ledger.entries[existing.id].status == "served"
    assert ledger.entries[created.id].url == "https://example.test/pr/8"
    saved = json.loads(ledger_path.read_text(encoding="utf-8"))
    saved_entries = {entry["id"]: entry for entry in saved["entries"]}
    assert saved_entries[existing.id]["url"] == "https://example.test/pr/7"
    assert saved_entries[created.id]["status"] == "served"
    assert limited.id not in saved_entries


def test_terminal_ledger_entry_needs_no_receipt_or_provider_read(tmp_path: Path) -> None:
    card = _card("cache")
    config = MawConfig(root=tmp_path)
    config.serve.prs = "auto"
    ledger = Ledger(None)
    ledger.ensure(card)
    ledger.mark(card.id, "served", url="https://example.test/pr/4")
    calls: list[str] = []

    report = serve_cleanroom_pull_requests(
        [card],
        {},
        config=config,
        ledger=ledger,
        explicit_selection=False,
        publisher=lambda *_: calls.append("publish") or None,
    )

    assert calls == []
    assert report.served == []
    assert report.skipped == [
        {"id": card.id, "reason": "ledger: served https://example.test/pr/4"}
    ]


def test_reconcile_only_publisher_has_zero_git_or_gh_effects_when_pr_is_absent(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "cache.yml").write_text("cache: true\n", encoding="utf-8")
    card = _card("cache")
    gh_calls: list[tuple[str, ...]] = []

    result = publish_cleanroom_git_pull_request(
        card.id,
        "feat: carry cache setup",
        f"<!-- {card.id} -->\n\nCarry cache setup.\n",
        _receipt(card),
        tmp_path,
        Slug("example", "maw"),
        list_marked_prs=dict,
        run_gh=lambda *args: gh_calls.append(args) or "",
        allow_create=False,
    )

    assert result is None
    assert gh_calls == []
