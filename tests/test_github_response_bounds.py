"""API-byte regressions use owned synthetic children, never network or prey code."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
from unittest.mock import Mock

import pytest

from hungry_crab import serve
from hungry_crab.cache import Slug
from hungry_crab.errors import ExternalCommandError
from hungry_crab.fetch import bounded, github
from hungry_crab.fetch.bounded import ResponseTooLargeError, read_limited, run_bounded
from hungry_crab.fetch.github import GitHubClient
from hungry_crab.serve import GhIssueClient


def child(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


@pytest.mark.parametrize("payload", [b"", b"x", b"x" * 16])
def test_limited_stream_accepts_eof_at_or_below_limit(payload):
    assert read_limited(io.BytesIO(payload), 16, label="test") == payload


def test_limited_stream_stops_after_one_sentinel_byte():
    stream = io.BytesIO(b"x" * 200)
    with pytest.raises(ResponseTooLargeError, match="test exceeds 16 bytes"):
        read_limited(stream, 16, label="test")
    assert stream.tell() == 17


def test_limited_stream_handles_short_reads():
    class ShortReads(io.BytesIO):
        def read(self, size=-1):
            assert size > 0
            return super().read(min(size, 2))

    assert read_limited(ShortReads(b"abcdef"), 6, label="test") == b"abcdef"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limits_fail_before_spawning(monkeypatch, limit):
    spawn = Mock()
    monkeypatch.setattr(bounded.subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="positive integer"):
        run_bounded(child("pass"), timeout=1, stdout_limit=limit)
    spawn.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_timeouts_fail_before_transport(monkeypatch, timeout):
    which = Mock()
    monkeypatch.setattr(github.shutil, "which", which)
    with pytest.raises(ValueError, match="finite and positive"):
        GitHubClient(timeout=timeout)
    which.assert_not_called()
    with pytest.raises(ValueError, match="finite and positive"):
        run_bounded(child("pass"), timeout=timeout)


def test_capture_preserves_both_streams_and_exit_code():
    result = run_bounded(
        child(
            "import sys; sys.stdout.buffer.write(b'ok\\n'); "
            "sys.stderr.write('diagnostic'); sys.exit(3)"
        ),
        timeout=5,
    )
    assert result.returncode == 3
    assert result.stdout == b"ok\n"
    assert result.stderr == b"diagnostic"


def test_capture_accepts_exact_boundaries():
    result = run_bounded(
        child("import sys; sys.stdout.write('x'*16); sys.stderr.write('e'*8)"),
        timeout=5,
        stdout_limit=16,
        stderr_limit=8,
    )
    assert result.stdout == b"x" * 16
    assert result.stderr == b"e" * 8


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_capture_overflow_kills_and_reaps_direct_child(monkeypatch, stream):
    real_spawn = bounded.subprocess.Popen
    spawned = []

    def record(*args, **kwargs):
        proc = real_spawn(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(bounded.subprocess, "Popen", record)
    with pytest.raises(ResponseTooLargeError, match=f"gh {stream} exceeds"):
        run_bounded(
            child(f"import sys,time; sys.{stream}.write('x'*17); time.sleep(30)"),
            timeout=5,
            stdout_limit=16,
            stderr_limit=16,
        )
    assert spawned[0].poll() is not None
    assert spawned[0].stdout.closed and spawned[0].stderr.closed


def test_stderr_is_drained_while_stdout_is_idle():
    result = run_bounded(
        child("import sys; sys.stderr.write('e'*60000); sys.stdout.write('{}')"),
        timeout=5,
    )
    assert result.stdout == b"{}"
    assert len(result.stderr) == 60000


def test_request_input_is_written_while_both_response_pipes_are_drained():
    result = run_bounded(
        child(
            "import sys; sys.stderr.write('e'*60000); sys.stderr.flush(); "
            "sys.stdout.write('x'*60000); sys.stdout.flush(); "
            "data=sys.stdin.buffer.read(); sys.stdout.write(str(len(data)))"
        ),
        timeout=5,
        input_data=b"request" * 30000,
    )
    assert result.stdout == b"x" * 60000 + b"210000"
    assert result.stderr == b"e" * 60000


def test_deadline_covers_a_child_that_never_consumes_request_input():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(child("import time; time.sleep(30)"), timeout=0.3, input_data=b"x" * 200000)
    assert time.monotonic() - started < 1.8


def test_timeout_applies_after_both_pipes_close(monkeypatch):
    real_spawn = bounded.subprocess.Popen
    spawned = []

    def record(*args, **kwargs):
        proc = real_spawn(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(bounded.subprocess, "Popen", record)
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(child("import os,time; os.close(1); os.close(2); time.sleep(30)"), timeout=0.3)
    assert spawned[0].poll() is not None


def test_deadline_does_not_depend_on_parent_still_running():
    # The owned grandchild exits by itself. It deliberately inherits the pipes,
    # exposing the old timer's poll() guard without leaving a persistent process.
    code = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'])"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(child(code), timeout=0.3)
    assert time.monotonic() - start < 1.8


def install_fake_gh(monkeypatch, code):
    calls = []

    def capture(command, **kwargs):
        calls.append((command, kwargs))
        return run_bounded(child(code), **kwargs)

    monkeypatch.setattr(github, "run_bounded", capture)
    monkeypatch.setattr(github.shutil, "which", lambda _: "/trusted/gh")
    return calls


def test_gh_client_retains_authentication_environment_and_json(monkeypatch):
    calls = install_fake_gh(monkeypatch, "print('{\"ok\":true}')")
    client = GitHubClient(timeout=5)
    assert client.get("repos/owner/repo") == {"ok": True}
    command, kwargs = calls[0]
    assert command[0] == "/trusted/gh" and command[-1] == "repos/owner/repo"
    assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
    assert kwargs["env"]["GH_PAGER"] == "cat"
    assert kwargs["env"]["NO_COLOR"] == "1"


@pytest.mark.parametrize("body", ["", "null"])
def test_gh_empty_json_is_preserved(monkeypatch, body):
    install_fake_gh(monkeypatch, f"import sys; sys.stdout.write({body!r})")
    assert GitHubClient(timeout=5).get("test") is None


def test_gh_missing_detector_sees_more_than_error_tail(monkeypatch):
    install_fake_gh(
        monkeypatch,
        "import sys; sys.stderr.write('HTTP 404 Not Found'+'x'*1000); sys.exit(1)",
    )
    assert GitHubClient(timeout=5).get("test", allow_missing=True) is None
    with pytest.raises(ExternalCommandError):
        GitHubClient(timeout=5).get("test")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_gh_oversize_is_not_silently_missing(monkeypatch, stream):
    install_fake_gh(
        monkeypatch,
        f"import sys; sys.stderr.write('404'); sys.{stream}.write('x'*33); sys.exit(1)",
    )
    monkeypatch.setattr(github, "MAX_JSON_BYTES", 32)
    monkeypatch.setattr(github, "MAX_STDERR_BYTES", 32)
    with pytest.raises(ExternalCommandError, match="exceeds"):
        GitHubClient(timeout=5).get("test", allow_missing=True)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_graphql_bounds_both_pipes_before_buffering_the_response(monkeypatch, stream):
    install_fake_gh(
        monkeypatch,
        f"import sys,time; sys.{stream}.write('x'*33); sys.{stream}.flush(); time.sleep(30)",
    )
    monkeypatch.setattr(github, "MAX_JSON_BYTES", 32)
    monkeypatch.setattr(github, "MAX_STDERR_BYTES", 32)
    with pytest.raises(ExternalCommandError, match=f"gh {stream} exceeds"):
        GitHubClient(timeout=5).graphql("query Q { viewer { login } }", {})


def test_graphql_request_variables_round_trip_through_stdin(monkeypatch):
    install_fake_gh(
        monkeypatch,
        "import sys,json; payload=json.load(sys.stdin); print(json.dumps({'data':payload}))",
    )
    query = "query Q($name: String!) { viewer { login } }"
    variables = {"name": 'untrusted \\"query mutation\\" text'}
    assert GitHubClient(timeout=5).graphql(query, variables) == {
        "query": query,
        "variables": variables,
    }


def test_https_graphql_is_bounded_and_closes_the_response(monkeypatch):
    response = io.BytesIO(b"{}" + b" " * 100)
    monkeypatch.setattr(github.urllib.request, "urlopen", lambda *a, **k: response)
    monkeypatch.setattr(github, "MAX_JSON_BYTES", 16)
    with pytest.raises(ExternalCommandError, match="exceeds 16 bytes"):
        GitHubClient(prefer_gh=False).graphql("query Q { viewer { login } }", {})
    assert response.closed


@pytest.mark.parametrize("body", [b"", b"null", b'{"ok":true}'])
def test_https_client_parses_bounded_body_and_retains_headers(monkeypatch, body):
    response = io.BytesIO(body)
    calls = []

    def open_response(request, **kwargs):
        calls.append((request, kwargs))
        return response

    monkeypatch.setattr(github.urllib.request, "urlopen", open_response)
    monkeypatch.setenv("GH_TOKEN", "synthetic-test-token")
    assert GitHubClient(prefer_gh=False, timeout=7).get("test") == json.loads(body or b"null")
    request, kwargs = calls[0]
    assert request.get_header("Authorization") == "Bearer synthetic-test-token"
    assert kwargs["timeout"] == 7
    assert response.closed


def test_https_oversize_is_not_partial_json_or_missing(monkeypatch):
    response = io.BytesIO(b"{}" + b" " * 100)
    monkeypatch.setattr(github.urllib.request, "urlopen", lambda *a, **k: response)
    monkeypatch.setattr(github, "MAX_JSON_BYTES", 16)
    with pytest.raises(ExternalCommandError, match="exceeds 16 bytes"):
        GitHubClient(prefer_gh=False).get("test", allow_missing=True)
    assert response.closed


@pytest.mark.parametrize("status,missing", [(404, True), (404, False), (403, True)])
def test_https_http_error_semantics_are_preserved(monkeypatch, status, missing):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com/test", status, "error", {}, None)

    monkeypatch.setattr(github.urllib.request, "urlopen", fail)
    client = GitHubClient(prefer_gh=False)
    if status == 404 and missing:
        assert client.get("test", allow_missing=missing) is None
    else:
        with pytest.raises(ExternalCommandError, match=f"HTTP {status}"):
            client.get("test", allow_missing=missing)


# A stand-in for gh's host choice: `--hostname` first, then `GH_HOST`, then github.com. It
# answers with the host it would have asked, so a test sees where each request went.
FAKE_GH_HOST = """
import os, sys
args = sys.argv[1:]
if "--hostname" in args:
    host = args[args.index("--hostname") + 1]
else:
    host = os.environ.get("GH_HOST") or "github.com"
if args[-1].endswith("/zip"):
    sys.stdout.buffer.write(host.encode())
elif "graphql" in args:
    sys.stdin.buffer.read()
    sys.stdout.write('{"data": {"host": "%s"}}' % host)
else:
    sys.stdout.write('{"host": "%s"}' % host)
"""


def test_every_gh_call_asks_github_com_whatever_gh_host_says(monkeypatch):
    monkeypatch.setenv("GH_HOST", "ghe.example")
    calls = []

    def through_fake_gh(real):
        def call(command, **kwargs):
            if command[0] != "/trusted/gh":  # run_bounded's own Popen of the stand-in
                return real(command, **kwargs)
            calls.append((command, kwargs.get("env") or dict(os.environ)))
            return real([*child(FAKE_GH_HOST), *command[1:]], **kwargs)

        return call

    monkeypatch.setattr(github, "run_bounded", through_fake_gh(run_bounded))
    monkeypatch.setattr(github.subprocess, "run", through_fake_gh(subprocess.run))
    monkeypatch.setattr(github.subprocess, "Popen", through_fake_gh(subprocess.Popen))
    monkeypatch.setattr(github.shutil, "which", lambda _: "/trusted/gh")
    client = GitHubClient(timeout=10)

    assert client.get("repos/owner/repo") == {"host": "github.com"}
    assert client.graphql("query Q { viewer { login } }", {}) == {"host": "github.com"}
    assert client.artifact(Slug("owner", "repo"), 7) == b"github.com"
    assert len(calls) == 3
    for command, env in calls:
        assert command[1:4] == ["api", "--hostname", "github.com"]
        assert "GH_HOST" not in env


# gh's host for a call without `--hostname` and for a bare `--repo owner/repo`: `GH_HOST`, else
# the only host it is logged in to, else github.com. A URL or `host/owner/repo` names its own.
FAKE_GH_MAW_HOST = """
import os, sys
args = sys.argv[1:]
default = os.environ.get("GH_HOST") or os.environ.get("FAKE_GH_ONLY_LOGIN") or "github.com"
repo = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("--repo", "-R")), "")
url = next((a for a in args if a.startswith("https://")), "")
if args[0] == "api":
    host = args[args.index("--hostname") + 1] if "--hostname" in args else default
elif url:
    host = url.split("/")[2]
elif repo.count("/") == 2:
    host = repo.split("/")[0]
else:
    host = default
sys.stdout.write(host)
"""


@pytest.mark.parametrize(
    "args",
    [
        ("api", "repos/owner/maw", "--jq", ".default_branch"),
        ("api", "--paginate", "--slurp", "repos/owner/maw/pulls?state=all&per_page=100"),
        ("api", "--paginate", "repos/owner/maw/issues?state=all&per_page=100&direction=asc"),
        ("api", "user", "-q", ".login"),
        ("issue", "create", "--repo", "owner/maw", "--title", "t", "--body-file", "b.md"),
        ("label", "create", "crab", "--repo", "owner/maw", "--force"),
        ("pr", "create", "--repo", "owner/maw", "--head", "crab/x", "--base", "main"),
        ("pr", "close", "https://github.com/owner/maw/pull/1", "--repo", "owner/maw"),
    ],
)
def test_every_maw_gh_call_asks_github_com(monkeypatch, args):
    """The serve, loop and pull-request calls all run through ``GhIssueClient``."""
    monkeypatch.setenv("GH_HOST", "ghe.example")
    monkeypatch.setenv("FAKE_GH_ONLY_LOGIN", "ghe.example")
    real_run = subprocess.run

    def fake_gh(command, **kwargs):
        assert command[0] == "/trusted/gh"
        return real_run([*child(FAKE_GH_MAW_HOST), *command[1:]], **kwargs)

    monkeypatch.setattr(serve.subprocess, "run", fake_gh)
    assert GhIssueClient(gh="/trusted/gh", timeout=10).run_gh(*args) == "github.com"
