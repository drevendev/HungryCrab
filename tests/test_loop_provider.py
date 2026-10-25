from __future__ import annotations

import json
from typing import Any

import pytest

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.loop import merge_ci_state
from hungry_crab.loop_config import RequiredCheck
from hungry_crab.loop_provider import GitHubLoopProvider

URL = "https://github.com/example/maw/pull/1"
SHA = "a" * 40


def test_publishing_identity_comes_from_the_authenticated_user_endpoint() -> None:
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> str:
        calls.append(args)
        return json.dumps({"login": "loop-publisher", "type": "User"})

    assert GitHubLoopProvider(Slug("example", "maw"), run).identity() == "loop-publisher"
    assert calls == [("api", "user")]


@pytest.mark.parametrize(
    "raw", ["null", "{}", "[]", '{"login":null}', '{"login":""}', '{"login":"a b","type":"User"}']
)
def test_missing_or_malformed_publishing_identity_fails_closed(raw: str) -> None:
    provider = GitHubLoopProvider(Slug("example", "maw"), lambda *_: raw)
    with pytest.raises(CrabError, match="publishing identity"):
        provider.identity()


def test_provider_reads_every_page_counts_marked_artifacts_and_checks_envelopes() -> None:
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> str:
        calls.append(args)
        endpoint = args[-1]
        if endpoint == "user":
            return json.dumps({"login": "publisher", "type": "User"})
        if endpoint.endswith("issues?state=open&per_page=100"):
            return json.dumps(
                [
                    [
                        {"body": "<!-- crab:tests:unit -->", "user": {"login": "publisher"}},
                        {"body": "Unrelated"},
                    ],
                    [
                        {
                            "body": "<!-- crab:loop:x:1:molt -->",
                            "pull_request": {},
                            "user": {"login": "publisher"},
                            "html_url": URL,
                        }
                    ],
                ]
            )
        if endpoint.endswith("check-runs?filter=all&per_page=100"):
            return json.dumps(
                [
                    {"check_runs": [{"id": 1, "head_sha": SHA}]},
                    {"check_runs": [{"id": 2, "head_sha": SHA}]},
                ]
            )
        if endpoint.endswith("/status"):
            return json.dumps({"sha": SHA, "statuses": []})
        return json.dumps(
            {
                "html_url": URL,
                "head": {
                    "sha": SHA,
                    "ref": "crab/tests/unit",
                    "repo": {"full_name": "example/maw"},
                },
            }
        )

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
    payload = [
        [
            {
                "body": "<!-- crab:loop:marker -->",
                "user": {"login": "publisher"},
                "head": {"repo": {"full_name": "example/maw"}},
            }
        ]
        * 2
    ]
    provider = GitHubLoopProvider(
        Slug("example", "maw"),
        lambda *args: json.dumps(
            {"login": "publisher", "type": "User"} if args[-1] == "user" else payload
        ),
    )
    with pytest.raises(CrabError, match="multiple"):
        provider.find_pr("<!-- crab:loop:marker -->")


@pytest.mark.parametrize("conclusion", ["success", "failure", None])
def test_merged_pr_reads_ci_for_its_actual_merge_commit(conclusion: str | None) -> None:
    merge_sha = "b" * 40
    reads: list[str] = []

    def run(*args: str) -> str:
        endpoint = args[-1]
        reads.append(endpoint)
        if "/git/commits/" in endpoint:
            return json.dumps({"sha": endpoint.rsplit("/", 1)[1], "tree": {"sha": "c" * 40}})
        if endpoint.endswith("check-runs?filter=all&per_page=100"):
            sha = endpoint.split("/commits/")[1].split("/")[0]
            checks = (
                []
                if sha == merge_sha and conclusion is None
                else [
                    {
                        "id": 1,
                        "head_sha": sha,
                        "name": "tests",
                        "status": "completed",
                        "conclusion": conclusion if sha == merge_sha else "success",
                    }
                ]
            )
            return json.dumps([{"check_runs": checks}])
        if endpoint.endswith("/status"):
            return json.dumps({"sha": endpoint.split("/")[-2], "statuses": []})
        return json.dumps(
            {
                "html_url": URL,
                "head": {"sha": SHA},
                "merged_at": "today",
                "merge_commit_sha": merge_sha,
            }
        )

    from hungry_crab.loop import ci_state, merge_ci_state

    truth = GitHubLoopProvider(Slug("example", "maw"), run).artifact(URL)
    assert ci_state(truth) == "pass"
    assert (
        merge_ci_state(truth) == {"success": "pass", "failure": "fail", None: "pending"}[conclusion]
    )
    assert any(f"/commits/{merge_sha}/check-runs" in path for path in reads)


def test_provider_refuses_checks_or_status_bound_to_another_commit() -> None:
    for wrong in ("checks", "status"):

        def run(*args: str, wrong: str = wrong) -> str:
            endpoint = args[-1]
            if endpoint.endswith("check-runs?filter=all&per_page=100"):
                return json.dumps(
                    [{"check_runs": [{"head_sha": "b" * 40 if wrong == "checks" else SHA}]}]
                )
            if endpoint.endswith("/status"):
                return json.dumps({"sha": "b" * 40, "statuses": []})
            return json.dumps(
                {
                    "html_url": URL,
                    "head": {
                        "sha": SHA,
                        "ref": "crab/tests/unit",
                        "repo": {"full_name": "example/maw"},
                    },
                }
            )

        with pytest.raises(CrabError, match=r"different commit|invalid commit statuses"):
            GitHubLoopProvider(Slug("example", "maw"), run).artifact(URL)


@pytest.mark.parametrize("conclusion", ["skipped", "failure", "success"])
def test_provider_preserves_app_identity_across_paginated_merge_checks(conclusion: str) -> None:
    merge_sha = "b" * 40

    def run(*args: str) -> str:
        endpoint = args[-1]
        if "/check-runs?" in endpoint:
            assert "filter=all&per_page=100" in endpoint
            sha = endpoint.split("/commits/")[1].split("/")[0]
            expected = {
                "id": 1,
                "name": "tests",
                "head_sha": sha,
                "app": {"id": 15368},
                "status": "completed",
                "conclusion": conclusion if sha == merge_sha else "success",
            }
            return json.dumps(
                [
                    {"check_runs": [expected]},
                    {
                        "check_runs": [
                            {**expected, "id": 2, "app": {"id": 42}, "conclusion": "success"}
                        ]
                    },
                ]
            )
        if endpoint.endswith("/status"):
            return json.dumps({"sha": endpoint.split("/")[-2], "statuses": []})
        return json.dumps(
            {
                "html_url": URL,
                "head": {"sha": SHA},
                "merged_at": "today",
                "merge_commit_sha": merge_sha,
            }
        )

    truth = GitHubLoopProvider(Slug("example", "maw"), run).artifact(URL)
    assert [check["app"]["id"] for check in truth["merge_ci"]["checks"]] == [15368, 42]
    assert (
        merge_ci_state(truth, [RequiredCheck("tests", 15368)])
        == {"skipped": "pending", "failure": "fail", "success": "pass"}[conclusion]
    )
