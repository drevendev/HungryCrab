"""Concrete git/GitHub effects for an already prepared Hungry Crab pull request.

The publication transaction in :mod:`hungry_crab.pr_publication` owns the safety ordering:
scan the complete immutable payload, reconcile provider truth, then call this module.  This
adapter deliberately works in a temporary git worktree so a maw maintainer's current branch,
index, and unrelated dirty files are not publication inputs.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from .cache import Slug
from .errors import CrabError
from .fetch.git import GitRunner
from .pr_publication import PreparedPullRequest

GhRunner = Callable[..., str]


def _prepared_paths(prepared: PreparedPullRequest) -> tuple[str, ...]:
    """Validate file paths before any provider read or publication effect."""

    paths: list[str] = []
    seen: set[str] = set()
    for generated in prepared.files:
        path = generated.path
        parts = path.split("/")
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(ord(character) < 32 for character in path)
            or any(not part or part in {".", ".."} for part in parts)
            or ":" in parts[0]
            or any(part.casefold() == ".git" for part in parts)
        ):
            raise CrabError(
                "prepared pull request contains an unsafe maw path",
                hint=f"refusing path: {path!r}",
            )
        if path in seen:
            raise CrabError(
                "prepared pull request contains a duplicate maw path",
                hint=f"duplicate path: {path}",
            )
        seen.add(path)
        paths.append(path)
    if not paths:
        raise CrabError("prepared pull request contains no files")
    return tuple(paths)


def _write_exact_file(root: Path, path: str, content: str) -> None:
    """Write one prepared file without following a path outside the temporary worktree."""

    root = root.resolve(strict=True)
    current = root
    parts = path.split("/")
    for part in parts[:-1]:
        child = current / part
        if child.exists() or child.is_symlink():
            if child.is_symlink():
                raise CrabError(
                    "prepared pull request path crosses a symlink",
                    hint=f"refusing path: {path}",
                )
            if not child.is_dir():
                raise CrabError(
                    "prepared pull request path crosses a non-directory",
                    hint=f"refusing path: {path}",
                )
            resolved = child.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise CrabError(
                    "prepared pull request path resolves outside the temporary worktree",
                    hint=f"refusing path: {path}",
                ) from exc
            current = resolved
            continue
        child.mkdir()
        current = child

    target = current / parts[-1]
    if target.is_symlink():
        raise CrabError(
            "prepared pull request target is a symlink",
            hint=f"refusing path: {path}",
        )
    if target.exists() and not target.is_file():
        raise CrabError(
            "prepared pull request target is not a regular file",
            hint=f"refusing path: {path}",
        )
    if target.exists():
        resolved_target = target.resolve(strict=True)
        try:
            resolved_target.relative_to(root)
        except ValueError as exc:
            raise CrabError(
                "prepared pull request target resolves outside the temporary worktree",
                hint=f"refusing path: {path}",
            ) from exc
    target.write_bytes(content.encode("utf-8"))


def publish_git_pull_request(
    slug: Slug,
    maw_root: Path,
    branch: str,
    prepared: PreparedPullRequest,
    *,
    run_gh: GhRunner,
    git: GitRunner | None = None,
) -> str:
    """Publish an already scanned/reconciled payload through a deterministic branch.

    This is the effect callback for ``publish_prepared_transaction``; callers must not invoke it
    before that transaction has scanned the payload and reconciled marker-bearing pull requests.
    A remote deterministic branch left by a crash is reused.  Its existing changed-path set may
    be a subset of the current prepared set, but it may not contain any extra path: preserving a
    stale file would make the branch broader than the immutable publication payload.

    Repository hooks are disabled for worktree, commit, and push operations.  Only the prepared
    paths are written and staged, from a detached temporary worktree, so the maintainer's current
    worktree and unrelated dirty files are untouched.
    """

    paths = _prepared_paths(prepared)
    path_set = set(paths)
    git = git or GitRunner(maw_root)
    if not git.is_repo():
        raise CrabError("the maw is not a git repository; cannot publish a pull request")

    git.run("check-ref-format", "--branch", branch)
    base = run_gh("api", f"repos/{slug}", "--jq", ".default_branch").strip()
    if not base:
        raise CrabError("GitHub returned no default branch for the maw")
    git.run("check-ref-format", "--branch", base)

    git.run("fetch", "--no-tags", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}")
    remote_branch = git.run("ls-remote", "--heads", "origin", f"refs/heads/{branch}").strip()
    branch_exists = bool(remote_branch)
    if branch_exists:
        git.run(
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        )
        existing_paths = {
            line
            for line in git.run(
                "diff",
                "--name-only",
                f"refs/remotes/origin/{base}...refs/remotes/origin/{branch}",
            ).splitlines()
            if line
        }
        extras = sorted(existing_paths - path_set)
        if extras:
            raise CrabError(
                "existing nutrient branch contains files outside the prepared payload",
                hint="unexpected paths: " + ", ".join(extras),
            )
        source = f"refs/remotes/origin/{branch}"
    else:
        source = f"refs/remotes/origin/{base}"

    with tempfile.TemporaryDirectory(prefix="hungry-crab-pr-") as scratch_text:
        scratch = Path(scratch_text)
        worktree = scratch / "worktree"
        hooks = scratch / "empty-hooks"
        hooks.mkdir()
        hook_config = f"core.hooksPath={hooks}"
        added = False
        try:
            git.run(
                "-c",
                hook_config,
                "worktree",
                "add",
                "--detach",
                str(worktree),
                source,
            )
            added = True
            for generated in prepared.files:
                _write_exact_file(worktree, generated.path, generated.content)

            work_git = GitRunner(worktree, timeout=git.timeout)
            work_git.run("add", "--", *paths)
            staged = [
                line
                for line in work_git.run("diff", "--cached", "--name-only").splitlines()
                if line
            ]
            if staged:
                work_git.run(
                    "-c",
                    hook_config,
                    "commit",
                    "-m",
                    "feat: serve Hungry Crab nutrient",
                )
                work_git.run(
                    "-c",
                    hook_config,
                    "push",
                    "origin",
                    f"HEAD:refs/heads/{branch}",
                )
            elif not branch_exists:
                raise CrabError(
                    "prepared pull request makes no file changes; refusing to create an empty branch"
                )

            body_path = scratch / "pull-request-body.md"
            body_path.write_bytes(prepared.body.encode("utf-8"))
            output = run_gh(
                "pr",
                "create",
                "--repo",
                str(slug),
                "--head",
                branch,
                "--base",
                base,
                "--title",
                prepared.title,
                "--body-file",
                str(body_path),
            )
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if not lines:
                raise CrabError(
                    "pull request publication returned no URL; reconcile provider state before retrying"
                )
            return lines[-1]
        finally:
            if added:
                git.run(
                    "-c",
                    hook_config,
                    "worktree",
                    "remove",
                    "--force",
                    str(worktree),
                    check=False,
                )
