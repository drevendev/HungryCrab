"""Synthetic controls for A2 inventory pagination; no live network or verifier."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from hungry_crab import artifact_inventory as inventory
from hungry_crab.errors import CrabError

SCOPE = inventory.InventoryScope("owner/repo", 123, "a" * 64, "https://example.org/a2")
BASE = f"https://api.github.com/repos/owner/repo/attestations/sha256:{SCOPE.sha256}"
QUERY = {"per_page": "30", "predicate_type": SCOPE.predicate_type}


def _link(cursor: str, *, base: str = BASE, relation: str = "next") -> str:
    return f'<{base}?{urlencode({**QUERY, "after": cursor})}>; rel="{relation}"'


def _entry(number: int, repository_id: int = 123) -> dict[str, object]:
    return {"repository_id": repository_id, "bundle": {"example": number}}


def _page(
    entries: list[dict[str, object]],
    *links: str,
    status: int = 200,
    complete: bool = True,
) -> inventory.AttestationPage:
    return inventory.AttestationPage(
        status, json.dumps({"attestations": entries}).encode(), links, complete
    )


def _fetcher(
    *pages: inventory.AttestationPage,
) -> tuple[Callable[[str, int], inventory.AttestationPage], list[str]]:
    pending = iter(pages)
    urls: list[str] = []

    def fetch(url: str, limit: int) -> inventory.AttestationPage:
        assert 0 < limit <= inventory.MAX_BYTES
        urls.append(url)
        return next(pending)

    return fetch, urls


def test_short_nonterminal_page_uses_cursor_not_item_count() -> None:
    fetch, urls = _fetcher(_page([_entry(1)], _link("a+b/=")), _page([_entry(2)]))
    assert inventory.read_inventory(SCOPE, fetch) == (b'{"example":1}', b'{"example":2}')
    assert len(urls) == 2
    assert parse_qs(urlsplit(urls[1]).query)["after"] == ["a+b/="]


def test_empty_terminal_page() -> None:
    fetch, urls = _fetcher(_page([]))
    assert inventory.read_inventory(SCOPE, fetch) == ()
    assert len(urls) == 1


def test_full_terminal_page() -> None:
    fetch, urls = _fetcher(_page([_entry(n) for n in range(30)]))
    assert len(inventory.read_inventory(SCOPE, fetch)) == 30
    assert len(urls) == 1


@pytest.mark.parametrize(
    "link",
    [
        _link("next", base=BASE.replace("api.github.com", "example.org")),
        _link("next", base=BASE.replace("owner/repo", "owner/other")),
        _link("next", base=BASE.replace(SCOPE.sha256, "b" * 64)),
        _link("next").replace("per_page=30", "per_page=100"),
        _link("next").replace("predicate_type=", "other="),
        _link("next") + ", " + _link("other"),
        _link("next").replace('rel="next"', 'rel="unknown"'),
        _link(""),
        "not a Link header",
    ],
)
def test_reject_bad_continuation_before_fetching(link: str) -> None:
    fetch, urls = _fetcher(_page([_entry(1)], link))
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        inventory.read_inventory(SCOPE, fetch)
    assert len(urls) == 1


@pytest.mark.parametrize(
    "second",
    [
        _page([_entry(2)], status=403),
        _page([_entry(2)], complete=False),
        _page([_entry(2)], _link("next")),
        _page([_entry(1)]),
        _page([_entry(2, repository_id=999)]),
        _page([{"repository_id": 123, "bundle_url": "https://example.org/bundle"}]),
        _page([], _link("other")),
    ],
)
def test_nonterminal_failure_never_returns_partial_inventory(
    second: inventory.AttestationPage,
) -> None:
    fetch, urls = _fetcher(_page([_entry(1)], _link("next")), second)
    with pytest.raises(CrabError, match="REMOTE_VERIFICATION_REQUIRED"):
        inventory.read_inventory(SCOPE, fetch)
    assert len(urls) == 2


def test_duplicate_json_key_refused() -> None:
    fetch, _ = _fetcher(
        inventory.AttestationPage(200, b'{"attestations":[],"attestations":[]}', (), True)
    )
    with pytest.raises(CrabError, match="malformed inventory JSON"):
        inventory.read_inventory(SCOPE, fetch)


def test_page_cap_before_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inventory, "MAX_PAGES", 1)
    fetch, urls = _fetcher(_page([_entry(1)], _link("next")))
    with pytest.raises(CrabError, match="page cap"):
        inventory.read_inventory(SCOPE, fetch)
    assert len(urls) == 1


def test_item_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inventory, "MAX_ITEMS", 2)
    fetch, _ = _fetcher(_page([_entry(1), _entry(2)]))
    with pytest.raises(CrabError, match="item cap"):
        inventory.read_inventory(SCOPE, fetch)


def test_aggregate_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _page([_entry(1)], _link("next"))
    monkeypatch.setattr(inventory, "MAX_BYTES", len(first.body) + 1)
    fetch, urls = _fetcher(first, _page([_entry(2)]))
    with pytest.raises(CrabError, match="incomplete or unsuccessful"):
        inventory.read_inventory(SCOPE, fetch)
    assert len(urls) == 2
