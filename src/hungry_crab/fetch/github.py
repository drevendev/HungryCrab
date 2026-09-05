"""GitHub REST access: ``gh api`` when the CLI is installed, plain HTTPS otherwise."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any

from ..cache import Slug
from ..errors import ExternalCommandError

API_ROOT = "https://api.github.com"
USER_AGENT = "hungry-crab (+https://github.com/drevendev/HungryCrab)"


class GitHubClient:
    """Minimal read-only client. Every call returns parsed JSON."""

    def __init__(self, *, prefer_gh: bool = True, timeout: float = 120.0) -> None:
        self.gh = shutil.which("gh") if prefer_gh else None
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.timeout = timeout

    @property
    def transport(self) -> str:
        return "gh" if self.gh else "https"

    def get(self, path: str, *, allow_missing: bool = False) -> Any:
        if self.gh:
            return self._get_gh(path, allow_missing=allow_missing)
        return self._get_https(path, allow_missing=allow_missing)

    def repo(self, slug: Slug) -> dict[str, Any]:
        data = self.get(f"repos/{slug}")
        if not isinstance(data, dict):
            raise ExternalCommandError(f"unexpected response for repos/{slug}")
        return data

    def languages(self, slug: Slug) -> dict[str, int]:
        data = self.get(f"repos/{slug}/languages", allow_missing=True)
        if not isinstance(data, dict):
            return {}
        return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}

    def _get_gh(self, path: str, *, allow_missing: bool) -> Any:
        assert self.gh is not None
        command = [
            self.gh,
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            path,
        ]
        env = dict(os.environ)
        env.update({"GH_PAGER": "cat", "NO_COLOR": "1", "GH_PROMPT_DISABLED": "1"})
        try:
            proc = subprocess.run(
                command, capture_output=True, env=env, timeout=self.timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExternalCommandError(f"failed to run gh api {path}: {exc}") from exc
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            if allow_missing and ("404" in stderr or "Not Found" in stderr):
                return None
            raise ExternalCommandError(
                f"gh api {path} failed: {stderr[-500:]}",
                hint="check authentication with: gh auth status",
            )
        return json.loads(stdout or "null")

    def _get_https(self, path: str, *, allow_missing: bool) -> Any:
        url = f"{API_ROOT}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_missing:
                return None
            raise ExternalCommandError(
                f"GET {url} failed with HTTP {exc.code}",
                hint="install gh and run `gh auth login`, or set GH_TOKEN",
            ) from exc
        except urllib.error.URLError as exc:
            raise ExternalCommandError(f"GET {url} failed: {exc.reason}") from exc
        return json.loads(body or "null")
