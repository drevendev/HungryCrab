from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from hungry_crab.errors import ExternalCommandError
from hungry_crab.fetch.github import GitHubClient


class Response(io.BytesIO):
    def __init__(self, data: object, headers: dict[str, str] | None = None) -> None:
        super().__init__(json.dumps(data).encode())
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value


def error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError("https://api.github.com/repos/a/b", code, "error", message, None)


def test_conditional_requests_and_credential_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "first-test-token")
    requests: list[urllib.request.Request] = []

    def open_url(request: urllib.request.Request, **kwargs: object) -> Response:
        requests.append(request)
        if len(requests) == 2:
            raise error(304)
        return Response({"name": "b"}, {"ETag": '"v1"'})

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    client = GitHubClient(prefer_gh=False, cache_dir=tmp_path)
    assert client.get("repos/a/b") == {"name": "b"}
    assert client.get("repos/a/b") == {"name": "b"}
    assert requests[1].get_header("If-none-match") == '"v1"'
    monkeypatch.setenv("GH_TOKEN", "second-test-token")
    assert GitHubClient(prefer_gh=False, cache_dir=tmp_path).get("repos/a/b") == {"name": "b"}
    assert requests[2].get_header("If-none-match") is None
    assert all("test-token" not in p.read_text() for p in tmp_path.iterdir())


@pytest.mark.parametrize(
    "status,headers,delay",
    [
        (502, {}, 1),
        (429, {"Retry-After": "2"}, 2),
        (403, {"Retry-After": "3"}, 3),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "104"}, 5),
    ],
)
def test_retries_obey_server_delay(
    status: int, headers: dict[str, str], delay: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    sleeps: list[float] = []

    def open_url(request: object, **kwargs: object) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error(status, headers)
        return Response({"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    monkeypatch.setattr("hungry_crab.fetch.github.time.sleep", sleeps.append)
    monkeypatch.setattr("hungry_crab.fetch.github.time.time", lambda: 100)
    assert GitHubClient(prefer_gh=False).get("repos/a/b") == {"ok": True}
    assert sleeps == [delay] and calls == 2


@pytest.mark.parametrize(
    "status,headers", [(401, {}), (403, {}), (422, {}), (429, {"Retry-After": "120"})]
)
def test_permanent_errors_or_long_rate_limits_fail_without_retry(
    status: int, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def open_url(request: object, **kwargs: object) -> Response:
        nonlocal calls
        calls += 1
        raise error(status, headers)

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    with pytest.raises(ExternalCommandError):
        GitHubClient(prefer_gh=False).get("repos/a/b")
    assert calls == 1


def test_transport_failure_has_a_bounded_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def open_url(request: object, **kwargs: object) -> Response:
        nonlocal calls
        calls += 1
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    monkeypatch.setattr("hungry_crab.fetch.github.time.sleep", sleeps.append)
    with pytest.raises(ExternalCommandError):
        GitHubClient(prefer_gh=False, retries=2).get("repos/a/b")
    assert calls == 3 and sleeps == [1, 2]


def test_missing_optional_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    def open_url(request: object, **kwargs: object) -> Response:
        raise error(404)

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    assert GitHubClient(prefer_gh=False).get("repos/a/b", allow_missing=True) is None


def test_secondary_rate_limit_without_retry_after_waits_a_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def open_url(request: object, **kwargs: object) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://api.github.com/repos/a/b",
                403,
                "limited",
                Message(),
                io.BytesIO(b'{"message":"secondary rate limit"}'),
            )
        return Response({"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", open_url)
    monkeypatch.setattr("hungry_crab.fetch.github.time.sleep", sleeps.append)
    assert GitHubClient(prefer_gh=False).get("repos/a/b") == {"ok": True}
    assert calls == 2 and sleeps == [60]
