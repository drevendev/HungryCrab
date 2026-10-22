"""``crab catch``: bring the prey into the local cache.

A plain clone with every branch is the default because the history and branches miners need
it. Giants get ``--since`` (shallow by date, all branches) or ``--shallow`` (default branch,
depth 1, tree-only). Nothing inside the clone is ever executed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..cache import Slug, prey_paths
from ..cache import cache_root as default_cache_root
from ..errors import CrabError, UsageError
from ..fs import is_reparse_point
from .git import GitRunner
from .github import GitHubClient
from .issues import fetch_issues, write_issues

_SINCE_RE = re.compile(r"^(\d+)\s*([dwmy])$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def _noop(_: str) -> None:
    return None


@dataclass(frozen=True)
class CatchOptions:
    shallow: bool = False
    since: str | None = None
    force: bool = False
    issues: int = 0


@dataclass
class CatchResult:
    slug: str
    url: str
    repo_dir: str
    sha: str
    default_branch: str
    shallow: bool
    since: str | None
    updated: bool
    caught_at: str
    issues_fetched: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_since(text: str, *, now: datetime | None = None) -> date:
    """``2y`` / ``6m`` / ``90d`` / ``4w`` or an ISO date."""
    current = now or datetime.now(UTC)
    match = _SINCE_RE.match(text.strip().lower())
    if match:
        days = int(match.group(1)) * _UNIT_DAYS[match.group(2)]
        return (current - timedelta(days=days)).date()
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise UsageError(
            f"cannot parse --since value {text!r}",
            hint="use 2y, 6m, 90d or an ISO date such as 2024-01-01",
        ) from exc


def _require_real_entries(item: Path, *, cache_root: Path) -> os.stat_result:
    """Refuse ``item`` unless every component below ``cache_root`` down to it is a real entry.

    ``cache_root`` and its ancestors are the user's choice and are not inspected: ``~/.cache``
    on a data disk, ``CRAB_CACHE_DIR`` in a junctioned folder or ``/home -> /var/home`` are
    ordinary setups. Below the root everything is the crab's own, so a symlink or a Windows
    reparse point there is refused rather than followed. Returns the ``lstat`` of ``item``.
    """
    try:
        parts = item.absolute().relative_to(cache_root.absolute()).parts
    except ValueError:
        parts = ()
    if not parts or ".." in parts:
        raise CrabError(f"refusing cache deletion of {item}: not inside the cache {cache_root}")
    component = cache_root.absolute()
    for part in parts:
        component /= part
        try:
            info = component.lstat()
        except OSError as exc:
            raise CrabError(f"cannot inspect cache deletion path {component}: {exc}") from exc
        real = stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        if not real or is_reparse_point(info):
            raise CrabError(f"refusing cache deletion through link/reparse path {component}")
    return info


def rmtree_force(path: Path, *, cache_root: Path) -> None:
    """Delete one directory inside ``cache_root`` without changing permissions outside it.

    A failed deletion can leave cache debris. It is safer to report that state than
    to chmod symlink/junction targets or hide a partially removed checkout.
    """
    if not stat.S_ISDIR(_require_real_entries(path, cache_root=cache_root).st_mode):
        raise CrabError(f"refusing cache deletion of {path}: not a directory")

    def retry_readonly(func: Callable[..., object], filename: str, _failure: object) -> None:
        """Repair only the item that failed, after refusing any link/junction component."""
        item = Path(filename)
        if not item.absolute().is_relative_to(path.absolute()):
            raise CrabError(f"refusing cache cleanup outside its root: {item}")
        _require_real_entries(item, cache_root=cache_root)
        try:
            item.chmod(item.lstat().st_mode | stat.S_IWRITE)
            func(filename)
        except OSError as exc:
            raise CrabError(f"cannot remove read-only cache entry {item}: {exc}") from exc

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry_readonly)
        else:
            shutil.rmtree(path, onerror=retry_readonly)
    except OSError as exc:
        raise CrabError(
            f"cannot safely remove cached files in {path}: {exc}",
            hint=(
                "Close programs holding files and inspect what remains. "
                "No recursive chmod traversal was attempted."
            ),
        ) from exc


def clone_arguments(options: CatchOptions, *, now: datetime | None = None) -> list[str]:
    args = ["clone", "--quiet"]
    if options.since:
        args += [f"--shallow-since={parse_since(options.since, now=now).isoformat()}"]
        args += ["--single-branch"] if options.shallow else ["--no-single-branch"]
    elif options.shallow:
        args += ["--depth", "1", "--single-branch"]
    return args


def catch(
    slug: Slug,
    options: CatchOptions | None = None,
    *,
    cache_root: Path | None = None,
    source_url: str | None = None,
    log: Callable[[str], None] = _noop,
    now: datetime | None = None,
    github: GitHubClient | None = None,
) -> CatchResult:
    """Clone or refresh the prey. ``source_url`` overrides the GitHub URL (used by tests)."""
    opts = options or CatchOptions()
    root = cache_root if cache_root is not None else default_cache_root()
    paths = prey_paths(slug, root)
    paths.root.mkdir(parents=True, exist_ok=True)
    repo_dir = paths.repo
    url = source_url or slug.clone_url

    if opts.force and repo_dir.exists():
        log(f"removing previous clone at {repo_dir}")
        rmtree_force(repo_dir, cache_root=root)

    updated = False
    if (repo_dir / ".git").exists():
        log(f"refreshing {slug} in {repo_dir}")
        git = GitRunner(repo_dir)
        git.run("fetch", "--quiet", "--all", "--prune", "--tags", "--force")
        branch = git.default_branch()
        if git.ok("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"):
            git.run("checkout", "--quiet", "-B", branch, f"origin/{branch}")
        updated = True
    else:
        if repo_dir.exists():
            rmtree_force(repo_dir, cache_root=root)
        log(f"cloning {url} into {repo_dir}")
        parent = GitRunner(paths.root, timeout=3600)
        parent.run(*clone_arguments(opts, now=now), url, str(repo_dir))
        git = GitRunner(repo_dir)

    issues_fetched = 0
    if opts.issues > 0:
        client = github or GitHubClient()
        items = fetch_issues(client, slug, limit=opts.issues, log=log)
        write_issues(paths.api / "issues.jsonl", items)
        issues_fetched = len(items)

    result = CatchResult(
        slug=str(slug),
        url=url,
        repo_dir=str(repo_dir),
        sha=git.head_sha(),
        default_branch=git.default_branch(),
        shallow=git.is_shallow(),
        since=opts.since,
        updated=updated,
        caught_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        issues_fetched=issues_fetched,
    )
    paths.catch_file.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    return result
