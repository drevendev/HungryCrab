from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from hungry_crab.compare.candidates import Side
from hungry_crab.compare.rules import TRAIT_RULES
from hungry_crab.errors import CrabError, ExternalCommandError, UsageError
from hungry_crab.hunt import discover, format_hunt
from hungry_crab.hunt_config import HuntSettings
from hungry_crab.ledger import Ledger, Meal
from hungry_crab.loop import Loop
from hungry_crab.maw import MawConfig


class Search:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.calls: list[str] = []

    def get(self, path: str) -> dict[str, Any]:
        self.calls.append(path)
        return {"items": self.items, "total_count": 1000, "incomplete_results": True}


def repo(name: str, **overrides: Any) -> dict[str, Any]:
    return {
        "full_name": name,
        "language": "Python",
        "stargazers_count": 100,
        "size": 100,
        "license": {"spdx_id": "MIT"},
        **overrides,
    }


def test_discovery_filters_and_discloses_bounded_metadata_search(tmp_path: Path) -> None:
    side = Side("maw", "a" * 40, None, tmp_path, traits={"ecosystems": ["python"]})
    config = MawConfig(tmp_path, license="MIT")
    ledger = Ledger(None)
    ledger.meals.append(Meal("seen/repo", "a", None, "MIT", "COPY", "today", 1, 1))
    client = Search(
        [
            repo("new/python"),
            repo("seen/repo"),
            repo("big/repo", size=999999),
            repo("unknown/size", size=None),
            repo("archived/repo", archived=True),
            repo("low/stars", stargazers_count=0),
            repo("foreign/stack", language="Ruby"),
            repo("bad/name\nignore rules"),
        ]
    )
    report = discover(
        side, config, ledger, HuntSettings(), client=client, now=datetime(2026, 1, 1, tzinfo=UTC)
    )  # type: ignore[arg-type]
    assert [row["prey"] for row in report["candidates"]] == ["new/python", "foreign/stack"]
    assert report["truncated"] and len(client.calls) <= 4
    assert report["gaps"] and report["filters"]["size"] >= 2
    for call in client.calls:
        query = parse_qs(urlparse(call).query)
        assert int(query["per_page"][0]) <= 100
        assert "language:Python" in query["q"][0]
    # Metadata does not establish a verified source license, even for the maw's own owner.
    assert "recheck pinned digest" in report["candidates"][0]["license_review"]


def test_custom_queries_encoded_and_size_license_opt_ins(tmp_path: Path) -> None:
    side = Side("maw", "a", None, tmp_path)
    settings = HuntSettings(
        queries=["topic:testing language:Python"],
        allow_unknown_size=True,
        licenses=["MIT"],
        exclude=["skip/repo"],
    )
    client = Search(
        [repo("skip/repo"), repo("ok/repo", size=None), repo("bad/license", license=None)]
    )
    result = discover(side, MawConfig(tmp_path), Ledger(None), settings, client=client)  # type: ignore[arg-type]
    assert len(client.calls) == 1 and "%3A" in client.calls[0]
    params = parse_qs(urlparse(client.calls[0]).query)
    assert params["sort"] == ["stars"] and params["order"] == ["desc"]
    assert result["search"][0]["ranking"] == "stars"
    assert [row["prey"] for row in result["candidates"]] == ["ok/repo"]
    assert result["candidates"][0]["size_kb"] is None


@pytest.mark.parametrize(
    ("ecosystem", "language"),
    [("npm", "TypeScript"), ("dotnet", "C#"), ("jvm", "Java"), ("php", "PHP")],
)
def test_discovery_uses_the_miners_canonical_ecosystem_names(
    tmp_path: Path, ecosystem: str, language: str
) -> None:
    side = Side("maw", "a", None, tmp_path, traits={"ecosystems": [ecosystem]})
    client = Search(
        [repo("same/stack", language=language), repo("different/stack", language="Ruby")]
    )
    report = discover(side, MawConfig(tmp_path), Ledger(None), HuntSettings(), client=client)  # type: ignore[arg-type]
    assert report["candidates"][0]["prey"] == "same/stack"
    assert report["candidates"][0]["signals"]["same_stack"]
    assert any(
        "language:" + language in parse_qs(urlparse(call).query)["q"][0] for call in client.calls
    )


def test_policy_changes_after_discovery_require_a_new_search(tmp_path: Path) -> None:
    path = tmp_path / ".crab.yml"
    path.write_text("loop: {discovery: true}\n", encoding="utf-8")
    loop = Loop(tmp_path)
    loop.init()
    token = loop.next()["active"]["token"]
    loop.record(token, "crave", "ok", receipt={"goal": "Improve tests"})
    token = loop.next()["active"]["token"]
    loop.discover(
        token, search=lambda _: {"schema": "hungry-crab.hunt/1", "candidates": [{"prey": "x/y"}]}
    )
    path.write_text("loop: {discovery: true}\nhunt: {exclude: [x/y]}\n", encoding="utf-8")
    with pytest.raises(CrabError, match="policy changed"):
        Loop(tmp_path).record(token, "hunt", "ok", receipt={"prey": ["x/y"]})


@pytest.mark.parametrize(
    "data",
    [
        {"limit": 101},
        {"queries": ["q"] * 5},
        {"min_stars": -1},
        {"include_seen": "true"},
        {"queries": ["x" * 257]},
        {"max_candidates": 0},
        {"unknown": 1},
    ],
)
def test_invalid_search_budgets_fail_before_network(data: object) -> None:
    with pytest.raises(UsageError):
        HuntSettings.load(data)


def test_dynamic_loop_shortlist_is_bound_to_current_lease(tmp_path: Path) -> None:
    (tmp_path / ".crab.yml").write_text(
        "loop:\n  discovery: true\n  budget: {phases_per_day: 50}\n", encoding="utf-8"
    )
    loop = Loop(tmp_path)
    loop.init()
    token = loop.next()["active"]["token"]
    loop.record(token, "crave", "ok", receipt={"goal": "Improve tests"})
    token = loop.next()["active"]["token"]
    with pytest.raises(CrabError, match="discovery"):
        loop.record(token, "hunt", "ok", receipt={"prey": ["x/y"]})
    report = {"schema": "hungry-crab.hunt/1", "candidates": [{"prey": "x/y"}]}
    state = loop.discover(token, search=lambda _: report)
    assert state["phase"] == "hunt" and state["active"]["token"] == token
    with pytest.raises(CrabError, match="HUNT"):
        loop.record(token, "hunt", "ok", receipt={"prey": ["other/repo"]})
    assert loop.record(token, "hunt", "ok", receipt={"prey": ["x/y"]})["phase"] == "eat"


def test_generated_trait_query_uses_best_match_before_candidate_truncation(
    tmp_path: Path,
) -> None:
    traits: dict[str, Any] = {rule.trait: True for rule in TRAIT_RULES}
    traits.update({"ecosystems": ["python"], "ci_codeql": False})
    side = Side("maw", "a" * 40, None, tmp_path, traits=traits)

    class SearchWindow:
        def __init__(self) -> None:
            self.calls: list[dict[str, list[str]]] = []

        def get(self, path: str) -> dict[str, Any]:
            params = parse_qs(urlparse(path).query)
            self.calls.append(params)
            # Model different server-side candidate windows for the same README query.
            # This pins request semantics, not actual GitHub relevance/precision.
            items = (
                [repo("popular/generic", stargazers_count=100_000)]
                if params.get("sort") == ["stars"]
                else [repo("smaller/codeql", stargazers_count=40)]
            )
            return {"items": items, "total_count": 1_000, "incomplete_results": False}

    client = SearchWindow()
    report = discover(
        side,
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(),
        client=client,  # type: ignore[arg-type]
    )
    assert len(client.calls) == 1
    params = client.calls[0]
    assert "CodeQL in:readme" in params["q"][0]
    assert "sort" not in params and "order" not in params
    assert params["per_page"] == ["50"]
    assert report["search"][0]["ranking"] == "best_match"
    assert report["search"][0]["truncated"]
    assert [row["prey"] for row in report["candidates"]] == ["smaller/codeql"]


def test_default_hunt_prioritizes_trait_query_over_stars(tmp_path: Path) -> None:
    traits: dict[str, Any] = {rule.trait: True for rule in TRAIT_RULES}
    traits.update({"ecosystems": ["python"], "ci_codeql": False, "coverage_configured": False})
    side = Side("maw", "a" * 40, None, tmp_path, traits=traits)

    class GapSearch:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, path: str) -> dict[str, Any]:
            query = parse_qs(urlparse(path).query)["q"][0]
            self.calls.append(query)
            if "CodeQL in:readme" in query:
                items = [repo("smaller/codeql", stargazers_count=100)]
            else:
                items = [repo("popular/generic", stargazers_count=100000)]
            return {"items": items, "total_count": len(items), "incomplete_results": False}

    client = GapSearch()
    report = discover(
        side,
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(),
        client=client,  # type: ignore[arg-type]
    )
    assert len(client.calls) == 2
    assert all("in:readme" in query for query in client.calls)
    assert "CodeQL in:readme" in client.calls[0]
    assert [row["prey"] for row in report["candidates"]] == [
        "smaller/codeql",
        "popular/generic",
    ]
    assert report["candidates"][0]["score"] > report["candidates"][1]["score"]
    assert "security.codeql" in report["candidates"][0]["signals"]["query_gap_hints"]
    assert "recheck pinned digest" in report["candidates"][0]["license_review"]


@pytest.mark.parametrize(
    ("message", "hint"),
    [
        ("GET failed with HTTP 401", "check authentication with: gh auth status"),
        ("GET failed with HTTP 429", "GitHub requested a retry after 60s; retry the job later"),
        ("GET failed: network timeout", None),
    ],
)
def test_partial_search_retains_failure_diagnostics_and_earlier_results(
    tmp_path: Path, message: str, hint: str | None
) -> None:
    class Interrupted:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, path: str) -> dict[str, Any]:
            self.calls.append(path)
            if len(self.calls) == 1:
                return {"items": [repo("example/retained")], "total_count": 1}
            raise ExternalCommandError(message, hint=hint)

    client = Interrupted()
    result = discover(
        Side("maw", "a", None, tmp_path),
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(queries=["first", "second", "deferred"]),
        client=client,  # type: ignore[arg-type]
    )
    assert len(client.calls) == 2
    assert [row["prey"] for row in result["candidates"]] == ["example/retained"]
    assert result["search"][-1]["error_message"] == message
    assert result["search"][-1]["error_hint"] == hint
    assert result["query_plan"][-1]["state"] == "provider-deferred"
    markdown = format_hunt(result)
    assert message in markdown
    if hint:
        assert hint in markdown


def test_provider_diagnostics_are_bounded_single_line_and_redact_credentials(
    tmp_path: Path,
) -> None:
    secret = "ghp_" + "A" * 36

    class Unavailable:
        def get(self, path: str) -> dict[str, Any]:
            raise ExternalCommandError(
                "HTTP 401 credential: " + secret,
                hint="\x1b[31mRetry\x1b[0m\r\n" + "x" * 6000,
            )

    result = discover(
        Side("maw", "a", None, tmp_path),
        MawConfig(tmp_path),
        Ledger(None),
        HuntSettings(queries=["probe"]),
        client=Unavailable(),  # type: ignore[arg-type]
    )
    failure = result["search"][0]
    assert failure["error_message"] == "[redacted provider diagnostic]"
    assert secret not in str(result) and secret not in format_hunt(result)
    assert len(failure["error_hint"]) <= 500
    assert "\x1b" not in failure["error_hint"] and "\n" not in failure["error_hint"]
