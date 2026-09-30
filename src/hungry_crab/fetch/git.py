"""A thin, read-mostly wrapper around the git executable.

Only plumbing that cannot execute repository content is used (clone, fetch, log, for-each-ref,
rev-list, rev-parse). Hooks are never installed by the crab, prompts are disabled, and output is
decoded leniently so odd commit messages cannot break a digest.

A repository's own configuration is not the crab's: a local directory eaten as prey brings its
`.git/config` with it (#134), and several keys there make read-only commands start programs.
Every command runs with those keys overridden — the filesystem monitor, signature checks, the
external diff and textconv drivers, and every filter driver the repository configures.
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
    # A filesystem monitor is a program git runs on every index refresh (`diff HEAD`,
    # `ls-files`), and `log.showSignature` runs `gpg.program` for every commit logged.
    "-c", "core.fsmonitor=false",
    "-c", "log.showSignature=false",
)  # fmt: skip
# Commands that produce diffs run the external diff and textconv drivers a repository names.
_DIFF_COMMANDS = frozenset({"diff", "log", "show"})
_NO_DIFF_DRIVERS = ("--no-ext-diff", "--no-textconv")
_FILTER_KEYS = ("clean", "smudge", "process")


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
            # The prey's LFS content is never worth fetching: the miners read files as data and
            # nothing is ever built or run, so a pointer tells them everything a two-gigabyte
            # blob would. Without this, a clone downloads it — and the GitHub `size` field that
            # `sniff` warns on counts only the packed git objects, so no preflight on that number
            # could bound what lands on disk.
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
    )
    return env


def filter_drivers(listing: str) -> list[str]:
    """Driver names from ``git config --null --name-only --get-regexp ^filter\\.`` output."""

    names: list[str] = []
    for key in listing.split("\0"):
        key = key.strip("\n")
        if not key.startswith("filter.") or key.count(".") < 2:
            continue
        name = key[len("filter.") : key.rindex(".")]
        if name not in names:
            names.append(name)
    return names


def neutral_filter_env(names: list[str], base: dict[str, str]) -> dict[str, str]:
    """``GIT_CONFIG_*`` entries that empty each named filter driver; an empty command is none.

    A clean filter runs whenever git hashes a working-tree file it cannot match by stat, as
    `git diff HEAD` does on a copied or dirty tree, and the repository chooses both the driver
    (`.gitattributes`) and its command (`.git/config`). The entries go through the environment
    rather than ``-c`` so that a driver name holding ``=`` cannot split its own key.
    """

    try:
        count = int(base.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    env: dict[str, str] = {}
    for name in names:
        for key, value in (*((k, "") for k in _FILTER_KEYS), ("required", "false")):
            env[f"GIT_CONFIG_KEY_{count}"] = f"filter.{name}.{key}"
            env[f"GIT_CONFIG_VALUE_{count}"] = value
            count += 1
    if env:
        env["GIT_CONFIG_COUNT"] = str(count)
    return env


class GitRunner:
    """Run git commands in one working directory."""

    def __init__(self, cwd: Path, *, timeout: float = 600.0) -> None:
        self.cwd = cwd
        self.timeout = timeout
        self._exe: str | None = None
        self._filters: dict[str, list[str]] = {}

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    @property
    def exe(self) -> str:
        if self._exe is None:
            self._exe = git_executable()
        return self._exe

    def _filter_drivers(self, where: Path, env: dict[str, str]) -> list[str]:
        """The filter drivers configured where a command runs, listed once per directory.

        Reading configuration runs nothing; a listing that fails leaves nothing to empty.
        """
        key = str(where)
        if key not in self._filters:
            try:
                proc = subprocess.run(
                    [self.exe, "config", "--null", "--name-only", "--get-regexp", r"^filter\."],
                    cwd=key,
                    capture_output=True,
                    env=env,
                    timeout=self.timeout,
                    check=False,
                )
                ok = proc.returncode == 0
                listing = proc.stdout.decode("utf-8", errors="replace") if ok else ""
            except (OSError, subprocess.TimeoutExpired):
                listing = ""
            self._filters[key] = filter_drivers(listing)
        return self._filters[key]

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
        cwd: Path | None = None,
    ) -> str:
        """Run ``git <args>`` and return stdout as text."""
        if args and args[0] in _DIFF_COMMANDS:
            args = (args[0], *_NO_DIFF_DRIVERS, *args[1:])
        command = [self.exe, *SAFE_CONFIG, *args]
        limit = timeout or self.timeout
        where = cwd or self.cwd
        env = git_env()
        env.update(neutral_filter_env(self._filter_drivers(where, env), env))
        try:
            proc = subprocess.run(
                command,
                cwd=str(where),
                capture_output=True,
                env=env,
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
        """Whether history provenance is incomplete or cannot be established safely."""
        out = self.try_run("rev-parse", "--is-shallow-repository")
        return (out or "").strip() != "false"

    def has_commits(self) -> bool:
        return self.ok("rev-parse", "--verify", "-q", "HEAD")
