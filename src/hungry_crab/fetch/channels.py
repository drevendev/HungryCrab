"""Bounded discussion, review and CI-run acquisition; raw prose stays untrusted JSON data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from ..cache import Slug
from ..errors import CrabError
from ..typeutil import as_dict, as_list
from .github import GitHubClient
from .gitlab import GitLabClient
from .providers import RepositoryClient
from .test_reports import parse_junit_archive

DISCUSSIONS_QUERY = """query CrabDiscussions(
  $owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount pageInfo {hasNextPage endCursor}
      nodes {number title url createdAt updatedAt isAnswered
        category {name isAnswerable} reactions {totalCount}
        comments {totalCount} }
    }
  }
}"""


def _pages(
    client: RepositoryClient, path: str, limit: int, key: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    for page in range(1, 31):
        separator = "&" if "?" in path else "?"
        raw = client.get(f"{path}{separator}per_page=100&page={page}")
        batch = as_list(as_dict(raw).get(key)) if key else as_list(raw)
        remaining = limit - len(items)
        items.extend(as_dict(item) for item in batch[:remaining])
        total = as_dict(raw).get("total_count") if key else None
        if len(items) >= limit:
            truncated = (
                total > len(items)
                if isinstance(total, int)
                else len(batch) > remaining or len(batch) == 100
            )
            return items, truncated
        if len(batch) < 100:
            return items, False
    return items, True


def discussions(client: RepositoryClient, slug: Slug, limit: int) -> dict[str, Any]:
    if slug.host == "gitlab.com":
        return {"status": "unsupported", "items": [], "provider": "gitlab"}
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    more = False
    for _ in range(120):
        raw = cast(GitHubClient, client).graphql(
            DISCUSSIONS_QUERY,
            {
                "owner": slug.owner,
                "name": slug.repo,
                "first": min(25, limit - len(items)),
                "after": cursor,
            },
        )
        connection = as_dict(as_dict(raw.get("repository")).get("discussions"))
        for entry in map(as_dict, as_list(connection.get("nodes"))):
            items.append(
                {
                    "number": entry.get("number"),
                    "url": entry.get("url"),
                    "category": str(as_dict(entry.get("category")).get("name") or "")[:100],
                    "answered": entry.get("isAnswered") is True,
                    "comments": as_dict(entry.get("comments")).get("totalCount", 0),
                    "reactions": as_dict(entry.get("reactions")).get("totalCount", 0),
                    "updated_at": entry.get("updatedAt"),
                    "title": str(entry.get("title") or "")[:300],
                }
            )
        page = as_dict(connection.get("pageInfo"))
        more = page.get("hasNextPage") is True
        next_cursor = page.get("endCursor")
        if (
            len(items) >= limit
            or not more
            or not isinstance(next_cursor, str)
            or next_cursor == cursor
        ):
            break
        cursor = next_cursor
    return {"status": "available", "items": items[:limit], "truncated": more}


def reviews(client: RepositoryClient, slug: Slug, limit: int) -> dict[str, Any]:
    if isinstance(client, GitLabClient):
        mrs, more = _pages(
            client, f"{client.project(slug)}/merge_requests?scope=all&state=all", min(limit, 30)
        )
        items: list[dict[str, Any]] = []
        for mr in mrs:
            threads, cut = _pages(
                client, f"{client.project(slug)}/merge_requests/{int(mr['iid'])}/discussions", limit
            )
            more |= cut
            for thread in threads:
                for note in map(as_dict, as_list(thread.get("notes"))):
                    position = as_dict(note.get("position"))
                    if position:
                        items.append(
                            {
                                "id": note.get("id"),
                                "path": position.get("new_path") or position.get("old_path"),
                                "line": position.get("new_line") or position.get("old_line"),
                                "commit_id": position.get("head_sha"),
                                "url": (
                                    f"{slug.url}/-/merge_requests/{mr['iid']}#note_{note.get('id')}"
                                ),
                                "body_excerpt": str(note.get("body") or "")[:600],
                                "updated_at": note.get("updated_at"),
                            }
                        )
                    if len(items) >= limit:
                        return {"status": "available", "items": items[:limit], "truncated": True}
        return {"status": "available", "items": items, "truncated": more}
    entries, cut = _pages(client, f"repos/{slug}/pulls/comments?sort=updated&direction=desc", limit)
    return {
        "status": "available",
        "truncated": cut,
        "items": [
            {
                "id": e.get("id"),
                "path": e.get("path"),
                "line": e.get("line") or e.get("original_line"),
                "commit_id": e.get("commit_id"),
                "url": e.get("html_url"),
                "updated_at": e.get("updated_at"),
                "body_excerpt": str(e.get("body") or "")[:600],
            }
            for e in entries
        ],
    }


def _duration(start: object, finish: object) -> float | None:
    if not isinstance(start, str) or not isinstance(finish, str):
        return None
    try:
        seconds = (
            datetime.fromisoformat(finish.replace("Z", "+00:00"))
            - datetime.fromisoformat(start.replace("Z", "+00:00"))
        ).total_seconds()
    except (ValueError, TypeError):
        return None
    return seconds if seconds >= 0 else None


def runs(client: RepositoryClient, slug: Slug, limit: int) -> dict[str, Any]:
    if isinstance(client, GitLabClient):
        entries, cut = _pages(
            client, f"{client.project(slug)}/pipelines?order_by=id&sort=desc", limit
        )
        items = []
        for entry in entries:
            detail = as_dict(client.get(f"{client.project(slug)}/pipelines/{int(entry['id'])}"))
            jobs, job_cut = _pages(
                client,
                f"{client.project(slug)}/pipelines/{int(entry['id'])}/jobs?include_retried=true",
                300,
            )
            items.append(
                {
                    "id": detail.get("id"),
                    "workflow": detail.get("ref"),
                    "url": detail.get("web_url"),
                    "conclusion": detail.get("status"),
                    "duration_seconds": detail.get("duration"),
                    "attempt": None,
                    "jobs_truncated": job_cut,
                    "jobs": [
                        {
                            "id": j.get("id"),
                            "name": str(j.get("name") or "")[:200],
                            "conclusion": j.get("status"),
                            "duration_seconds": j.get("duration"),
                        }
                        for j in jobs
                    ],
                }
            )
        return {"status": "available", "items": items, "truncated": cut, "provider": "gitlab"}
    entries, cut = _pages(client, f"repos/{slug}/actions/runs", limit, "workflow_runs")
    result: list[dict[str, Any]] = []
    for entry in entries:
        run_id = int(entry["id"])
        attempt = max(1, int(entry.get("run_attempt") or 1))
        current, jobs_cut = _pages(
            client, f"repos/{slug}/actions/runs/{run_id}/attempts/{attempt}/jobs", 300, "jobs"
        )
        previous: list[dict[str, Any]] = []
        if attempt > 1:
            previous, old_cut = _pages(
                client,
                f"repos/{slug}/actions/runs/{run_id}/attempts/{attempt - 1}/jobs",
                300,
                "jobs",
            )
            jobs_cut |= old_cut
        failures = {str(j.get("name")) for j in previous if j.get("conclusion") == "failure"}
        jobs = [
            {
                "id": j.get("id"),
                "name": str(j.get("name") or "")[:200],
                "conclusion": j.get("conclusion"),
                "duration_seconds": _duration(j.get("started_at"), j.get("completed_at")),
                "recovered_on_rerun": j.get("conclusion") == "success"
                and str(j.get("name")) in failures,
            }
            for j in current
        ]
        reports: list[dict[str, Any]] = []
        artifacts, artifacts_cut = _pages(
            client, f"repos/{slug}/actions/runs/{run_id}/artifacts", 20, "artifacts"
        )
        for artifact in artifacts:
            if artifact.get("expired") or not any(
                word in str(artifact.get("name", "")).lower()
                for word in ("junit", "test-results", "surefire")
            ):
                continue
            try:
                parsed = parse_junit_archive(
                    cast(GitHubClient, client).artifact(slug, int(artifact["id"]))
                )
            except CrabError:
                parsed = {"status": "unavailable", "tests": [], "reports": 0}
            reports.append({"artifact_id": artifact.get("id"), **parsed})
        result.append(
            {
                "id": run_id,
                "workflow": str(entry.get("name") or "")[:200],
                "url": entry.get("html_url"),
                "attempt": attempt,
                "conclusion": entry.get("conclusion"),
                "duration_seconds": sum(
                    j["duration_seconds"]
                    for j in jobs
                    if isinstance(j["duration_seconds"], int | float)
                ),
                "duration_kind": "sum-of-job-seconds",
                "jobs": jobs,
                "jobs_truncated": jobs_cut,
                "test_reports": reports,
                "artifacts_truncated": artifacts_cut,
            }
        )
    return {"status": "available", "items": result, "truncated": cut, "provider": "github"}
