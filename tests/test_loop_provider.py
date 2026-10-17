from __future__ import annotations

import json
from typing import Any

import pytest

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.loop_provider import GitHubLoopProvider

URL = "https://github.com/example/maw/pull/1"
SHA = "a" * 40


def test_provider_reads_every_page_counts_marked_artifacts_and_checks_envelopes() -> None:
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> str:
        calls.append(args)
        endpoint = args[-1]
        if endpoint.endswith("issues?state=open&per_page=100"):
            return json.dumps(
                [
                    [{"body": "<!-- crab:tests:unit -->"}, {"body": "Unrelated"}],
                    [{"body": "<!-- crab:loop:x:1:molt -->", "pull_request": {}}],
                ]
            )
        if endpoint.endswith("check-runs?per_page=100"):
            return json.dumps([{"check_runs": [{"id": 1}]}, {"check_runs": [{"id": 2}]}])
        if endpoint.endswith("/status"):
            return '{"statuses": []}'
        return json.dumps({"html_url": URL, "head": {"sha": SHA}})

    provider = GitHubLoopProvider(Slug("example", "maw"), run)
    assert provider.counts() == (1, 1)
    assert len(provider.artifact(URL)["checks"]) == 2
    assert all(args[0] == "api" for args in calls)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/other/maw/pull/1",
        URL + "?query=x",
        "http://github.com/example/maw/pull/1",
        "file:///tmp/a",
    ],
)
def test_foreign_or_ambiguous_url_is_rejected_before_provider_read(url: str) -> None:
    def fail(*_: str) -> str:
        pytest.fail("provider called for an untrusted URL")

    provider = GitHubLoopProvider(Slug("example", "maw"), fail)
    with pytest.raises(CrabError, match="URL"):
        provider.artifact(url)


@pytest.mark.parametrize("payload", ["not-json", "{}", "[[] , {}]", "[[null]]"])
def test_malformed_pagination_fails_closed(payload: str) -> None:
    provider = GitHubLoopProvider(Slug("example", "maw"), lambda *_: payload)
    with pytest.raises(CrabError):
        provider.counts()


def test_file_visibility_limit_and_duplicate_markers_refuse_publication() -> None:
    payload: Any = [[{"filename": "a.py"}] * 3000]
    provider = GitHubLoopProvider(Slug("example", "maw"), lambda *_: json.dumps(payload))
    with pytest.raises(CrabError, match="visibility limit"):
        provider.files(URL)
    payload = [[{"body": "<!-- crab:loop:marker -->"}] * 2]
    with pytest.raises(CrabError, match="multiple"):
        provider.find_pr("<!-- crab:loop:marker -->")
