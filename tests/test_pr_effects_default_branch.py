from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.pr_effects import publish_git_pull_request
from hungry_crab.pr_publication import GeneratedFile, PreparedPullRequest


def test_nutrient_branch_equal_to_provider_default_is_refused_before_fetch_or_push() -> None:
    calls: list[tuple[str, ...]] = []

    class Git:
        def is_repo(self) -> bool:
            return True

        def run(self, *args: str) -> str:
            calls.append(args)
            return ""

    prepared = PreparedPullRequest(
        "feat: nutrient", "Prepared body", (GeneratedFile("app.py", "x=1\n"),)
    )
    branch = "crab/tests/isolation"
    with pytest.raises(CrabError, match="default branch"):
        publish_git_pull_request(
            Slug("example", "maw"), Path(), branch, prepared, run_gh=lambda *_: branch, git=Git()
        )  # type: ignore[arg-type]
    assert calls == [("check-ref-format", "--branch", branch)]
