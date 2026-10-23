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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import UsageError

ENV_CACHE_DIR = "CRAB_CACHE_DIR"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.-]*$")
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
    host: str = "github.com"

    def __str__(self) -> str:
        prefix = "" if self.host == "github.com" else f"{self.host}/"
        return f"{prefix}{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"{self.url}.git"

    @property
    def wiki_clone_url(self) -> str:
        return f"{self.url}.wiki.git"

    @classmethod
    def parse(cls, text: str) -> Slug:
        """Accept ``owner/repo``, HTTPS and SSH GitHub URLs."""
        raw = text.strip()
        gitlab = re.fullmatch(
            r"(?:https://gitlab\.com/|gitlab\.com/|git@gitlab\.com:|ssh://git@gitlab\.com/)"
            r"([^\s?#]+?)(?:\.git)?/?",
            raw,
        )
        if gitlab:
            parts = gitlab[1].split("/")
            if len(parts) < 2 or any(
                not _REPO_NAME_RE.fullmatch(p) or p in {".", ".."} or p.endswith(".") for p in parts
            ):
                raise UsageError(f"invalid repository reference {text!r}")
            return cls("/".join(parts[:-1]), parts[-1], "gitlab.com")
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
        if (
            not _NAME_RE.fullmatch(owner)
            or owner.endswith(".")
            or not _REPO_NAME_RE.fullmatch(repo)
            or repo in {".", ".."}
            or repo.endswith(".")
        ):
            raise UsageError(f"invalid repository reference {text!r}")
        return cls(owner, repo)


@dataclass(frozen=True)
class PreyPaths:
    """Cache paths for one supported forge repository."""

    root: Path

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def api(self) -> Path:
        return self.root / "api"

    @property
    def wiki(self) -> Path:
        return self.root / "wiki"

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
    provider = "gitlab" if slug.host == "gitlab.com" else "github"
    return PreyPaths((root or cache_root()) / provider / slug.owner / slug.repo)


def maw_paths(path: Path, root: Path | None = None) -> MawPaths:
    resolved = path.resolve()
    digest = hashlib.sha1(str(resolved).lower().encode("utf-8")).hexdigest()[:10]
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", resolved.name) or "root"
    return MawPaths((root or cache_root()) / "maws" / f"{name}-{digest}")


@dataclass(frozen=True)
class Target:
    """Either a forge repository (``slug``) or a local directory (``path``)."""

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


def resolve_target(text: str, *, log: Callable[[str], None] | None = None) -> Target:
    """Prefer local directories; never guess GitHub from an explicit missing path."""
    raw = text.strip()
    if not raw:
        raise UsageError("repository target must not be empty")
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return Target(path=candidate.resolve())
    if candidate.exists() or candidate.is_symlink():
        raise UsageError(f"{text!r} is not a directory; the crab eats directories or owner/repo")

    explicit_path = (
        raw.startswith(("./", "../", ".\\", "..\\", "/", "\\", "~"))
        or "\\" in raw
        or re.match(r"^[A-Za-z]:", raw) is not None
    )
    local_name = "/" not in raw and ":" not in raw and "@" not in raw
    if explicit_path or local_name:
        raise UsageError(f"no such directory: {text!r}")

    slug = Slug.parse(text)
    if log is not None and not raw.startswith(
        ("https://", "http://", "github.com/", "gitlab.com/", "git@", "ssh://")
    ):
        log(f"{text} is not a directory here; interpreting as {slug.url.removeprefix('https://')}")
    return Target(slug=slug)
