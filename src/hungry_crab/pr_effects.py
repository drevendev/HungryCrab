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
from .pr_publication import GeneratedFile, PreparedPullRequest

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


def _index_mode(git: GitRunner, path: str) -> str:
    """Return a regular-file mode for ``path`` without consulting worktree attributes."""

    entry = git.run("ls-files", "--stage", "--", path).strip()
    if not entry:
        return "100644"
    mode = entry.split(" ", 1)[0]
    if mode not in {"100644", "100755"}:
        raise CrabError(
            "prepared pull request target has an unsupported git mode",
            hint=f"refusing {path}: mode {mode}",
        )
    return mode


def _stage_exact_file(
    git: GitRunner,
    scratch: Path,
    ordinal: int,
    generated: GeneratedFile,
) -> None:
    """Stage exact prepared bytes without invoking repository clean filters."""

    payload = scratch / f"prepared-{ordinal}.bin"
    payload.write_bytes(generated.content.encode("utf-8"))
    mode = _index_mode(git, generated.path)
    blob = git.run(
        "hash-object",
        "-w",
        "--no-filters",
        "--",
        str(payload),
    ).strip()
    if not blob:
        raise CrabError(
            "git returned no object id for prepared pull request content",
            hint=f"refusing path: {generated.path}",
        )
    git.run(
        "update-index",
        "--add",
        "--cacheinfo",
        mode,
        blob,
        generated.path,
    )


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

    The detached temporary worktree is created without checkout, then its index is populated
    directly from HEAD.  Prepared UTF-8 bytes are hashed with ``--no-filters`` and inserted into
    that index with ``update-index``.  Repository clean/smudge filters therefore cannot rewrite
    or execute on the already-scanned payload.  The maintainer's current worktree and unrelated
    dirty files are never publication inputs.
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
                "--no-checkout",
                str(worktree),
                source,
            )
            added = True
            work_git = GitRunner(worktree, timeout=git.timeout)
            work_git.run("read-tree", "HEAD")
            for ordinal, generated in enumerate(prepared.files):
                _stage_exact_file(work_git, scratch, ordinal, generated)

            staged = [
                line
                for line in work_git.run("diff", "--cached", "--name-only").splitlines()
                if line
            ]
            unexpected = sorted(set(staged) - path_set)
            if unexpected:
                raise CrabError(
                    "git index contains paths outside the prepared publication payload",
                    hint="unexpected paths: " + ", ".join(unexpected),
                )
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
                    "prepared pull request makes no file changes; "
                    "refusing to create an empty branch"
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
                    "pull request publication returned no URL; "
                    "reconcile provider state before retrying"
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
