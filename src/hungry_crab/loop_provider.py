"""Provider truth and rejection of failed MOLT proposals; no merge operation exists here."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Protocol

from .cache import Slug
from .errors import CrabError


class LoopProvider(Protocol):
    def counts(self) -> tuple[int, int]: ...

    def artifact(self, url: str) -> dict[str, Any]: ...

    def find_pr(self, marker: str) -> dict[str, Any] | None: ...

    def files(self, url: str) -> list[dict[str, Any]]: ...

    def close_pr(self, url: str) -> None: ...


class GitHubLoopProvider:
    def __init__(self, slug: Slug, run: Callable[..., str]) -> None:
        self.slug = slug
        self.run = run

    def _pages(self, endpoint: str, key: str | None = None) -> list[dict[str, Any]]:
        try:
            pages = json.loads(self.run("api", "--paginate", "--slurp", endpoint))
        except ValueError as exc:
            raise CrabError("loop provider returned invalid JSON") from exc
        if key is not None and isinstance(pages, list):
            pages = [page.get(key) if isinstance(page, dict) else None for page in pages]
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise CrabError("loop provider returned an invalid page list")
        rows = [row for page in pages for row in page]
        if any(not isinstance(row, dict) for row in rows):
            raise CrabError("loop provider returned an invalid artifact")
        return rows

    def _number(self, url: str) -> tuple[str, str]:
        match = re.fullmatch(
            rf"https://github\.com/{re.escape(str(self.slug))}/(issues|pull)/(\d+)", url
        )
        if match is None:
            raise CrabError("loop artifact URL must belong to this maw on GitHub")
        return ("pulls" if match[1] == "pull" else "issues", match[2])

    def artifact(self, url: str) -> dict[str, Any]:
        kind, number = self._number(url)
        try:
            data = json.loads(self.run("api", f"repos/{self.slug}/{kind}/{number}"))
        except ValueError as exc:
            raise CrabError("loop provider returned invalid JSON") from exc
        if not isinstance(data, dict) or data.get("html_url") != url:
            raise CrabError("loop provider returned a different artifact")
        if kind == "pulls":
            head = data.get("head")
            if (
                not isinstance(head, dict)
                or not isinstance(head.get("sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head["sha"])
            ):
                raise CrabError("loop provider returned an invalid PR head")
            sha = head["sha"]
            checks = self._pages(
                f"repos/{self.slug}/commits/{sha}/check-runs?per_page=100", "check_runs"
            )
            data["checks"] = checks
            try:
                status = json.loads(self.run("api", f"repos/{self.slug}/commits/{sha}/status"))
            except ValueError as exc:
                raise CrabError("loop provider returned invalid commit status JSON") from exc
            if not isinstance(status, dict) or not isinstance(status.get("statuses"), list):
                raise CrabError("loop provider returned invalid commit statuses")
            data["statuses"] = status["statuses"]
            if any(not isinstance(row, dict) for row in data["statuses"]):
                raise CrabError("loop provider returned an invalid commit status")
        return data

    def counts(self) -> tuple[int, int]:
        rows = self._pages(f"repos/{self.slug}/issues?state=open&per_page=100")
        issues = prs = 0
        for row in rows:
            if not str(row.get("body") or "").lstrip().startswith("<!-- crab:"):
                continue
            if "pull_request" in row:
                prs += 1
            else:
                issues += 1
        return issues, prs

    def find_pr(self, marker: str) -> dict[str, Any] | None:
        rows = self._pages(f"repos/{self.slug}/pulls?state=all&per_page=100")
        matches = [row for row in rows if str(row.get("body") or "").startswith(marker)]
        if len(matches) > 1:
            raise CrabError("multiple pull requests carry this loop phase marker")
        return self.artifact(matches[0]["html_url"]) if matches else None

    def files(self, url: str) -> list[dict[str, Any]]:
        kind, number = self._number(url)
        if kind != "pulls":
            raise CrabError("loop expected a pull request")
        rows = self._pages(f"repos/{self.slug}/pulls/{number}/files?per_page=100")
        if len(rows) >= 3000:
            raise CrabError("loop refuses a PR at GitHub's changed-file visibility limit")
        return rows

    def close_pr(self, url: str) -> None:
        kind, _ = self._number(url)
        if kind != "pulls":
            raise CrabError("loop rejection requires a pull request")
        self.run("pr", "close", url, "--repo", str(self.slug))
