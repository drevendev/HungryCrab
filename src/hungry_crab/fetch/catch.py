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
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..cache import Slug, prey_paths
from ..errors import ExternalCommandError, UsageError
from . import channels
from .git import GitRunner
from .issues import fetch_issues, write_issues
from .providers import RepositoryClient, client_for

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
    wiki: bool = True
    discussions: int = 0
    reviews: int = 0
    runs: int = 0


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
    wiki: dict[str, object] | None = None
    history_window_applied: bool | None = None

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
    github: RepositoryClient | None = None,
    wiki_source_url: str | None = None,
) -> CatchResult:
    """Clone or refresh the prey. ``source_url`` overrides the GitHub URL (used by tests)."""
    opts = options or CatchOptions()
    # Validate before changing any cache. A bad date used to delete a valid --force clone.
    clone_args = clone_arguments(opts, now=now)
    if any(not 0 <= n <= 3000 for n in (opts.issues, opts.discussions, opts.reviews, opts.runs)):
        raise UsageError("--issues, --discussions, --reviews and --runs must be in 0..3000")
    paths = prey_paths(slug, cache_root)
    paths.root.mkdir(parents=True, exist_ok=True)
    repo_dir = paths.repo
    url = source_url or slug.clone_url
    client = github or client_for(slug)
    token = client.token if source_url is None else None
    auth_host = slug.host

    updated = False
    previous: dict[str, object] = {}
    if paths.catch_file.is_file():
        try:
            value = json.loads(paths.catch_file.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                previous = value
        except (OSError, ValueError):
            pass
    policy_changed = previous.get("clone_policy") != clone_args
    history_window_applied = True if opts.since else None
    if (repo_dir / ".git").exists() and not opts.force and not policy_changed:
        log(f"refreshing {slug} in {repo_dir}")
        git = GitRunner(repo_dir, github_token=token, auth_host=auth_host)
        original_fetch = git.try_run("config", "--get-all", "remote.origin.fetch")
        retargeted = False
        try:
            # Probe before mutating the cache. Fetch alone never updates origin/HEAD.
            branch = git.remote_default_branch()
            retargeted = git.retarget_single_branch_fetch(branch)
            fetch_args = ["fetch", "--quiet", "--all", "--prune", "--force"]
            if opts.since:
                fetch_args += [f"--shallow-since={parse_since(opts.since, now=now).isoformat()}"]
            elif opts.shallow:
                fetch_args += ["--depth=1"]
            else:
                fetch_args += ["--tags"]
            try:
                git.run(*fetch_args)
            except ExternalCommandError as exc:
                if opts.shallow and opts.since and "no commits selected" in exc.message.lower():
                    git.run("fetch", "--quiet", "--all", "--prune", "--force", "--depth=1")
                    history_window_applied = False
                    log("history window has no commits; refreshed a depth-1 tree snapshot instead")
                else:
                    raise
            if not git.ok("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"):
                raise ExternalCommandError(f"remote default branch {branch!r} was not fetched")
            if opts.shallow and opts.since and history_window_applied:
                newest = datetime.fromtimestamp(
                    int(git.run("show", "-s", "--format=%ct", f"origin/{branch}").strip()), UTC
                ).date()
                # A retargeted fetch can succeed with no selected commits when the old cache
                # already holds the history. Keep the same bounded snapshot contract as clone.
                if newest < parse_since(opts.since, now=now):
                    git.run("fetch", "--quiet", "--all", "--prune", "--force", "--depth=1")
                    history_window_applied = False
                    log("history window has no commits; refreshed a depth-1 tree snapshot instead")
            git.run("checkout", "--quiet", "-B", branch, f"origin/{branch}")
        except ExternalCommandError as exc:
            if retargeted and original_fetch is not None:
                git.run("config", "--replace-all", "remote.origin.fetch", original_fetch.strip())
            raise ExternalCommandError(
                f"could not refresh {slug}: {exc.message}",
                hint="the previous cached tree and catch record were preserved; check the remote "
                "and retry crab catch",
            ) from exc
        git.run("symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")
        updated = True
    else:
        log(f"cloning {url} into {repo_dir}")
        try:
            _clone_replace(repo_dir, url, clone_args, token=token, auth_host=auth_host)
        except ExternalCommandError as exc:
            if opts.shallow and opts.since and "no commits selected" in exc.message.lower():
                log("history window has no commits; catching a depth-1 tree snapshot instead")
                _clone_replace(
                    repo_dir,
                    url,
                    clone_arguments(CatchOptions(shallow=True)),
                    token=token,
                    auth_host=auth_host,
                )
                history_window_applied = False
            else:
                raise
        git = GitRunner(repo_dir)

    wiki_info: dict[str, object] = {"status": "disabled", "sha": None}
    if opts.wiki and (source_url is None or wiki_source_url is not None):
        wiki_info = catch_wiki(
            paths.wiki,
            wiki_source_url or slug.wiki_clone_url,
            log=log,
            token=token,
            auth_host=auth_host,
        )

    issues_fetched = 0
    if opts.issues > 0:
        items = fetch_issues(client, slug, limit=opts.issues, log=log)
        write_issues(paths.api / "issues.jsonl", items)
        issues_fetched = len(items)

    for name in ("discussions", "reviews", "runs"):
        limit = getattr(opts, name)
        if limit:
            data = getattr(channels, name)(client, slug, limit)
            paths.api.mkdir(parents=True, exist_ok=True)
            temporary = paths.api / f".{name}-{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                temporary.replace(paths.api / f"{name}.json")
            finally:
                temporary.unlink(missing_ok=True)

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
        wiki=wiki_info,
        history_window_applied=history_window_applied,
    )
    recorded = {**result.to_dict(), "clone_policy": clone_args}
    paths.catch_file.write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8")
    return result


def _clone_replace(
    destination: Path,
    url: str,
    args: list[str],
    *,
    token: str | None = None,
    auth_host: str = "github.com",
) -> None:
    """Publish only a complete clone, preserving the last valid tree on failure."""
    staged = destination.with_name(f".{destination.name}-{uuid.uuid4().hex}")
    backup = destination.with_name(f".{destination.name}-old-{uuid.uuid4().hex}")
    try:
        GitRunner(destination.parent, timeout=3600, github_token=token, auth_host=auth_host).run(
            *args, url, str(staged)
        )
        if not GitRunner(staged).has_commits():
            raise ExternalCommandError(f"{url} has no commits to digest")
        if destination.exists():
            destination.rename(backup)
        try:
            staged.rename(destination)
        except OSError:
            if backup.exists():
                backup.rename(destination)
            raise
    finally:
        if staged.exists():
            rmtree_force(staged)
    if backup.exists():
        rmtree_force(backup)


def catch_wiki(
    destination: Path,
    url: str,
    *,
    log: Callable[[str], None] = _noop,
    token: str | None = None,
    auth_host: str = "github.com",
) -> dict[str, object]:
    """Wikis are independent Git repositories. An uninitialised wiki is ordinary absence."""
    git = GitRunner(destination.parent, timeout=120, github_token=token, auth_host=auth_host)
    try:
        remote = git.run("ls-remote", url, "HEAD")
    except ExternalCommandError as exc:
        if (
            "not found" in exc.message.lower()
            or "does not appear to be a git repository" in exc.message.lower()
        ):
            log("wiki: not available")
            return {"status": "missing", "sha": None, "url": url}
        raise
    if not remote.strip():
        return {"status": "missing", "sha": None, "url": url}
    remote_sha = remote.split()[0]
    current = GitRunner(destination)
    if (
        not (destination / ".git").exists()
        or not current.has_commits()
        or current.head_sha() != remote_sha
    ):
        log("catching wiki (depth 1)")
        _clone_replace(
            destination,
            url,
            ["clone", "--quiet", "--depth", "1", "--single-branch"],
            token=token,
            auth_host=auth_host,
        )
    return {"status": "available", "sha": GitRunner(destination).head_sha(), "url": url}
