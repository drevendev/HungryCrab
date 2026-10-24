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
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..cache import Slug, prey_paths
from ..cache import cache_root as default_cache_root
from ..errors import CrabError, ExternalCommandError, UsageError
from ..fs import is_reparse_point
from . import channels
from .git import GitRunner
from .issues import fetch_issues, write_issues
from .providers import RepositoryClient, client_for

_SINCE_RE = re.compile(r"^(\d+)\s*([dwmy])$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}
# The cache's prey directories by provider, with the host their slugs carry.
_PROVIDER_HOSTS = {"github": "github.com", "gitlab": "gitlab.com"}
# The records that prove a prey directory is the crab's: what a catch and a sniff write.
_OWNERSHIP_RECORDS = (("catch.json",), ("api", "sniff.json"))
# The `status` of the provisional `catch.json` a first catch writes before it clones.
INCOMPLETE = "incomplete"
# Whether chmod can act on a link itself (Windows from 3.13, a few BSDs; never Linux).
_CHMOD_NOFOLLOW = os.chmod in os.supports_follow_symlinks
_LEFT_BEHIND_HINT = (
    "Close programs holding files and inspect what remains. "
    "No recursive chmod traversal was attempted."
)


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
    hint = "use 2y, 6m, 90d or an ISO date such as 2024-01-01"
    match = _SINCE_RE.match(text.strip().lower())
    if match:
        try:
            days = int(match.group(1)) * _UNIT_DAYS[match.group(2)]
            return (current - timedelta(days=days)).date()
        except (ValueError, OverflowError) as exc:
            # on Python 3.11+ int() applies the integer-string digit limit to
            # oversized digit counts; a value that would reach before year 1, or
            # that does not fit a C int, is a usage error like any other
            # unparsable value
            raise UsageError(f"cannot parse --since value {text!r}", hint=hint) from exc
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise UsageError(f"cannot parse --since value {text!r}", hint=hint) from exc


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


def _names_prey(record: Path, slug: str) -> bool:
    """Whether ``record`` is a crab record, a small regular JSON file, about ``slug``."""
    try:
        info = record.lstat()
        if not stat.S_ISREG(info.st_mode) or is_reparse_point(info) or info.st_size > 1 << 20:
            return False
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    named = data.get("slug") if isinstance(data, dict) else None
    # Forge names are case-insensitive, and so is the filesystem the cache may live on.
    return isinstance(named, str) and named.casefold() == slug.casefold()


def _require_crab_owned(path: Path, *, cache_root: Path) -> None:
    """Refuse to delete a prey directory that no record of the crab's own names.

    A cache directory can share its layout with a person's checkouts: ``CRAB_CACHE_DIR`` set
    to a folder of ``github/<owner>/<repo>`` clones is a common convention, and the shape of a
    path proves nothing. Below a provider directory, the directory deleted or a parent of it
    must hold the ``catch.json`` a catch writes or the ``api/sniff.json`` a sniff writes, and
    the record must name that very prey. Other deletions are staging trees the caller made.
    """
    parts = path.absolute().relative_to(cache_root.absolute()).parts
    host = _PROVIDER_HOSTS.get(parts[0])
    if host is None:
        return
    for depth in range(len(parts), 2, -1):
        names = "/".join(parts[1:depth])
        slug = names if host == "github.com" else f"{host}/{names}"
        prey_root = cache_root.absolute().joinpath(*parts[:depth])
        if any(_names_prey(prey_root.joinpath(*record), slug) for record in _OWNERSHIP_RECORDS):
            return
    raise CrabError(
        f"refusing to remove {path}: no catch or sniff record of the crab names it",
        hint="the crab removes only directories it caught or sniffed into its cache; "
        "if this one should go, remove it yourself",
    )


def rmtree_force(path: Path, *, cache_root: Path) -> None:
    """Delete one directory inside ``cache_root`` without changing permissions outside it.

    A failed deletion can leave cache debris. It is safer to report that state than
    to chmod symlink/junction targets or hide a partially removed checkout.
    """
    if not stat.S_ISDIR(_require_real_entries(path, cache_root=cache_root).st_mode):
        raise CrabError(f"refusing cache deletion of {path}: not a directory")
    _require_crab_owned(path, cache_root=cache_root)

    def retry_readonly(func: Callable[..., object], filename: str, _failure: object) -> None:
        """Repair only the item that failed, after refusing any link/junction component."""
        item = Path(filename)
        if not item.absolute().is_relative_to(path.absolute()):
            raise CrabError(f"refusing cache cleanup outside its root: {item}")
        _require_real_entries(item, cache_root=cache_root)
        try:
            mode = item.lstat().st_mode | stat.S_IWRITE
            # The entry may have become a link since it was checked: never change a target.
            if _CHMOD_NOFOLLOW:
                item.chmod(mode, follow_symlinks=False)
            elif sys.platform == "win32":
                # Before 3.13, chmod on Windows sets the attributes of the entry itself.
                item.chmod(mode)
            # Elsewhere chmod would follow a swapped-in link, and removing a file there needs
            # no write permission on it, so the retry goes ahead as it is.
            func(filename)
        except OSError as exc:
            # An open file on Windows ends here: the entry is half removed, and says so.
            raise CrabError(
                f"cannot remove cache entry {item}: {exc.strerror or exc}; "
                f"what is left stays in {path}",
                hint=_LEFT_BEHIND_HINT,
            ) from exc

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry_readonly)
        else:
            shutil.rmtree(path, onerror=retry_readonly)
    except OSError as exc:
        raise CrabError(
            f"cannot safely remove cached files in {path}: {exc}",
            hint=_LEFT_BEHIND_HINT,
        ) from exc


def clone_arguments(options: CatchOptions, *, now: datetime | None = None) -> list[str]:
    args = ["clone", "--quiet"]
    if options.since:
        args += [f"--shallow-since={parse_since(options.since, now=now).isoformat()}"]
        args += ["--single-branch"] if options.shallow else ["--no-single-branch"]
    elif options.shallow:
        args += ["--depth", "1", "--single-branch"]
    return args


def clone_policy(options: CatchOptions) -> dict[str, object]:
    """What the clone was asked for, as the caller said it.

    The recorded policy decides whether a cached clone can be refreshed or must be replaced.
    It holds ``--since`` as given, not the date it resolves to: ``90d`` names a new date every
    day, and comparing resolved dates replaced the whole clone on every daily refresh.
    """
    since = options.since.strip().lower() if options.since else None
    return {"shallow": options.shallow, "since": since}


def _fetch_channels(
    client: RepositoryClient,
    slug: Slug,
    opts: CatchOptions,
    api_dir: Path,
    log: Callable[[str], None],
) -> int:
    """Fetch the requested provider samples into ``api_dir``; return the issue count."""
    issues_fetched = 0
    if opts.issues > 0:
        items = fetch_issues(client, slug, limit=opts.issues, log=log)
        write_issues(api_dir / "issues.jsonl", items)
        issues_fetched = len(items)
    for name in ("discussions", "reviews", "runs"):
        limit = getattr(opts, name)
        if limit:
            data = getattr(channels, name)(client, slug, limit)
            api_dir.mkdir(parents=True, exist_ok=True)
            temporary = api_dir / f".{name}-{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                temporary.replace(api_dir / f"{name}.json")
            finally:
                temporary.unlink(missing_ok=True)
    return issues_fetched


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
    channels_only: bool = False,
) -> CatchResult:
    """Clone or refresh the prey. ``source_url`` overrides the GitHub URL (used by tests).

    ``channels_only`` fetches the requested provider samples for a prey that is already caught
    and leaves its tree at the cached commit: a digest that asks for issues must not move the
    prey to a newer upstream commit, or replace a shallow cache with a full clone.
    """
    opts = options or CatchOptions()
    # Validate before changing any cache. A bad date used to delete a valid --force clone.
    clone_args = clone_arguments(opts, now=now)
    if any(not 0 <= n <= 3000 for n in (opts.issues, opts.discussions, opts.reviews, opts.runs)):
        raise UsageError("--issues, --discussions, --reviews and --runs must be in 0..3000")
    root = cache_root if cache_root is not None else default_cache_root()
    paths = prey_paths(slug, root)
    root_existed = paths.root.exists()
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
    if previous.get("status") == INCOMPLETE:
        # A provisional record proves the directory is the crab's; it describes no catch.
        previous = {}
    provisional = not root_existed and not paths.catch_file.exists()
    if provisional:
        # Written before any clone, so a catch that fails half way (a refused issues fetch, a
        # full disk) still leaves a record that `crab cache rm` accepts. Only a directory this
        # catch created is claimed: an existing one without a record may be someone's own.
        record = {"slug": str(slug), "status": INCOMPLETE}
        paths.catch_file.write_text(json.dumps(record) + "\n", encoding="utf-8")
    if channels_only and (repo_dir / ".git").exists() and previous:
        log(f"fetching provider samples for {slug}; the cached tree stays at its commit")
        git = GitRunner(repo_dir)
        issues_fetched = _fetch_channels(client, slug, opts, paths.api, log)
        recorded_wiki = previous.get("wiki")
        recorded_window = previous.get("history_window_applied")
        result = CatchResult(
            slug=str(slug),
            url=url,
            repo_dir=str(repo_dir),
            sha=git.head_sha(),
            default_branch=git.default_branch(),
            shallow=git.is_shallow(),
            since=str(previous["since"]) if isinstance(previous.get("since"), str) else None,
            updated=False,
            caught_at=str(previous.get("caught_at") or ""),
            issues_fetched=issues_fetched,
            wiki=dict(recorded_wiki) if isinstance(recorded_wiki, dict) else None,
            history_window_applied=recorded_window if isinstance(recorded_window, bool) else None,
        )
        recorded = {
            **previous,
            "issues_fetched": issues_fetched,
            "channels_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        }
        paths.catch_file.write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8")
        return result

    # A clone without a usable record (a cache from before the policy was recorded, or a
    # catch that failed after cloning) is refreshed, not replaced: replacing it would throw
    # away a valid tree and download the whole history again.
    recorded_policy = previous.get("clone_policy")
    policy_changed = isinstance(recorded_policy, dict) and recorded_policy != clone_policy(opts)
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
        except Exception:
            if not root_existed:
                if provisional:
                    paths.catch_file.unlink(missing_ok=True)
                for directory in (paths.root, paths.root.parent):
                    try:
                        directory.rmdir()
                    except OSError:
                        break
            raise
        git = GitRunner(repo_dir)

    wiki_info: dict[str, object] = {"status": "disabled", "sha": None}
    if opts.wiki and (source_url is None or wiki_source_url is not None):
        wiki_url = wiki_source_url or slug.wiki_clone_url
        try:
            wiki_info = catch_wiki(paths.wiki, wiki_url, log=log, token=token, auth_host=auth_host)
        except ExternalCommandError as exc:
            if wiki_source_url is not None:
                raise
            # The wiki is a second repository the prey may not have. Without credentials,
            # GitHub answers a probe for a missing wiki with an authentication prompt rather
            # than "not found", so a failed probe is evidence about the wiki, never a reason
            # to fail the catch of the prey itself.
            reason = " ".join(exc.message.split())[:200] or "probe failed"
            log(f"wiki: unavailable ({reason})")
            wiki_info = {"status": "unavailable", "sha": None, "url": wiki_url, "reason": reason}

    issues_fetched = _fetch_channels(client, slug, opts, paths.api, log)

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
    recorded = {**result.to_dict(), "clone_policy": clone_policy(opts)}
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
        try:
            if destination.exists():
                destination.rename(backup)
            try:
                staged.rename(destination)
            except OSError:
                if backup.exists():
                    backup.rename(destination)
                raise
        except OSError as exc:
            # On Windows a file another program holds open blocks the rename of its directory.
            raise CrabError(
                f"cannot replace {destination}: {exc.strerror or exc}",
                hint="Close programs holding files in it and run the command again; "
                "the new clone was discarded.",
            ) from exc
    finally:
        if staged.exists():
            rmtree_force(staged, cache_root=staged.parent)
    if backup.exists():
        rmtree_force(backup, cache_root=backup.parent)


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
            ["clone", "--quiet", "--depth", "1", "--single-branch", "--no-checkout"],
            token=token,
            auth_host=auth_host,
        )
    return {"status": "available", "sha": GitRunner(destination).head_sha(), "url": url}
