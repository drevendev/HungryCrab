"""``crab catch``: bring the prey into the local cache.

A plain clone with every branch is the default because the history and branches miners need
it. Giants get ``--since`` (shallow by date, all branches) or ``--shallow`` (default branch,
depth 1, tree-only). Nothing inside the clone is ever executed.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..cache import Slug, prey_paths
from ..errors import UsageError
from .git import GitRunner

_SINCE_RE = re.compile(r"^(\d+)\s*([dwmy])$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def _noop(_: str) -> None:
    return None


@dataclass(frozen=True)
class CatchOptions:
    shallow: bool = False
    since: str | None = None
    force: bool = False


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


def rmtree_force(path: Path) -> None:
    """``shutil.rmtree`` that copes with read-only git objects on Windows."""
    for child in path.rglob("*"):
        try:
            child.chmod(child.stat().st_mode | stat.S_IWRITE)
        except OSError:
            continue
    shutil.rmtree(path)


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
) -> CatchResult:
    """Clone or refresh the prey. ``source_url`` overrides the GitHub URL (used by tests)."""
    opts = options or CatchOptions()
    paths = prey_paths(slug, cache_root)
    paths.root.mkdir(parents=True, exist_ok=True)
    repo_dir = paths.repo
    url = source_url or slug.clone_url

    if opts.force and repo_dir.exists():
        log(f"removing previous clone at {repo_dir}")
        rmtree_force(repo_dir)

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
            rmtree_force(repo_dir)
        log(f"cloning {url} into {repo_dir}")
        parent = GitRunner(paths.root, timeout=3600)
        parent.run(*clone_arguments(opts, now=now), url, str(repo_dir))
        git = GitRunner(repo_dir)

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
    )
    paths.catch_file.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    return result
