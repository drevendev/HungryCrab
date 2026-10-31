"""One source-supported hop with independently checked native provider identity."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .attribution import GitSourceReader
from .cache import Slug
from .compare.candidates import _evidence_path
from .discovery_search import SearchBudget, SearchClient, verified_frontier
from .errors import CrabError
from .typeutil import as_dict


def frontier(
    roots: list[dict[str, Any]],
    *,
    budget: SearchBudget,
    clients: dict[str, SearchClient] | None = None,
    maximum: int = 8,
) -> dict[str, Any]:
    """Verify a literal repository reference in a pinned bounded source range.

    The host judges whether the evidenced relationship is useful; the CLI checks
    the actual reference, canonical public identity and a separately resolved pin.
    It never infers a dependency from a package name or follows a second hop.
    """
    clients = dict(clients or {})

    def resolve(root: dict[str, Any]) -> dict[str, Any] | None:
        sha, path = root.get("from_sha"), root.get("path")
        start, end = root.get("start"), root.get("end")
        if (
            not isinstance(sha, str)
            or re.fullmatch(r"[a-f0-9]{40,64}", sha) is None
            or not isinstance(path, str)
            or not _evidence_path(path)
            or type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end <= start + 100
            or not isinstance(root.get("source_repo"), str)
        ):
            return None
        try:
            target = Slug.parse(str(root.get("target", "")))
        except CrabError:
            return None
        reader = GitSourceReader(Path(root["source_repo"]))
        parent, _, name = path.rpartition("/")
        if (reader.entries(sha, parent) or {}).get(name) not in {"100644", "100755"}:
            return None
        text = reader.read(sha, path)
        if text is None or len(text.encode()) > 65_536 or end > len(text.splitlines()):
            return None
        window = "\n".join(text.splitlines()[start - 1 : end])
        # Exact URL token boundary: repo-suffix, another owner or prose about a
        # similarly named package cannot establish this repository reference.
        if re.search(re.escape(target.url) + r"(?:\.git)?(?=[\s\"'<>),\]]|$)", window) is None:
            return None
        provider = "github" if target.host == "github.com" else "gitlab"
        client = clients.get(provider)
        if client is None:
            if provider == "github":
                from .fetch.github import GitHubClient

                client = GitHubClient(retries=0)
            else:
                from .fetch.gitlab import GitLabClient

                client = GitLabClient()
            clients[provider] = client
        endpoint = (
            f"repos/{target.owner}/{target.repo}"
            if provider == "github"
            else "projects/" + quote(f"{target.owner}/{target.repo}", safe="")
        )
        budget.charge()
        try:
            metadata = as_dict(client.get(endpoint))
            native = metadata.get("full_name" if provider == "github" else "path_with_namespace")
            if str(native).casefold() != f"{target.owner}/{target.repo}".casefold():
                return None
            public = (
                metadata.get("private") is False
                if provider == "github"
                else metadata.get("visibility") == "public"
            )
            ref = metadata.get("default_branch")
            if not public or not isinstance(ref, str) or not 0 < len(ref) <= 200:
                return None
            budget.charge()
            revision = as_dict(
                client.get(
                    endpoint
                    + ("/commits/" if provider == "github" else "/repository/commits/")
                    + quote(ref, safe="")
                )
            )
        except CrabError:
            return None
        pin = revision.get("sha" if provider == "github" else "id")
        return {
            "repository": str(target),
            "sha": pin,
            "verified": True,
            "head_only": True,
            "evidence": {
                "sha": sha,
                "path": path,
                "start": start,
                "end": end,
                "reference": target.url,
                "relationship_review": "host-source-supported",
                "target_pin_scope": "resolved-default-ref-only",
            },
        }

    before = budget.used
    result = verified_frontier(roots, resolve, maximum=maximum)
    result["requests"] = budget.used - before
    return result
