"""Read-only GitLab.com adapter. Namespaces are encoded as one project identifier.

Responses are normalized at this boundary, so miners do not need forge-specific branches.
Self-hosted forges and GitLab publication are separate capabilities, not silently guessed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..cache import Slug
from ..errors import ExternalCommandError
from ..typeutil import as_dict, as_list

MAX_RESPONSE = 8 * 1024 * 1024


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != "https" or url.hostname != "gitlab.com" or url.port not in {None, 443}:
            raise ExternalCommandError("GitLab API refused a redirect outside gitlab.com")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitLabClient:
    def __init__(self, *, timeout: float = 120.0) -> None:
        self.token = os.environ.get("GITLAB_TOKEN")
        self.timeout = timeout

    @property
    def transport(self) -> str:
        return "gitlab-https"

    @staticmethod
    def project(slug: Slug) -> str:
        return "projects/" + urllib.parse.quote(f"{slug.owner}/{slug.repo}", safe="")

    def get(self, path: str, *, allow_missing: bool = False) -> Any:
        if path.startswith(("/", "http:", "https:")) or ".." in path.split("/"):
            raise ExternalCommandError("invalid GitLab API path")
        headers = {"Accept": "application/json", "User-Agent": "hungry-crab"}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        request = urllib.request.Request(f"https://gitlab.com/api/v4/{path}", headers=headers)
        try:
            with urllib.request.build_opener(_SameHostRedirect()).open(
                request, timeout=self.timeout
            ) as response:
                body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise ExternalCommandError("GitLab API response exceeds 8 MiB")
            return json.loads(body.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if allow_missing and exc.code == 404:
                return None
            raise ExternalCommandError(
                f"GitLab API request failed (HTTP {exc.code})",
                hint="set GITLAB_TOKEN with read_api permission for protected provider channels"
                if exc.code in {401, 403}
                else None,
            ) from exc
        except (OSError, ValueError) as exc:
            raise ExternalCommandError("GitLab API returned an unreadable response") from exc

    def repo(self, slug: Slug) -> dict[str, Any]:
        raw = as_dict(self.get(f"{self.project(slug)}?statistics=true&license=true"))
        license_data = as_dict(raw.get("license"))
        return {
            "full_name": f"{slug.owner}/{slug.repo}",
            "html_url": slug.url,
            "description": raw.get("description"),
            "default_branch": raw.get("default_branch"),
            "stargazers_count": raw.get("star_count"),
            "forks_count": raw.get("forks_count"),
            "open_issues_count": raw.get("open_issues_count"),
            "archived": raw.get("archived"),
            "fork": bool(raw.get("forked_from_project")),
            "created_at": raw.get("created_at"),
            "pushed_at": raw.get("last_activity_at"),
            "topics": raw.get("topics", []),
            "has_wiki": raw.get("wiki_enabled", False),
            "has_discussions": False,
            "size": int(as_dict(raw.get("statistics")).get("repository_size") or 0) // 1024,
            "size_available": isinstance(
                as_dict(raw.get("statistics")).get("repository_size"), int
            ),
            "license": {"spdx_id": license_data.get("key"), "name": license_data.get("name")},
            "provider": "gitlab",
        }

    def languages(self, slug: Slug) -> dict[str, int]:
        raw = as_dict(self.get(f"{self.project(slug)}/languages", allow_missing=True))
        # GitLab publishes percentages rather than bytes. Scale to preserve relative shares.
        return {k: round(float(v) * 1000) for k, v in raw.items() if isinstance(v, int | float)}

    def issues(self, slug: Slug, limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 31):
            raw = as_list(
                self.get(
                    f"{self.project(slug)}/issues?scope=all&state=all&"
                    f"per_page=100&page={page}&order_by=updated_at&sort=desc"
                )
            )
            for item in raw:
                entry = as_dict(item)
                result.append(
                    {
                        "number": entry.get("iid"),
                        "title": str(entry.get("title") or "")[:300],
                        "state": entry.get("state"),
                        "labels": entry.get("labels", []),
                        "created_at": entry.get("created_at"),
                        "updated_at": entry.get("updated_at"),
                        "closed_at": entry.get("closed_at"),
                        "comments": entry.get("user_notes_count", 0),
                        "reactions": entry.get("upvotes", 0),
                        "url": entry.get("web_url"),
                        "body_excerpt": str(entry.get("description") or "")[:600],
                        "via": "gitlab",
                    }
                )
                if len(result) >= limit:
                    return result
            if len(raw) < 100:
                break
        return result
