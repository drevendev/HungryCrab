"""GitHub REST access: ``gh api`` when the CLI is installed, plain HTTPS otherwise."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from ..cache import Slug
from ..errors import ExternalCommandError

API_ROOT = "https://api.github.com"
USER_AGENT = "hungry-crab (+https://github.com/drevendev/HungryCrab)"


class GitHubClient:
    """Minimal read-only client. Every call returns parsed JSON."""

    def __init__(
        self,
        *,
        prefer_gh: bool = True,
        timeout: float = 120.0,
        cache_dir: Path | None = None,
        retries: int = 3,
        max_wait: float = 60.0,
    ) -> None:
        self.gh = shutil.which("gh") if prefer_gh else None
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.retries = retries
        self.max_wait = max_wait

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
        # Partition validators by credentials: a private response must never satisfy an
        # anonymous request, or a request using a different job token.
        key = hashlib.sha256(f"{self.token or ''}\0{url}".encode()).hexdigest()
        cached: dict[str, Any] = {}
        cache_file = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cache_file and cache_file.is_file():
            try:
                loaded = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "body" in loaded:
                    cached = loaded
            except (OSError, ValueError):
                pass
        if isinstance(cached.get("etag"), str):
            headers["If-None-Match"] = cached["etag"]
        elif isinstance(cached.get("modified"), str):
            headers["If-Modified-Since"] = cached["modified"]
        request = urllib.request.Request(url, headers=headers)
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    try:
                        data = json.loads(body or "null")
                    except ValueError as exc:
                        raise ExternalCommandError(f"GET {url} returned invalid JSON") from exc
                    if cache_file:
                        entry = {
                            "body": data,
                            "etag": response.headers.get("ETag"),
                            "modified": response.headers.get("Last-Modified"),
                        }
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        temporary = cache_file.with_name(f".{key}-{uuid.uuid4().hex}.tmp")
                        try:
                            temporary.write_text(json.dumps(entry), encoding="utf-8")
                            temporary.replace(cache_file)
                        finally:
                            temporary.unlink(missing_ok=True)
                    return data
            except urllib.error.HTTPError as exc:
                try:
                    error_body = exc.read(4096) if exc.code == 403 else b""
                except OSError:
                    error_body = b""
                exc.close()
                if exc.code == 304 and cached:
                    return cached["body"]
                if exc.code == 404 and allow_missing:
                    return None
                delay = retry_delay(exc, attempt, error_body=error_body)
                if delay is not None and attempt < self.retries and delay <= self.max_wait:
                    time.sleep(delay)
                    continue
                hint = "set GH_TOKEN or GITHUB_TOKEN with read access to the repository"
                if delay is not None:
                    hint = f"GitHub requested a retry after {delay:.0f}s; retry the job later"
                raise ExternalCommandError(
                    f"GET {url} failed with HTTP {exc.code}", hint=hint
                ) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                delay = float(2**attempt)
                if attempt < self.retries and delay <= self.max_wait:
                    time.sleep(delay)
                    continue
                raise ExternalCommandError(f"GET {url} failed: {exc}") from exc
        raise AssertionError("unreachable")


def retry_delay(
    error: urllib.error.HTTPError, attempt: int, *, error_body: bytes = b""
) -> float | None:
    """Respect server delays; never retry ordinary permission or validation errors."""
    headers = error.headers
    retry_after = headers.get("Retry-After")
    if retry_after and error.code in (403, 429, 503):
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                return None
    if error.code in (403, 429) and headers.get("X-RateLimit-Remaining") == "0":
        try:
            reset = headers.get("X-RateLimit-Reset")
            return max(0.0, float(reset) - time.time()) + 1 if reset else float(60 * 2**attempt)
        except ValueError:
            return None
    if error.code == 403 and b"secondary rate limit" in error_body.lower():
        return float(60 * 2**attempt)
    if error.code == 429:
        return float(60 * 2**attempt)
    if error.code in (500, 502, 503, 504):
        return float(2**attempt)
    return None
