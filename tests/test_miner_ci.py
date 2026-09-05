from __future__ import annotations

from helpers import read_json, read_md

from hungry_crab.digest import DigestResult
from hungry_crab.miners.ci import parse_action, parse_workflow


def test_parse_action_variants() -> None:
    tagged = parse_action("actions/checkout@v4")
    assert tagged == {
        "kind": "marketplace",
        "action": "actions/checkout",
        "ref": "v4",
        "pinned_sha": False,
    }
    sha = parse_action("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683")
    assert sha["pinned_sha"] is True
    nested = parse_action("github/codeql-action/analyze@v3")
    assert nested["action"] == "github/codeql-action/analyze"
    assert parse_action("./.github/actions/setup")["kind"] == "local"
    assert parse_action("docker://alpine:3.20")["kind"] == "docker"


def test_parse_workflow_handles_yaml_on_key_and_reusable_calls() -> None:
    text = """name: Reuse
on:
  workflow_call:
  schedule:
    - cron: "0 3 * * 1"
permissions: read-all
jobs:
  call:
    uses: org/repo/.github/workflows/lint.yml@main
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - run: curl -fsSL https://example.com/install.sh | bash
      - uses: actions/setup-go@v5
"""
    parsed = parse_workflow(text, ".github/workflows/reuse.yml")
    assert parsed is not None
    assert parsed["triggers"] == ["workflow_call", "schedule"]
    assert parsed["trigger_details"]["schedule"] == ["0 3 * * 1"]
    assert parsed["reusable"] is True
    assert parsed["reusable_calls"] == 1
    assert parsed["permissions"] == "read-all"
    assert parsed["timeouts"] == 1
    assert parsed["pipe_to_shell"] == 1
    assert parsed["cache"] is True  # setup-go caches by default


def test_npm_ci_features(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "ci.json")
    features = data["features"]
    assert features["provider"] == "github-actions"
    assert features["workflows"] == 2
    assert features["cache"] is True
    assert features["matrix"] is True
    assert features["concurrency"] is True
    assert features["permissions_top_level"] is True
    assert features["timeouts"] is True
    assert features["release_automation"] is True
    assert features["dependabot"] is True
    assert features["renovate"] is False
    assert features["windows_runner"] is True
    assert features["macos_runner"] is False
    assert features["actions_sha_pinned_ratio"] == 0.12
    assert features["secrets"] == ["CODECOV_TOKEN"]
    assert {"npm test", "playwright", "codecov", "coverage", "npm build"} <= set(features["tools"])
    assert features["pipe_to_shell_steps"] == 0
    checkout = data["actions"]["actions/checkout"]
    assert checkout["count"] == 2
    assert checkout["pinned_sha"] is True
    assert "v4.2.2" in checkout["refs"]
    ci = next(wf for wf in data["workflows"] if wf["path"].endswith("ci.yml"))
    job = ci["jobs"][0]
    assert job["matrix"]["axes"] == {"os": 2, "node": 2}
    assert job["matrix"]["size"] == 4
    assert data["dependabot"]["updates"][0]["ecosystem"] == "npm"
    assert data["dependabot"]["updates"][0]["groups"] == ["dev-dependencies"]
    text = read_md(npm_digest, "ci.md")
    assert "## Workflows" in text and "## Actions used" in text
    assert "CODECOV_TOKEN" in text


def test_python_ci_gaps(py_digest: DigestResult) -> None:
    features = read_json(py_digest, "ci.json")["features"]
    assert features["cache"] is False
    assert features["concurrency"] is False
    assert features["matrix"] is True
    assert features["macos_runner"] is True
    assert features["release_automation"] is True
    assert features["dependabot"] is False
    assert {"ruff", "mypy", "pytest", "uv"} <= set(features["tools"])


def test_dotnet_ci_gaps(dotnet_digest: DigestResult) -> None:
    features = read_json(dotnet_digest, "ci.json")["features"]
    assert features["workflows"] == 1
    assert features["cache"] is False
    assert features["matrix"] is False
    assert features["permissions_top_level"] is False
    assert features["permissions_per_job"] is False
    assert {"dotnet build", "dotnet test"} <= set(features["tools"])
    assert features["other_ci"] == []
