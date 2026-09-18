from __future__ import annotations

import json
from typing import Any

from hungry_crab.cache import Slug
from hungry_crab.serve import GhIssueClient

MAW_SLUG = Slug("example", "maw")


class _PagedGh(GhIssueClient):
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.gh = "gh"
        self.timeout = 1.0
        self.token_env = ""
        self.pages = pages
        self.calls: list[tuple[str, ...]] = []

    def _run(self, *args: str) -> str:
        self.calls.append(args)
        return "\n".join(json.dumps(page) for page in self.pages)


def _api_item(number: int, body: str | None, *, pull_request: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "number": number,
        "html_url": f"https://github.com/example/maw/{'pull' if pull_request else 'issues'}/{number}",
        "state": "open",
        "title": f"t{number}",
        "body": body,
    }
    if pull_request:
        item["pull_request"] = {"url": f"https://api.github.com/repos/example/maw/pulls/{number}"}
    return item


def test_list_marked_prs_reads_all_pages_and_ignores_issues() -> None:
    marker = "crab:ci:ci.cache"
    pages = [
        [_api_item(1, f"<!-- {marker} -->\nissue carrier", pull_request=False)],
        [
            _api_item(5, f"> <!-- {marker} -->\nquoted in a PR", pull_request=True),
            _api_item(3, f"<!-- {marker} -->\nserved PR", pull_request=True),
        ],
    ]
    client = _PagedGh(pages)

    found = client.list_marked_prs(MAW_SLUG)

    assert found == {
        marker: {
            "number": 3,
            "url": "https://github.com/example/maw/pull/3",
            "state": "open",
            "title": "t3",
        }
    }
    (call,) = client.calls
    assert call[:2] == ("api", "--paginate")
    assert call[2].startswith("repos/example/maw/issues?state=all&per_page=100")


def test_list_marked_prs_prefers_oldest_direct_marker_after_race() -> None:
    marker = "crab:tests:tests.coverage"
    client = _PagedGh(
        [
            [
                _api_item(20, f"<!-- {marker} -->\nnew duplicate", pull_request=True),
                _api_item(7, f"<!-- {marker} -->\nfirst PR", pull_request=True),
                _api_item(4, f"> <!-- {marker} -->\nquote only", pull_request=True),
            ]
        ]
    )

    found = client.list_marked_prs(MAW_SLUG)

    assert found[marker]["number"] == 7
    assert found[marker]["url"] == "https://github.com/example/maw/pull/7"
