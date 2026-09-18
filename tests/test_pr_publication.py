from __future__ import annotations

from typing import Any

import pytest

from hungry_crab.errors import CrabError
from hungry_crab.pr_publication import (
    GeneratedFile,
    PreparedPullRequest,
    publication_items,
    publish_prepared_pull_request,
)


def _prepared(*, title: str = "feat: carry the nutrient", body: str = "<!-- crab:ci:cache -->") -> PreparedPullRequest:
    return PreparedPullRequest(
        title=title,
        body=body,
        files=(GeneratedFile("generated/cache.yml", "cache: true\n"),),
    )


def test_prepared_pull_request_scans_files_title_and_body() -> None:
    prepared = PreparedPullRequest(
        title="feat: safe title",
        body="<!-- crab:ci:cache -->\nSafe trace body.\n",
        files=(
            GeneratedFile("generated/cache.yml", "cache: true\n"),
            GeneratedFile("generated/tooling.toml", "enabled = true\n"),
        ),
    )

    assert list(publication_items(prepared)) == [
        ("generated/cache.yml", "cache: true\n"),
        ("generated/tooling.toml", "enabled = true\n"),
        ("PR_TITLE", "feat: safe title"),
        ("PR_BODY", "<!-- crab:ci:cache -->\nSafe trace body.\n"),
    ]


@pytest.mark.parametrize(
    ("prepared", "location"),
    [
        (
            PreparedPullRequest(
                title="feat: safe title",
                body="<!-- crab:ci:cache -->",
                files=(
                    GeneratedFile(
                        "generated/settings.yml",
                        "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n",
                    ),
                ),
            ),
            "generated/settings.yml:1 (github-token)",
        ),
        (
            _prepared(title="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
            "PR_TITLE:1 (github-token)",
        ),
        (
            _prepared(body="token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
            "PR_BODY:1 (github-token)",
        ),
    ],
)
def test_secret_hit_blocks_before_any_publication_effect(
    prepared: PreparedPullRequest, location: str
) -> None:
    effects: list[str] = []
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    def publish(_: PreparedPullRequest) -> str:
        effects.extend(["branch", "write", "push", "pull-request"])
        return "https://github.com/example/maw/pull/7"

    with pytest.raises(CrabError) as raised:
        publish_prepared_pull_request(prepared, publish)

    assert effects == []
    assert raised.value.hint == location
    assert secret not in raised.value.message
    assert secret not in (raised.value.hint or "")


def test_clean_prepared_pull_request_reaches_effect_once() -> None:
    prepared = _prepared(body="<!-- crab:ci:cache -->\nTrace: public metadata only.\n")
    effects: list[dict[str, Any]] = []

    def publish(payload: PreparedPullRequest) -> str:
        effects.append({"title": payload.title, "files": len(payload.files)})
        return "https://github.com/example/maw/pull/7"

    url = publish_prepared_pull_request(prepared, publish)

    assert url == "https://github.com/example/maw/pull/7"
    assert effects == [{"title": "feat: carry the nutrient", "files": 1}]
