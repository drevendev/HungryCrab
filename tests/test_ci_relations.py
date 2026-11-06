"""Receiving-owned, inert fixtures for the bounded CI call relation sidecar."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from helpers import read_json

from hungry_crab.digest import DigestResult
from hungry_crab.miners.ci import parse_workflow
from hungry_crab.miners.ci_relations import build_ci_relations


def _workflow(
    path: str, *, calls: dict[str, str] | None = None, reusable: bool = False
) -> dict[str, Any]:
    on = "workflow_call" if reusable else "push"
    jobs = "\n".join(f"  {job}:\n    uses: {uses}" for job, uses in (calls or {}).items())
    if not jobs:
        jobs = "  noop:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe"
    parsed = parse_workflow(f"name: x\non: {on}\njobs:\n{jobs}\n", path)
    assert parsed is not None
    return parsed


def test_local_reusable_resolution_and_source_rows_remain_unchanged() -> None:
    caller = _workflow(
        ".github/workflows/a.yml",
        calls={
            "unit": "./.github/workflows/library.yml",
            "integration": "./.github/workflows/library.yml",
            "not_callable": "./.github/workflows/plain.yaml",
            "not_indexed": "./.github/workflows/unknown.yml",
        },
    )
    target = _workflow(".github/workflows/library.yml", reusable=True)
    plain = _workflow(".github/workflows/plain.yaml")
    input_rows = [target, plain, caller]
    original = deepcopy(input_rows)
    got = build_ci_relations(input_rows)
    assert input_rows == original
    assert got["schema"] == "hungry-crab.ci-relations/1"
    assert got["truncated"] is False
    assert [(r["caller_job"], r["status"]) for r in got["relations"]] == [
        ("integration", "resolved-workflow-call"),
        ("not_callable", "target-not-workflow-call"),
        ("not_indexed", "not-in-supplied-inventory"),
        ("unit", "resolved-workflow-call"),
    ]
    assert [r["target_path"] for r in got["relations"]][:2] == [
        ".github/workflows/library.yml",
        ".github/workflows/plain.yaml",
    ]


@pytest.mark.parametrize(
    ("uses", "status"),
    [
        ("org/repo/.github/workflows/reuse.yml@v1", "remote-unresolved"),
        ("./.github/workflows/${{ inputs.name }}.yml", "expression-unresolved"),
        ("./.github/workflows/../secret.yml", "invalid-local-path"),
        ("./.github/workflows/nested/wf.yml", "invalid-local-path"),
        ("./.github/workflows/a.yml?ref=main", "invalid-local-path"),
        ("../.github/workflows/a.yml", "invalid-local-path"),
        (r"..\..\.github\workflows\a.yml", "invalid-local-path"),
        (r".\.github\workflows\a.yml", "invalid-local-path"),
        ("/.github/workflows/a.yml", "invalid-local-path"),
    ],
)
def test_external_expression_and_lexical_escapes_fail_closed(uses: str, status: str) -> None:
    caller = _workflow(".github/workflows/a.yml", calls={"call": uses})
    record = build_ci_relations([caller])["relations"][0]
    assert record["raw_uses"] == uses
    assert record["target_path"] is None
    assert record["status"] == status


def test_bounded_emission_and_deterministic_job_order() -> None:
    caller = _workflow(
        ".github/workflows/a.yml",
        calls={"z": "./.github/workflows/lib.yml", "a": "./.github/workflows/lib.yml"},
    )
    target = _workflow(".github/workflows/lib.yml", reusable=True)
    result = build_ci_relations([target, caller], relation_limit=1)
    assert result["relations"][0]["caller_job"] == "a"
    assert len(result["relations"]) == 1
    assert result["truncation"] == {"workflows": False, "relations": True, "bytes": False}
    assert build_ci_relations([caller, target], relation_limit=1) == result


def test_inventory_cap_does_not_claim_target_absence() -> None:
    caller = _workflow(".github/workflows/a.yml", calls={"call": "./.github/workflows/z.yml"})
    target = _workflow(".github/workflows/z.yml", reusable=True)
    one = build_ci_relations([target, caller], workflow_limit=1)
    assert one["indexed_workflows"] == 1
    assert one["truncation"] == {"workflows": True, "relations": False, "bytes": False}
    assert one["relations"][0]["status"] == "not-in-supplied-inventory"
    all_rows = [_workflow(f".github/workflows/{i:03d}.yml", reusable=True) for i in range(65)]
    capped = build_ci_relations(all_rows)
    assert capped["inventory_workflows"] == 65
    assert capped["indexed_workflows"] == 64
    assert capped["truncated"] is True
    assert capped["relations"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workflow_limit": 0},
        {"workflow_limit": 65},
        {"relation_limit": 257},
        {"byte_limit": 0},
        {"byte_limit": 1_048_577},
    ],
)
def test_caps_cannot_be_raised_or_disabled(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        build_ci_relations([], **kwargs)


def test_large_raw_uses_is_byte_bounded_without_silent_truncation() -> None:
    huge_uses = "remote/" + ("x" * 100_000)
    caller = {
        "path": ".github/workflows/a.yml",
        "jobs": [{"id": f"call-{i:03d}", "uses": huge_uses} for i in range(20)],
    }
    original = deepcopy(caller)
    result = build_ci_relations([caller])
    assert caller == original
    assert 0 < len(result["relations"]) < 20
    assert result["truncated"] is True
    assert result["truncation"] == {"workflows": False, "relations": False, "bytes": True}
    assert result["relation_bytes"] <= result["byte_limit"] == 1_048_576
    assert all(row["raw_uses"] == huge_uses for row in result["relations"])

    tiny = build_ci_relations([caller], byte_limit=100)
    assert tiny["relations"] == []
    assert tiny["truncation"]["bytes"] is True



def _nested_relation_payload_bytes(rows: list[dict[str, str | None]]) -> int:
    """Actual contribution of relations[] in digest's nested UTF-8 JSON format."""

    def serialized_size(records: list[dict[str, str | None]]) -> int:
        return len(json.dumps({"relations": records}, indent=2, ensure_ascii=False).encode("utf-8"))

    return serialized_size(rows) - serialized_size([])


@pytest.mark.parametrize(
    "raw_uses",
    [
        "remote/" + ("x" * 3950),
        "remote/" + (("\\" + '"' + "Ω") * 1500),
    ],
)
def test_relation_byte_budget_bounds_nested_json(raw_uses: str) -> None:
    caller = {
        "path": ".github/workflows/a.yml",
        "jobs": [{"id": f"call-{i:03d}", "uses": raw_uses} for i in range(256)],
    }
    source = deepcopy(caller)
    result = build_ci_relations([caller])
    assert caller == source
    assert 0 < len(result["relations"]) < 256
    assert result["truncation"]["bytes"] is True
    assert result["relation_bytes"] <= result["byte_limit"] == 1_048_576
    assert _nested_relation_payload_bytes(result["relations"]) <= result["relation_bytes"]
    assert all(row["raw_uses"] == raw_uses for row in result["relations"])


def test_digest_emits_separate_versioned_relation_artifact(npm_digest: DigestResult) -> None:
    original = read_json(npm_digest, "ci.json")
    sidecar = read_json(npm_digest, "ci_relations.json")
    assert sidecar["schema"] == "hungry-crab.ci-relations/1"
    assert sidecar["inventory_workflows"] == len(original["workflows"])
    assert isinstance(sidecar["relations"], list)
    assert original["features"]["provider"] == "github-actions"
