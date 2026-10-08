"""Provider selection at the acquisition boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..cache import Slug
from .github import GitHubClient
from .gitlab import GitLabClient


class RepositoryClient(Protocol):
    token: str | None

    @property
    def transport(self) -> str: ...

    def repo(self, slug: Slug) -> dict[str, Any]: ...
    def languages(self, slug: Slug) -> dict[str, int]: ...
    def get(self, path: str, *, allow_missing: bool = False) -> Any: ...


def client_for(
    slug: Slug, *, prefer_gh: bool = True, cache_dir: Path | None = None
) -> RepositoryClient:
    if slug.host == "gitlab.com":
        return GitLabClient()
    return GitHubClient(prefer_gh=prefer_gh, cache_dir=cache_dir)
