"""Cache layout and target resolution.

Layout (``~/.cache/hungry-crab`` unless ``CRAB_CACHE_DIR`` overrides it)::

    github/<owner>/<repo>/repo/            git clone of the prey (all branches)
    github/<owner>/<repo>/api/             raw GitHub API responses from `crab sniff`
    github/<owner>/<repo>/digests/<sha>/   digest of one commit
    github/<owner>/<repo>/catch.json       what `crab catch` did last time
    maws/<name>-<hash>/digests/<sha>/     digests of local repositories (the maw side)

The clone is shared between commits; digests are addressed by SHA so a repeated digest of the
same commit is served from the cache.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import UsageError

ENV_CACHE_DIR = "CRAB_CACHE_DIR"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+?)(?:\.git)?/?"
    r"(?:[#?].*)?$"
)
_GITHUB_SSH_RE = re.compile(
    r"^(?:ssh://)?git@github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$"
)


def cache_root() -> Path:
    """Return the cache directory, honouring ``CRAB_CACHE_DIR``."""
    override = os.environ.get(ENV_CACHE_DIR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "hungry-crab"


@dataclass(frozen=True)
class Slug:
    """A GitHub repository reference, ``owner/repo``."""

    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"{self.url}.git"

    @classmethod
    def parse(cls, text: str) -> Slug:
        """Accept ``owner/repo``, HTTPS and SSH GitHub URLs."""
        raw = text.strip()
        match = _GITHUB_URL_RE.match(raw) or _GITHUB_SSH_RE.match(raw)
        if match:
            owner, repo = match.group("owner"), match.group("repo")
        else:
            parts = raw.strip("/").split("/")
            if len(parts) != 2:
                raise UsageError(
                    f"cannot parse repository reference {text!r}",
                    hint="use owner/repo or a GitHub URL",
                )
            owner, repo = parts
            repo = repo.removesuffix(".git")
        if not (_NAME_RE.match(owner) and _NAME_RE.match(repo)):
            raise UsageError(f"invalid repository reference {text!r}")
        return cls(owner, repo)


@dataclass(frozen=True)
class PreyPaths:
    """Cache paths for one GitHub repository."""

    root: Path

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def api(self) -> Path:
        return self.root / "api"

    @property
    def digests(self) -> Path:
        return self.root / "digests"

    @property
    def catch_file(self) -> Path:
        return self.root / "catch.json"


@dataclass(frozen=True)
class MawPaths:
    """Cache paths for a local repository (usually the maw)."""

    root: Path

    @property
    def digests(self) -> Path:
        return self.root / "digests"

    @property
    def meals(self) -> Path:
        return self.root / "meals"

    def meal(self, prey_label: str, prey_sha: str) -> Path:
        """Where one meal lives: this maw, that prey, that commit.

        A digest describes one repository; a meal describes a pair. Keeping the comparison
        here rather than inside the prey's digest is what stops two maws eating the same prey
        from overwriting each other's menu.
        """
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", prey_label).strip("-") or "prey"
        return self.meals / f"{name}@{prey_sha}"

    @property
    def ledger_file(self) -> Path:
        return self.root / "ledger.json"


def prey_paths(slug: Slug, root: Path | None = None) -> PreyPaths:
    return PreyPaths((root or cache_root()) / "github" / slug.owner / slug.repo)


def maw_paths(path: Path, root: Path | None = None) -> MawPaths:
    resolved = path.resolve()
    digest = hashlib.sha1(str(resolved).lower().encode("utf-8")).hexdigest()[:10]
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", resolved.name) or "root"
    return MawPaths((root or cache_root()) / "maws" / f"{name}-{digest}")


@dataclass(frozen=True)
class Target:
    """Either a GitHub repository (``slug``) or a local directory (``path``)."""

    slug: Slug | None = None
    path: Path | None = None

    @property
    def is_local(self) -> bool:
        return self.path is not None

    @property
    def label(self) -> str:
        if self.slug is not None:
            return str(self.slug)
        assert self.path is not None
        return self.path.name or str(self.path)


def resolve_target(text: str) -> Target:
    """A directory that exists wins; anything else must parse as a GitHub reference."""
    candidate = Path(text).expanduser()
    if candidate.is_dir():
        return Target(path=candidate.resolve())
    return Target(slug=Slug.parse(text))
