"""Synthetic controls for A2 inventory pagination; no live network or verifier."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
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


@pytest.mark.parametrize("encoded", ["%", "%A", "%ZZ", "%FF", "%C3%28", "%ED%A0%80"])
def test_lossy_cursor_decoding_refused(encoded: str) -> None:
    link = _link("placeholder").replace("after=placeholder", f"after={encoded}")
    fetch, urls = _fetcher(_page([_entry(1)], link))
    with pytest.raises(CrabError, match="invalid pagination URL"):
        inventory.read_inventory(SCOPE, fetch)
    assert len(urls) == 1


@pytest.mark.parametrize("control", ["\x00", "\x01", "\x1f", "\x7f", "\r", "\n"])
@pytest.mark.parametrize("location", ["prefix", "cursor", "field"])
def test_raw_link_control_refused(control: str, location: str) -> None:
    link = _link("placeholder")
    if location == "prefix":
        link = link.replace("<https:", f"<{control}https:")
    elif location == "cursor":
        link = link.replace("after=placeholder", f"after=a{control}b")
    else:
        link = control + link
    fetch, urls = _fetcher(_page([_entry(1)], link))
    with pytest.raises(CrabError, match="invalid pagination URL"):
        inventory.read_inventory(SCOPE, fetch)
    assert len(urls) == 1


def test_link_horizontal_tab_whitespace_allowed() -> None:
    fetch, urls = _fetcher(_page([_entry(1)], "\t" + _link("next") + "\t"), _page([]))
    assert inventory.read_inventory(SCOPE, fetch) == (b'{"example":1}',)
    assert len(urls) == 2


@pytest.mark.parametrize("cursor", ["%FF", "\u00e9", "\ufffd", "\x00", "\x1f", "\x7f"])
def test_valid_encoded_cursor_round_trip(cursor: str) -> None:
    fetch, urls = _fetcher(_page([_entry(1)], _link(cursor)), _page([_entry(2)]))
    assert inventory.read_inventory(SCOPE, fetch) == (b'{"example":1}', b'{"example":2}')
    assert len(urls) == 2
    assert parse_qs(urlsplit(urls[1]).query)["after"] == [cursor]


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


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_number_refused(number: str) -> None:
    body = ('{"attestations":[{"repository_id":123,"bundle":{"value":' + number + "}}]}").encode()
    fetch, _ = _fetcher(inventory.AttestationPage(200, body, (), True))
    with pytest.raises(CrabError, match="malformed inventory JSON"):
        inventory.read_inventory(SCOPE, fetch)


@pytest.mark.parametrize("number", ["9007199254740993.0", "1e-999", "0.5", "1e0", "-0.0"])
def test_fractional_or_exponent_token_refused_without_partial_result(number: str) -> None:
    body = ('{"attestations":[{"repository_id":123,"bundle":{"value":' + number + "}}]").encode()
    fetch, urls = _fetcher(
        _page([_entry(1)], _link("next")), inventory.AttestationPage(200, body, (), True)
    )
    with pytest.raises(CrabError, match="malformed inventory JSON"):
        inventory.read_inventory(SCOPE, fetch)
    assert len(urls) == 2


def test_large_integer_value_preserved() -> None:
    fetch, _ = _fetcher(_page([_entry(9007199254740993)]))
    assert inventory.read_inventory(SCOPE, fetch) == (b'{"example":9007199254740993}',)


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


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), float("-inf"), 10.0, 9.0])
def test_invalid_or_expired_deadline_never_fetches(
    monkeypatch: pytest.MonkeyPatch, deadline: float
) -> None:
    monkeypatch.setattr(inventory, "monotonic", lambda: 10.0)
    fetch, urls = _fetcher(_page([]))
    with pytest.raises(CrabError, match="inventory deadline"):
        inventory.read_inventory(SCOPE, fetch, deadline=deadline)
    assert urls == []


@pytest.mark.parametrize("late_page", [1, 2])
def test_late_page_rejected_without_partial_inventory(
    monkeypatch: pytest.MonkeyPatch, late_page: int
) -> None:
    now = 10.0
    monkeypatch.setattr(inventory, "monotonic", lambda: now)
    fetch, urls = _fetcher(_page([_entry(1)], _link("next")), _page([_entry(2)]))

    def delayed_fetch(url: str, limit: int) -> inventory.AttestationPage:
        nonlocal now
        page = fetch(url, limit)
        if len(urls) == late_page:
            now = 12.0
        return page

    with pytest.raises(CrabError, match="inventory deadline expired"):
        inventory.read_inventory(SCOPE, delayed_fetch, deadline=12.0)
    assert len(urls) == late_page


def test_shared_deadline_accepts_timely_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 10.0
    monkeypatch.setattr(inventory, "monotonic", lambda: now)
    fetch, urls = _fetcher(_page([_entry(1)], _link("next")), _page([_entry(2)]))

    def timed_fetch(url: str, limit: int) -> inventory.AttestationPage:
        nonlocal now
        now += 0.5
        return fetch(url, limit)

    assert inventory.read_inventory(SCOPE, timed_fetch, deadline=12.0) == (
        b'{"example":1}',
        b'{"example":2}',
    )
    assert len(urls) == 2


def test_deadline_includes_link_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 10.0
    monkeypatch.setattr(inventory, "monotonic", lambda: now)
    original = inventory._next_cursor

    def delayed_cursor(
        links: tuple[str, ...], scope: inventory.InventoryScope, endpoint: str
    ) -> str | None:
        nonlocal now
        cursor = original(links, scope, endpoint)
        now = 12.0
        return cursor

    monkeypatch.setattr(inventory, "_next_cursor", delayed_cursor)
    fetch, urls = _fetcher(_page([_entry(1)]))
    with pytest.raises(CrabError, match="inventory deadline expired"):
        inventory.read_inventory(SCOPE, fetch, deadline=12.0)
    assert len(urls) == 1


def test_deadline_stops_before_processing_next_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 10.0
    serialized: list[object] = []
    fetch, urls = _fetcher(_page([_entry(1), _entry(2)], _link("next")))

    def delayed_dumps(
        bundle: object,
        *,
        sort_keys: bool,
        separators: tuple[str, str],
        ensure_ascii: bool,
    ) -> str:
        nonlocal now
        serialized.append(bundle)
        now = 12.0
        return json.dumps(
            bundle, sort_keys=sort_keys, separators=separators, ensure_ascii=ensure_ascii
        )

    monkeypatch.setattr(inventory, "monotonic", lambda: now)
    monkeypatch.setattr(inventory, "json", SimpleNamespace(loads=json.loads, dumps=delayed_dumps))
    with pytest.raises(CrabError, match="inventory deadline expired"):
        inventory.read_inventory(SCOPE, fetch, deadline=12.0)
    assert serialized == [{"example": 1}]
    assert len(urls) == 1


def test_no_deadline_does_not_read_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_clock() -> float:
        pytest.fail("legacy inventory traversal must not read the clock")

    monkeypatch.setattr(inventory, "monotonic", unexpected_clock)
    fetch, urls = _fetcher(_page([_entry(1)]))
    assert inventory.read_inventory(SCOPE, fetch) == (b'{"example":1}',)
    assert len(urls) == 1
