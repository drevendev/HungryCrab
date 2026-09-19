from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.errors import CrabError
from hungry_crab.ledger import Ledger
from hungry_crab.nutrients import Candidate
from hungry_crab.pr_publication import PreparedPullRequest
from hungry_crab.pr_serve import serve_pr_branches

NOW = datetime(2026, 9, 19, tzinfo=UTC)
TRACE = "implemented from a specification, without access to the prey source"


def _card(category: str, key: str, *, mode: str = "REIMPLEMENT") -> Candidate:
    return Candidate(
        category,
        key,
        f"Serve {key}",
        f"A bounded {key} improvement",
        license_mode=mode,
        score=0.8,
    )


def _receipt(card: Candidate, path: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "nutrient_id": card.id,
            "changed_paths": [path],
            "summary": TRACE,
            "checks": ["pytest -q"],
        }
    )


def _render(card: Candidate) -> tuple[str, str]:
    return card.title, f"<!-- {card.id} -->\nTrace: clean-room implementation.\n"


def _write(maw: Path, path: str, content: str = "enabled = true\n") -> None:
    target = maw / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_pr_serve_publishes_exact_receipt_files_then_persists_ledger(tmp_path: Path) -> None:
    card = _card("ci", "ci.cache")
    maw = tmp_path / "maw"
    maw.mkdir()
    path = "generated/cache.toml"
    _write(maw, path)
    ledger = Ledger(tmp_path / "ledger.json", maw="maw")
    published: list[tuple[str, PreparedPullRequest]] = []

    def publish(branch: str, prepared: PreparedPullRequest) -> str:
        published.append((branch, prepared))
        return "https://github.com/example/maw/pull/9"

    result = serve_pr_branches(
        [card],
        receipt_payloads={card.id: _receipt(card, path)},
        maw_root=maw,
        ledger=ledger,
        render=_render,
        list_marked_prs=dict,
        publish=publish,
        prs_mode="ask",
        max_prs_per_run=3,
        confirmed=True,
        now=NOW,
    )

    assert result.served == [
        {
            "id": card.id,
            "url": "https://github.com/example/maw/pull/9",
            "branch": published[0][0],
        }
    ]
    assert result.skipped == []
    assert published[0][1].files[0].path == path
    assert published[0][1].files[0].content == "enabled = true\n"
    assert published[0][1].body.startswith(f"<!-- {card.id} -->\n")
    assert ledger.entries[card.id].status == "served"
    assert ledger.entries[card.id].url == "https://github.com/example/maw/pull/9"
    saved = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert saved["entries"][0]["url"] == "https://github.com/example/maw/pull/9"


def test_secret_hit_blocks_provider_read_and_publication_effect(tmp_path: Path) -> None:
    card = _card("ci", "ci.cache")
    maw = tmp_path / "maw"
    maw.mkdir()
    path = "generated/settings.env"
    _write(maw, path, "TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n")
    calls: list[str] = []

    def list_marked_prs() -> dict[str, dict[str, Any]]:
        calls.append("reconcile")
        return {}

    def publish(_: str, __: PreparedPullRequest) -> str:
        calls.append("publish")
        return "https://github.com/example/maw/pull/9"

    with pytest.raises(CrabError, match="possible secret"):
        serve_pr_branches(
            [card],
            receipt_payloads={card.id: _receipt(card, path)},
            maw_root=maw,
            ledger=Ledger(None),
            render=_render,
            list_marked_prs=list_marked_prs,
            publish=publish,
            prs_mode="auto",
            max_prs_per_run=3,
        )

    assert calls == []


def test_creation_budget_still_allows_existing_pr_reconciliation(tmp_path: Path) -> None:
    first = _card("ci", "ci.cache")
    second = _card("tooling", "tooling.dependabot")
    third = _card("tests", "tests.matrix")
    cards = [first, second, third]
    maw = tmp_path / "maw"
    maw.mkdir()
    receipts: dict[str, str] = {}
    for index, card in enumerate(cards):
        path = f"generated/{index}.txt"
        _write(maw, path, f"value {index}\n")
        receipts[card.id] = _receipt(card, path)

    existing_url = "https://github.com/example/maw/pull/8"
    existing = {second.id: {"url": existing_url, "state": "open", "number": 8}}
    publications: list[str] = []
    ledger = Ledger(tmp_path / "ledger.json", maw="maw")

    def publish(branch: str, _: PreparedPullRequest) -> str:
        publications.append(branch)
        return "https://github.com/example/maw/pull/9"

    result = serve_pr_branches(
        cards,
        receipt_payloads=receipts,
        maw_root=maw,
        ledger=ledger,
        render=_render,
        list_marked_prs=lambda: existing,
        publish=publish,
        prs_mode="auto",
        max_prs_per_run=1,
        now=NOW,
    )

    assert len(publications) == 1
    assert [item["id"] for item in result.served] == [first.id]
    reasons = {item["id"]: item["reason"] for item in result.skipped}
    assert reasons[second.id] == f"pull request exists {existing_url}"
    assert reasons[third.id] == "serve.max_prs_per_run reached"
    assert ledger.entries[first.id].status == "served"
    assert ledger.entries[second.id].status == "served"
    assert third.id not in ledger.entries


@pytest.mark.parametrize("mode", ["IDEAS_ONLY", "COPY"])
def test_non_reimplement_mode_fails_before_provider_effects(tmp_path: Path, mode: str) -> None:
    card = _card("ci", "ci.cache", mode=mode)
    maw = tmp_path / "maw"
    maw.mkdir()
    path = "generated/cache.toml"
    _write(maw, path)
    calls: list[str] = []

    with pytest.raises(CrabError, match="not eligible"):
        serve_pr_branches(
            [card],
            receipt_payloads={card.id: _receipt(card, path)},
            maw_root=maw,
            ledger=Ledger(None),
            render=_render,
            list_marked_prs=lambda: calls.append("reconcile") or {},
            publish=lambda _branch, _prepared: calls.append("publish") or "url",
            prs_mode="auto",
            max_prs_per_run=3,
        )

    assert calls == []


def test_ask_and_off_modes_block_before_receipt_or_provider_reads(tmp_path: Path) -> None:
    card = _card("ci", "ci.cache")
    calls: list[str] = []

    common = {
        "cards": [card],
        "receipt_payloads": {},
        "maw_root": tmp_path,
        "ledger": Ledger(None),
        "render": _render,
        "list_marked_prs": lambda: calls.append("reconcile") or {},
        "publish": lambda _branch, _prepared: calls.append("publish") or "url",
        "max_prs_per_run": 3,
    }
    with pytest.raises(CrabError, match="requires confirmation"):
        serve_pr_branches(prs_mode="ask", **common)
    with pytest.raises(CrabError, match="serve.prs is off"):
        serve_pr_branches(prs_mode="off", confirmed=True, **common)

    assert calls == []
