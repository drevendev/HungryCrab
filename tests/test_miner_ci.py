from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW
from helpers import read_json, read_md, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, DigestResult, run_digest
from hungry_crab.miners.ci import _matrix, parse_action, parse_workflow


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


def test_lookalike_marketplace_repositories_do_not_imply_ci_evidence() -> None:
    workflow = """name: Lookalike action names
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: codecov/codecov-action-helper@v1
      - uses: astral-sh/ruff-action-clone@v1
      - uses: github/codeql-action-fake@v1
      - uses: coverallsapp/github-action-substitute@v1
      - uses: paambaati/codeclimate-action-clone@v1
"""
    parsed = parse_workflow(workflow, ".github/workflows/lookalikes.yml")
    assert parsed is not None
    assert all(action["kind"] == "marketplace" for action in parsed["actions"])
    assert parsed["tools"] == []
    assert parsed["coverage"]["upload"] is None


def test_exact_marketplace_actions_and_nested_codeql_still_prove_ci_tools() -> None:
    workflow = """name: Known action names
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: codecov/codecov-action@v5
      - uses: astral-sh/ruff-action@v1
      - uses: github/codeql-action/analyze@v3
"""
    parsed = parse_workflow(workflow, ".github/workflows/known.yml")
    assert parsed is not None
    assert parsed["tools"] == ["codecov", "codeql", "ruff"]
    assert parsed["coverage"]["upload"] == "codecov"

    nested = workflow.replace("codecov/codecov-action@v5", "codecov/codecov-action/upload@v5")
    nested_parsed = parse_workflow(nested, ".github/workflows/nested.yml")
    assert nested_parsed is not None
    assert nested_parsed["tools"] == ["codecov", "codeql", "ruff"]
    assert nested_parsed["coverage"]["upload"] == "codecov"


def test_parse_action_does_not_classify_unresolved_expressions_as_literal() -> None:
    expressions = (
        "sample-org/Checker@${{github.sha}}",
        "  sample-org/Checker@${{ github.sha }}  ",
        "sample-org/Checker/subaction@${{inputs.ref}}",
        "sample-org/Checker@${{github.sha",
        "sample-org/Checker@github.sha}}",
        "./.github/actions/${{inputs.action}}",
        "docker://${{inputs.image}}",
    )
    for uses in expressions:
        assert parse_action(uses) == {
            "kind": "unknown",
            "action": uses.strip(),
            "ref": None,
            "pinned_sha": False,
        }


def test_parse_action_expression_guard_preserves_literal_references() -> None:
    for uses, kind, action, ref, pinned in (
        ("sample-org/Checker@v1", "marketplace", "sample-org/Checker", "v1", False),
        (
            "sample-org/Checker/subaction@main",
            "marketplace",
            "sample-org/Checker/subaction",
            "main",
            False,
        ),
        (
            "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
            "marketplace",
            "actions/checkout",
            "11bd71901bbe5b1630ceea73d27597364c9af683",
            True,
        ),
        ("./.github/actions/setup", "local", "./.github/actions/setup", None, False),
        ("docker://alpine:3.20", "docker", "docker://alpine:3.20", None, False),
    ):
        assert parse_action(uses) == {
            "kind": kind,
            "action": action,
            "ref": ref,
            "pinned_sha": pinned,
        }
    for uses in ("sample-org/Checker", "sample-org/Checker@", "@v1"):
        assert parse_action(uses)["kind"] == "unknown"


def test_parse_workflow_keeps_expression_action_unknown() -> None:
    workflow = """name: Expression
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: sample-org/Checker@${{github.sha}}
      - uses: sample-org/Checker/subaction@v1
"""
    parsed = parse_workflow(workflow, ".github/workflows/expr.yml")
    assert parsed is not None
    assert parsed["actions"][0] == {
        "kind": "unknown",
        "action": "sample-org/Checker@${{github.sha}}",
        "ref": None,
        "pinned_sha": False,
        "job": "test",
    }
    assert parsed["actions"][1]["kind"] == "marketplace"
    assert parsed["actions"][1]["action"] == "sample-org/Checker/subaction"


def test_unresolved_actions_do_not_infer_tools_or_coverage() -> None:
    workflow = """name: Unknown action refs
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: codecov/codecov-action@${{github.sha}}
      - uses: astral-sh/ruff-action@${{inputs.ref}}
      - uses: actions/checkout@v4
"""
    parsed = parse_workflow(workflow, ".github/workflows/unknown.yml")
    assert parsed is not None
    assert [a["kind"] for a in parsed["actions"]] == [
        "unknown",
        "unknown",
        "marketplace",
    ]
    assert parsed["tools"] == []
    assert parsed["coverage"]["upload"] is None
    assert parsed["cache"] is False

    literal = workflow.replace("${{github.sha}}", "v5").replace("${{inputs.ref}}", "v1")
    resolved = parse_workflow(literal, ".github/workflows/resolved.yml")
    assert resolved is not None
    assert resolved["tools"] == ["codecov", "ruff"]
    assert resolved["coverage"]["upload"] == "codecov"


def test_parse_action_sha_pin_accepts_both_ascii_hex_cases() -> None:
    lower = "11bd71901bbe5b1630ceea73d27597364c9af683"
    for ref in (lower, lower.upper(), lower[:20].upper() + lower[20:]):
        assert parse_action(f"actions/checkout@{ref}") == {
            "kind": "marketplace",
            "action": "actions/checkout",
            "ref": ref,
            "pinned_sha": True,
        }
    for ref in (lower[:-1], lower + "a", "g" + lower[1:], "\uff26" + lower[1:], "v4"):
        assert parse_action(f"actions/checkout@{ref}") == {
            "kind": "marketplace",
            "action": "actions/checkout",
            "ref": ref,
            "pinned_sha": False,
        }
    assert parse_action("./.github/actions/setup")["kind"] == "local"
    assert parse_action("docker://alpine:3.20")["kind"] == "docker"

    workflow = f"""name: Uppercase SHA
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{lower.upper()}
"""
    parsed = parse_workflow(workflow, ".github/workflows/uppercase-sha.yml")
    assert parsed is not None
    assert parsed["actions"][0]["pinned_sha"] is True
    assert parsed["actions"][0]["ref"] == lower.upper()


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
    assert features["coverage_upload"] == "codecov"
    assert features["coverage_flags"] == ["--coverage"]
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


def test_go_ci_records_what_it_does_about_coverage(go_digest: DigestResult) -> None:
    data = read_json(go_digest, "ci.json")
    features = data["features"]
    assert features["coverage_upload"] == "codecov"
    assert features["coverage_flags"] == ["-coverprofile"]
    workflow = data["workflows"][0]
    assert workflow["coverage"] == {"flags": ["-coverprofile"], "upload": "codecov"}


def test_coverage_flags_are_matched_as_flags_not_as_words() -> None:
    steps = {
        "go test -cover ./...": ["-cover"],
        "go test -coverprofile=c.out ./...": ["-coverprofile"],
        "pytest --cov=pkg --cov-report=xml": ["--cov"],
        "pytest --cov": ["--cov"],
        "npx vitest run --coverage": ["--coverage"],
        'dotnet test --collect:"XPlat Code Coverage"': ["--collect XPlat Code Coverage"],
        "dotnet test -p:CollectCoverage=true": ["-p:CollectCoverage"],
        "coverage run -m pytest": ["coverage run"],
        "cargo llvm-cov --lcov --output-path lcov.info": ["cargo llvm-cov"],
        "echo coverage is nice; go test ./...": [],
        "pytest --cov-fail-under=80": [],
        "./discover ./...": [],
    }
    head = "name: x\non: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
    for run, expected in steps.items():
        parsed = parse_workflow(f"{head}      - run: {run!r}\n", ".github/workflows/x.yml")
        assert parsed is not None
        assert parsed["coverage"]["flags"] == expected, run
        assert parsed["coverage"]["upload"] is None


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


def test_matrix_exact_literal_cartesian_preserves_existing_size() -> None:
    matrix = _matrix(
        {
            "matrix": {"os": ["ubuntu-latest", "windows-latest"], "python": ["3.11", "3.14"]},
            "fail-fast": False,
        }
    )
    assert matrix is not None
    assert matrix["axes"] == {"os": 2, "python": 2}
    assert matrix["size"] == 4
    assert matrix["base_size"] == 4
    assert matrix["size_status"] == "exact"
    assert matrix["fail_fast"] is False


def test_matrix_unresolved_axis_is_not_a_singleton() -> None:
    matrix = _matrix({"matrix": {"os": "${{ inputs.platforms }}", "python": ["3.11"]}})
    assert matrix is not None
    assert matrix["axes"] == {"os": None, "python": 1}
    assert matrix["base_size"] is None
    assert matrix["size"] is None
    assert matrix["size_status"] == "unknown-axes"


def test_matrix_modifiers_do_not_fabricate_exact_size() -> None:
    # Include may modify a base row rather than add a new one.
    included = _matrix(
        {
            "matrix": {
                "os": ["ubuntu", "windows"],
                "include": [{"os": "ubuntu", "extra": "x"}],
            }
        }
    )
    assert included is not None
    assert included["base_size"] == 2
    assert included["include_count"] == 1
    assert included["size"] is None
    assert included["size_status"] == "unknown-modifiers"

    # Exclude removes a row, so reporting the unadjusted base as exact is wrong.
    excluded = _matrix({"matrix": {"os": ["ubuntu", "windows"], "exclude": [{"os": "windows"}]}})
    assert excluded is not None
    assert excluded["base_size"] == 2
    assert excluded["exclude_count"] == 1
    assert excluded["size"] is None
    assert excluded["size_status"] == "unknown-modifiers"


def test_matrix_unsupported_values_are_unknown_not_singletons() -> None:
    values = (
        "ubuntu-latest",
        {"os": "ubuntu-latest"},
        ["${{ fromJSON(inputs.platforms) }}"],
    )
    for value in values:
        matrix = _matrix({"matrix": {"os": value}})
        assert matrix is not None
        assert matrix["axes"] == {"os": None}
        assert matrix["size"] is None
        assert matrix["size_status"] == "unknown-axes"


def test_parse_workflow_preserves_unknown_matrix_cardinality() -> None:
    workflow = """name: Dynamic matrix
on: push
jobs:
  tests:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        os: '${{ fromJSON(inputs.platforms) }}'
        python: ['3.11', '3.14']
        exclude:
          - python: '3.14'
    steps:
      - run: echo ok
"""
    parsed = parse_workflow(workflow, ".github/workflows/dynamic.yml")
    assert parsed is not None
    matrix = parsed["jobs"][0]["matrix"]
    assert matrix is not None
    assert matrix["axes"] == {"os": None, "python": 2}
    assert matrix["size"] is None
    assert matrix["base_size"] is None
    assert matrix["size_status"] == "unknown-axes"
    assert matrix["exclude_count"] == 1


def test_matrix_uncertainty_survives_ci_json_and_markdown(tmp_path: Path) -> None:
    """Exercise the actual miner outputs, including the Jobs table rendering."""
    root = write_tree(
        tmp_path / "matrix-prey",
        {
            "README.md": "# Synthetic matrix fixture\n",
            ".github/workflows/matrix.yml": """name: Matrix bounds
on: push
jobs:
  dynamic:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        os: '${{ fromJSON(inputs.platforms) }}'
        python: ['3.11', '3.14']
        exclude:
          - python: '3.14'
    steps:
      - run: echo ok
  literal-modified:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        include:
          - os: ubuntu-latest
            extra: yes
    steps:
      - run: echo ok
""",
        },
    )
    result = run_digest(
        Target(path=root),
        DigestOptions(
            out=tmp_path / "digest",
            now=FIXED_NOW,
            miners=["ci"],
            cache_root=tmp_path / "cache",
        ),
    )
    workflow = next(
        wf for wf in read_json(result, "ci.json")["workflows"]
        if wf["path"] == ".github/workflows/matrix.yml"
    )
    jobs = {job["id"]: job["matrix"] for job in workflow["jobs"]}
    dynamic = jobs["dynamic"]
    assert dynamic["axes"] == {"os": None, "python": 2}
    assert dynamic["size"] is None
    assert dynamic["base_size"] is None
    assert dynamic["size_status"] == "unknown-axes"
    assert dynamic["exclude_count"] == 1

    modified = jobs["literal-modified"]
    assert modified["axes"] == {"os": 2}
    assert modified["size"] is None
    assert modified["base_size"] == 2
    assert modified["size_status"] == "unknown-modifiers"
    assert modified["include_count"] == 1

    markdown = read_md(result, "ci.md")
    assert "osx?" in markdown
    assert "osxNone" not in markdown
    assert "osx2" in markdown
