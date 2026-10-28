"""Recovery records interrupted work without leasing or losing provider truth."""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_loop import NOW, URL, Provider, artifact, force_phase
from test_loop import loop as loop
from test_loop_work import landed

from hungry_crab.errors import CrabError
from hungry_crab.loop import Loop, atomic_json


@pytest.mark.parametrize("provider_failure", [False, True])
def test_budget_block_persists_reconciled_molt_lesson(
    loop: Loop, monkeypatch: pytest.MonkeyPatch, provider_failure: bool
) -> None:
    assert isinstance(loop.provider, Provider)
    url = URL.replace("/1", "/2")
    loop.provider.rows[URL] = artifact(merged=True)
    loop.provider.rows[url] = artifact(url, ci="failure")
    force_phase(
        loop,
        "harden",
        prs=[landed(), {**landed(), "phase": "molt", "state": "open", "url": url, "files": []}],
    )
    data = loop._load()
    data["waiting_on"] = {"kind": "merge", "reason": "waiting for MOLT"}
    atomic_json(loop.path, data)
    loop.provider.pr_count = loop.settings.open_prs_max
    before = data["phases_today"]
    count = loop.provider.counts
    if provider_failure:

        def unavailable() -> tuple[int, int]:
            raise CrabError("synthetic counts failure after provider reconciliation")

        monkeypatch.setattr(loop.provider, "counts", unavailable)
        with pytest.raises(CrabError, match="counts failure"):
            loop.next()
        monkeypatch.setattr(loop.provider, "counts", count)
    view = loop.next()
    assert view["blocked_reason"] == "open artifact budget exhausted"
    saved = loop._load()
    assert saved["notes"] == view["notes"] and "Rejected MOLT" in saved["notes"]
    assert saved["prs"][1]["state"] == "closed"
    assert saved["waiting_on"] is None and saved["active"] is None
    assert saved["phases_today"] == before
    loop.next()
    assert loop._load()["notes"].count("Rejected MOLT") == 1


@pytest.mark.parametrize("operation", ["acknowledge", "pause", "next", "status"])
@pytest.mark.parametrize("invalid", ["expired", "future", "overlong"])
def test_recovery_clears_invalid_lease_once_without_new_phase(
    loop: Loop, operation: str, invalid: str
) -> None:
    loop.next()
    loop.pause(True)
    data = loop._load()
    if invalid == "future":
        data["active"]["started_at"] = (NOW + timedelta(days=30)).isoformat()
        data["active"]["expires_at"] = (NOW + timedelta(days=30, minutes=10)).isoformat()
    elif invalid == "overlong":
        data["active"]["expires_at"] = (NOW + timedelta(days=30)).isoformat()
    atomic_json(loop.path, data)
    recovered = Loop(
        loop.maw,
        provider=loop.provider,
        now=NOW + timedelta(hours=5) if invalid == "expired" else NOW,
    )
    before = data["phases_today"]
    if operation == "pause":
        recovered.pause(True)
    else:
        getattr(recovered, operation)()
    saved = recovered._load()
    assert saved["active"] is None and saved["paused"]
    assert saved["phases_today"] == before
    failures = [row for row in saved["history"] if row["result"] == "fail"]
    assert len(failures) == 1 and "interrupted" in failures[0]["note"]
    recovered.next()
    assert len([row for row in recovered._load()["history"] if row["result"] == "fail"]) == 1


def test_live_lease_cannot_be_acknowledged(loop: Loop) -> None:
    loop.next()
    with pytest.raises(CrabError, match="phase is leased"):
        loop.acknowledge()
