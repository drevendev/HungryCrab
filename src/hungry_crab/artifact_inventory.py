"""Bounded A2 attestation inventory traversal, not signature verification.

The caller must supply a reviewed authenticated page transport. A page combines
the status, raw body and all Link field values from one complete response.
An exhausted inventory is not a verifier receipt or evidence of a stable
provider snapshot; admission remains separately blocked.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

from .errors import CrabError

MAX_PAGES = 8
MAX_ITEMS = 100
MAX_BYTES = 8 * 1024 * 1024
MAX_LINK_BYTES = 16 * 1024
PAGE_SIZE = 30
_HOST = "https://api.github.com"
_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LINK = re.compile(r'\s*<([^<>\s]+)>\s*;\s*rel="(next|prev|first|last)"\s*')


@dataclass(frozen=True)
class AttestationPage:
    status: int
    body: bytes
    links: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class InventoryScope:
    repository: str
    repository_id: int
    sha256: str
    predicate_type: str


def _hold(reason: str) -> CrabError:
    return CrabError(f"REMOTE_VERIFICATION_REQUIRED: {reason}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")


def _endpoint(scope: InventoryScope) -> str:
    if (
        not _SLUG.fullmatch(scope.repository)
        or any(part in {".", ".."} for part in scope.repository.split("/"))
        or type(scope.repository_id) is not int
        or scope.repository_id <= 0
        or not _SHA256.fullmatch(scope.sha256)
        or not scope.predicate_type.strip()
        or len(scope.predicate_type) > 512
    ):
        raise _hold("invalid inventory scope")
    return f"/repos/{scope.repository}/attestations/sha256:{scope.sha256}"


def _query(scope: InventoryScope, cursor: str | None = None) -> dict[str, str]:
    params = {"per_page": str(PAGE_SIZE), "predicate_type": scope.predicate_type}
    if cursor is not None:
        params["after"] = cursor
    return params


def _next_cursor(links: tuple[str, ...], scope: InventoryScope, endpoint: str) -> str | None:
    if sum(len(value.encode("utf-8")) for value in links) > MAX_LINK_BYTES:
        raise _hold("oversized Link fields")
    relations: set[str] = set()
    cursor = None
    for field in links:
        # GitHub emits comma-separated angle-bracket links. Do not silently
        # ignore an unsupported syntax, since it could hide another next link.
        for part in re.split(r",(?=\s*<)", field):
            match = _LINK.fullmatch(part)
            if match is None:
                raise _hold("malformed Link field")
            url, relation = match.groups()
            if relation in relations:
                raise _hold("duplicate pagination relation")
            relations.add(relation)
            try:
                parsed = urlsplit(url)
                pairs = parse_qsl(
                    parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4
                )
            except ValueError as exc:
                raise _hold("invalid pagination URL") from exc
            params = dict(pairs)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "api.github.com"
                or parsed.path != endpoint
                or parsed.fragment
                or len(params) != len(pairs)
                or any(params.get(k) != v for k, v in _query(scope).items())
                or set(params) - {"per_page", "predicate_type", "after", "before"}
            ):
                raise _hold("pagination scope changed")
            if relation == "next":
                cursor = params.get("after")
                if not cursor or "before" in params or len(cursor) > 2048:
                    raise _hold("invalid next cursor")
    return cursor


def read_inventory(
    scope: InventoryScope, fetch_page: Callable[[str, int], AttestationPage]
) -> tuple[bytes, ...]:
    """Return exact canonicalized inline bundles only after cursor exhaustion.

    The transport receives a rebuilt fixed-host URL and a remaining body-byte
    budget; it must never follow redirects or fetch bundle URLs. A URL-only
    provider schema is deliberately unsupported until separately reviewed.
    No partial inventory is returned on any error.
    """
    endpoint = _endpoint(scope)
    seen_cursors: set[str] = set()
    seen_bundles: set[str] = set()
    bundles: list[bytes] = []
    remaining = MAX_BYTES
    cursor = None
    for _ in range(MAX_PAGES):
        if remaining <= 0:
            raise _hold("inventory byte cap exhausted")
        url = f"{_HOST}{endpoint}?{urlencode(_query(scope, cursor))}"
        page = fetch_page(url, remaining)
        if page.status != 200 or page.complete is not True or len(page.body) > remaining:
            raise _hold("incomplete or unsuccessful inventory page")
        remaining -= len(page.body)
        try:
            data = json.loads(
                page.body.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (ValueError, RecursionError) as exc:
            raise _hold("malformed inventory JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("attestations"), list):
            raise _hold("unsupported inventory response")
        entries = data["attestations"]
        if len(entries) > PAGE_SIZE:
            raise _hold("provider page exceeds requested size")
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or type(entry.get("repository_id")) is not int
                or entry["repository_id"] != scope.repository_id
                or not isinstance(entry.get("bundle"), dict)
                or not entry["bundle"]
            ):
                raise _hold("unsupported bundle or repository identity mismatch")
            bundle = json.dumps(
                entry["bundle"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("ascii")
            identity = hashlib.sha256(bundle).hexdigest()
            if identity in seen_bundles:
                raise _hold("duplicate bundle")
            seen_bundles.add(identity)
            bundles.append(bundle)
            if len(bundles) >= MAX_ITEMS:
                raise _hold("inventory item cap reached")
        cursor = _next_cursor(page.links, scope, endpoint)
        if cursor is None:
            return tuple(bundles)
        if cursor in seen_cursors or not entries:
            raise _hold("repeated cursor or empty nonterminal page")
        seen_cursors.add(cursor)
    raise _hold("inventory page cap reached before exhaustion")
