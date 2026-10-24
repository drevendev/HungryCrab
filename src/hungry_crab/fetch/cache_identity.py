"""Prove cache ownership and the intended Git source before destructive acquisition."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

from ..artifact_read import read_bytes
from ..cache import Slug, prey_paths
from ..errors import CrabError
from ..fs import is_reparse_point
from .bounded import ResponseTooLargeError, run_bounded
from .git import git_env, git_executable

CLONE_RECORD = "crab-catch.json"
_HINT = "Move an unrecognised checkout aside yourself; the crab will not adopt or replace it."


def _acquisition_env() -> dict[str, str]:
    from .acquisition import CURRENT

    env = git_env()
    budget = CURRENT.get()
    if budget is not None:
        env = budget.git_env(env)
    if any(
        env.get(key)
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_INDEX_FILE",
            "GIT_QUARANTINE_PATH",
            "GIT_TEMPLATE_DIR",
        )
    ):
        raise CrabError(
            "cache acquisition environment uses external Git metadata or working tree",
            hint="unset Git path overrides before acquiring or mutating a cache",
        )
    return env


def _real_path(path: Path, root: Path) -> None:
    try:
        parts = path.absolute().relative_to(root.absolute()).parts
    except ValueError as exc:
        raise CrabError("cache mutation path is outside its cache") from exc
    if not parts or ".." in parts:
        raise CrabError("cache mutation path is not below its cache")
    current = root
    for part in parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CrabError("cannot inspect a cache mutation path", hint=_HINT) from exc
        if is_reparse_point(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise CrabError("refusing cache mutation through a link or reparse point", hint=_HINT)


def _record(path: Path) -> dict[str, object]:
    try:
        data = json.loads(read_bytes(path, 1 << 20))
    except (CrabError, OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _nonempty(path: Path) -> bool:
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is not None
    except OSError as exc:
        raise CrabError("cannot inspect an existing cache directory", hint=_HINT) from exc


def require_cache_outputs(paths: Iterable[Path], *, cache_root: Path) -> None:
    """Every output must have real parents and no symlink or reparse-point incumbent."""
    for path in paths:
        _real_path(path, cache_root)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CrabError("cannot inspect cache output") from exc
        if not stat.S_ISREG(info.st_mode) or is_reparse_point(info):
            raise CrabError("cache output is not a regular, non-reparse file")


def atomic_cache_text(path: Path, text: str | Iterable[str], *, cache_root: Path) -> None:
    """Publish through an exclusive temporary file; never truncate an existing inode."""
    require_cache_outputs([path], cache_root=cache_root)
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        require_cache_outputs([path], cache_root=cache_root)
        descriptor, name = tempfile.mkstemp(prefix=".crab-write-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.writelines([text] if isinstance(text, str) else text)
        require_cache_outputs([path], cache_root=cache_root)
        temporary.replace(path)
    except OSError as exc:
        raise CrabError("cannot atomically publish cache metadata") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _owned_entry(path: Path, slug: Slug, root: Path, *, include_sniff: bool = True) -> bool:
    records = [path / "catch.json"]
    if include_sniff:
        records.append(path / "api" / "sniff.json")
    for record in records:
        _real_path(record, root)
        named = _record(record).get("slug")
        if isinstance(named, str) and named.casefold() == str(slug).casefold():
            return True
    return False


def _metadata_has_no_aliases(git_dir: Path) -> None:
    pending = [git_dir]
    count = 0
    try:
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    count += 1
                    if count > 100_000:
                        raise CrabError("cached Git metadata exceeds the identity inspection limit")
                    info = entry.stat(follow_symlinks=False)
                    if is_reparse_point(info) or stat.S_ISLNK(info.st_mode):
                        raise CrabError("cached Git metadata contains a link or reparse point")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
    except OSError as exc:
        raise CrabError("cannot inspect cached Git metadata", hint=_HINT) from exc


def require_clone_identity(
    path: Path, url: str, *, cache_root: Path, slug: Slug | None = None, is_wiki: bool = False
) -> None:
    """Refuse unowned or retargeted existing clones, before fetching or replacing them."""
    acquisition_env = _acquisition_env()
    _real_path(path, cache_root)
    if not path.exists():
        return
    if not path.is_dir():
        raise CrabError("refusing to replace a non-directory cache entry", hint=_HINT)
    if not _nonempty(path):
        return
    git_dir = path / ".git"
    _real_path(git_dir / "config", cache_root)
    if not git_dir.is_dir() or (git_dir / "commondir").exists():
        raise CrabError("refusing to mutate a cache clone with external Git metadata", hint=_HINT)
    _metadata_has_no_aliases(git_dir)
    _real_path(git_dir / CLONE_RECORD, cache_root)
    marker = _record(git_dir / CLONE_RECORD)
    owned = marker.get("url") == url and marker.get("schema") == 1
    caught = bool(slug and _owned_entry(path.parent, slug, cache_root, include_sniff=False))
    if caught and is_wiki:
        wiki_record = _record(path.parent / "catch.json").get("wiki")
        caught = (
            isinstance(wiki_record, dict)
            and wiki_record.get("status") == "available"
            and wiki_record.get("url") == url
        )
    if not owned and not caught:
        raise CrabError("refusing to mutate a clone with no crab ownership record", hint=_HINT)
    # Read only this regular config file: includes and environment overrides cannot supply
    # an alternative identity. Never echo a URL that might contain credentials.
    env = {k: v for k, v in git_env().items() if not k.startswith("GIT_CONFIG")}
    try:
        result = run_bounded(
            [
                git_executable(),
                "config",
                "--file",
                str(git_dir / "config"),
                "--no-includes",
                "--null",
                "--get-all",
                "remote.origin.url",
            ],
            env=env,
            timeout=10,
            stdout_limit=16 * 1024,
            stderr_limit=16 * 1024,
        )
    except (OSError, subprocess.SubprocessError, ResponseTooLargeError) as exc:
        raise CrabError("cannot verify the cached clone's remote identity", hint=_HINT) from exc
    if result.returncode or result.stdout != url.encode("utf-8") + b"\0":
        raise CrabError("cached clone origin differs from the requested source", hint=_HINT)
    # Git may rewrite that local URL through includes, insteadOf or caller configuration.
    # Ask the same effective configuration the real acquisition will use, without fetching.
    env = acquisition_env
    try:
        effective = run_bounded(
            [git_executable(), "-C", str(path), "remote", "get-url", "--all", "origin"],
            env=env,
            timeout=10,
            stdout_limit=16 * 1024,
            stderr_limit=16 * 1024,
        )
    except (OSError, subprocess.SubprocessError, ResponseTooLargeError) as exc:
        raise CrabError("cannot verify the cached clone's effective remote", hint=_HINT) from exc
    if effective.returncode or effective.stdout != url.encode("utf-8") + b"\n":
        raise CrabError(
            "cached clone effective origin differs from the requested source", hint=_HINT
        )
    try:
        location = run_bounded(
            [
                git_executable(),
                "-C",
                str(path),
                "rev-parse",
                "--absolute-git-dir",
                "--show-toplevel",
            ],
            env=env,
            timeout=10,
            stdout_limit=16 * 1024,
            stderr_limit=16 * 1024,
        )
        locations = [Path(line).resolve() for line in location.stdout.decode("utf-8").splitlines()]
    except (OSError, ValueError, subprocess.SubprocessError, ResponseTooLargeError) as exc:
        raise CrabError("cannot verify the cached clone's Git locations", hint=_HINT) from exc
    if location.returncode or locations != [git_dir.resolve(), path.resolve()]:
        raise CrabError("cached clone uses external Git metadata or working tree", hint=_HINT)


def require_cache_identity(
    slug: Slug, root: Path, *, url: str | None = None, wiki_url: str | None = None
) -> None:
    """Check the live entry before sniff, catch, remove or a staged replacement can claim it."""
    _acquisition_env()
    paths = prey_paths(slug, root)
    _real_path(paths.root, root)
    if not paths.root.exists():
        return
    if os.path.lexists(paths.root / ".git"):
        raise CrabError(
            "refusing cache mutation: the entry is itself a working checkout", hint=_HINT
        )
    _real_path(paths.api, root)
    owned = _owned_entry(paths.root, slug, root)
    if _nonempty(paths.root) and not owned:
        raise CrabError(
            "refusing cache mutation: no catch or sniff record names this prey", hint=_HINT
        )
    if any(
        entry.name not in {"api", "repo", "wiki", "digests", "catch.json"}
        for entry in paths.root.iterdir()
    ):
        raise CrabError("refusing cache mutation: entry contains unrecognised data", hint=_HINT)
    previous = _record(paths.catch_file)
    recorded = previous.get("url")
    marker_path = paths.repo / ".git" / CLONE_RECORD
    _real_path(marker_path, root)
    marker_url = _record(marker_path).get("url")
    fallback = marker_url if isinstance(marker_url, str) and marker_url else slug.clone_url
    source = url or (recorded if isinstance(recorded, str) and recorded else fallback)
    require_clone_identity(paths.repo, source, cache_root=root, slug=slug)
    recorded_wiki = previous.get("wiki")
    recorded_wiki_url = recorded_wiki.get("url") if isinstance(recorded_wiki, dict) else None
    wiki_source = wiki_url or (
        recorded_wiki_url
        if isinstance(recorded_wiki_url, str) and recorded_wiki_url
        else slug.wiki_clone_url
    )
    require_clone_identity(paths.wiki, wiki_source, cache_root=root, slug=slug, is_wiki=True)
    require_cache_outputs(
        [
            paths.catch_file,
            *(
                paths.api / name
                for name in (
                    "repo.json",
                    "languages.json",
                    "sniff.json",
                    "issues.jsonl",
                    "discussions.json",
                    "reviews.json",
                    "runs.json",
                )
            ),
        ],
        cache_root=root,
    )


def require_replacement_identity(source: Path, target: Path, root: Path) -> None:
    """Apply the same proof before a quota transaction overwrites a live clone."""
    parts = target.absolute().relative_to(root.absolute()).parts
    if len(parts) < 4 or parts[0] not in {"github", "gitlab"} or parts[-1] not in {"repo", "wiki"}:
        return
    slug = Slug(
        "/".join(parts[1:-2]), parts[-2], "gitlab.com" if parts[0] == "gitlab" else "github.com"
    )
    marker = _record(source / ".git" / CLONE_RECORD)
    url = marker.get("url")
    if not isinstance(url, str) or not url:
        raise CrabError("staged clone has no acquisition identity")
    require_clone_identity(target, url, cache_root=root, slug=slug, is_wiki=parts[-1] == "wiki")
