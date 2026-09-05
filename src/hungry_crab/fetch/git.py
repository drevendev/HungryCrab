"""A thin, read-mostly wrapper around the git executable.

Only plumbing that cannot execute repository content is used (clone, fetch, log, for-each-ref,
rev-list, rev-parse). Hooks are never installed by the crab, prompts are disabled, and output is
decoded leniently so odd commit messages cannot break a digest.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..errors import ExternalCommandError, ToolMissingError

SAFE_CONFIG: tuple[str, ...] = (
    "-c", "core.quotepath=off",
    "-c", "core.longpaths=true",
    "-c", "i18n.logOutputEncoding=utf-8",
    "-c", "core.pager=cat",
    "-c", "color.ui=never",
)  # fmt: skip


def git_executable() -> str:
    exe = shutil.which("git")
    if not exe:
        raise ToolMissingError(
            "git is not installed or not on PATH",
            hint="install Git from https://git-scm.com/downloads",
        )
    return exe


def git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return env


class GitRunner:
    """Run git commands in one working directory."""

    def __init__(self, cwd: Path, *, timeout: float = 600.0) -> None:
        self.cwd = cwd
        self.timeout = timeout
        self._exe: str | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    @property
    def exe(self) -> str:
        if self._exe is None:
            self._exe = git_executable()
        return self._exe

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
        cwd: Path | None = None,
    ) -> str:
        """Run ``git <args>`` and return stdout as text."""
        command = [self.exe, *SAFE_CONFIG, *args]
        limit = timeout or self.timeout
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd or self.cwd),
                capture_output=True,
                env=git_env(),
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExternalCommandError(f"git {args[0]} timed out after {limit:.0f}s") from exc
        except OSError as exc:
            raise ExternalCommandError(f"failed to run git: {exc}") from exc
        stdout = proc.stdout.decode("utf-8", errors="replace")
        if check and proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            shown = " ".join(args[:2])
            raise ExternalCommandError(
                f"git {shown} failed (exit {proc.returncode}): {stderr[-800:]}"
            )
        return stdout

    def try_run(self, *args: str, timeout: float | None = None) -> str | None:
        """Like ``run`` but return ``None`` instead of raising on a non-zero exit."""
        try:
            return self.run(*args, check=True, timeout=timeout)
        except ExternalCommandError:
            return None

    def ok(self, *args: str) -> bool:
        return self.try_run(*args) is not None

    def is_repo(self) -> bool:
        out = self.try_run("rev-parse", "--is-inside-work-tree")
        return out is not None and out.strip() == "true"

    def toplevel(self) -> Path | None:
        out = self.try_run("rev-parse", "--show-toplevel")
        return Path(out.strip()) if out and out.strip() else None

    def head_sha(self) -> str:
        return self.run("rev-parse", "HEAD").strip()

    def current_branch(self) -> str | None:
        out = self.try_run("symbolic-ref", "--short", "-q", "HEAD")
        if out is None:
            return None
        return out.strip() or None

    def default_branch(self) -> str:
        """origin/HEAD when there is a remote, else the checked-out branch, else main/master."""
        out = self.try_run("symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
        if out and out.strip():
            return out.strip().removeprefix("origin/")
        current = self.current_branch()
        if current:
            return current
        for candidate in ("main", "master"):
            if self.ok("rev-parse", "--verify", "-q", f"refs/heads/{candidate}"):
                return candidate
        return "HEAD"

    def is_shallow(self) -> bool:
        out = self.try_run("rev-parse", "--is-shallow-repository")
        return (out or "").strip() == "true"

    def has_commits(self) -> bool:
        return self.ok("rev-parse", "--verify", "-q", "HEAD")
