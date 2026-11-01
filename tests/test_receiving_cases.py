"""No-op and partially fixed cases use the selected receiving flow, never donor tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_discovery_inputs import repository

from hungry_crab.errors import CrabError
from hungry_crab.receiving_cases import project_results, run_cases


def test_projection_preserves_actual_checks_without_whole_return_objects(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving",
        {"app.py": "def consume():\n    return {'count': 2, 'large': 'x' * 4096}\n"},
    )
    plan = definition()
    for index, case in enumerate(plan["cases"]):
        case.update(args=[], checks=[{"pointer": "/value/count", "equals": 3 if index == 0 else 2}])
    receipt = run_cases(tmp_path / "cases", maw, plan)
    projected = project_results(receipt, maw)
    assert projected["cases"][0]["passed"] is False
    assert projected["cases"][1]["passed"] is True
    assert projected["cases"][0]["checks"][0]["observed"] == 2
    assert projected["actual_receiving_calls"] == [plan["target"]]
    assert "x" * 100 not in json.dumps(projected)
    assert "x" * 4096 in (receipt.parent / "stdout.txt").read_text(encoding="utf-8")
    assert not projected["omissions"]
    # The returned values are projected from the exact verified stdout bytes.
    (receipt.parent / "stdout.txt").write_text('{"cases": []}', encoding="utf-8")
    with pytest.raises(CrabError, match="artifact changed"):
        project_results(receipt, maw)


def test_large_check_values_are_hashed_omissions_not_silent_success(tmp_path: Path) -> None:
    maw = repository(tmp_path / "receiving", {"app.py": "def consume():\n    return 'a' * 1024\n"})
    plan = definition()
    for case in plan["cases"]:
        case.update(args=[], checks=[{"pointer": "/value", "equals": "a" * 1024}])
    receipt = run_cases(tmp_path / "cases", maw, plan)
    projected = project_results(receipt, maw, value_bytes=64)
    check = projected["cases"][0]["checks"][0]
    assert check["passed"] is True  # The original assertion actually passed.
    assert check["observed"]["omitted"] is True
    assert check["observed"]["bytes"] == 1026
    assert len(check["observed"]["sha256"]) == 64
    assert projected["omissions"] and projected["value_bytes_retained"] < 1024
    with pytest.raises(CrabError, match=r"64\.\.8192"):
        project_results(receipt, maw, value_bytes=True)


def definition() -> dict[str, Any]:
    return {
        "original_receiving_owned": True,
        "author": "original case fixture host",
        "target": {"path": "app.py", "qualified_name": "consume"},
        "cases": [
            {
                "label": "bounded target",
                "role": "target",
                "args": [{"$stream": "abcd"}],
                "checks": [{"pointer": "/value/base64", "equals": "YWI="}],
            },
            {
                "label": "ordinary input control",
                "role": "positive-control",
                "args": [{"$io": "ab"}],
                "checks": [{"pointer": "/value/base64", "equals": "YWI="}],
            },
        ],
    }


def test_original_encoded_sources_keep_exact_git_blob_identity(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving",
        {
            "app.py": "import base64, hashlib\n"
            "def consume(data):\n"
            "    value = base64.b64decode(data['source'])\n"
            "    expected = hashlib.sha1(b'blob ' + str(len(value)).encode() "
            "+ b'\\0' + value).hexdigest()\n"
            "    return expected == data['object_id']\n"
        },
    )
    plan = definition()
    for case, source in zip(plan["cases"], ("name = '☃'\n", ""), strict=True):
        case["args"] = [{"source": {"$base64": source}, "object_id": {"$git_blob_sha1": source}}]
        case["checks"] = [{"pointer": "/value", "equals": True}]
    path = run_cases(tmp_path / "cases", maw, plan)
    result = json.loads((path.parent / "stdout.txt").read_text())
    assert all(c["passed"] for c in result["cases"])
    plan["cases"][0]["args"] = [{"$base64": []}]
    with pytest.raises(CrabError, match="text values"):
        run_cases(tmp_path / "invalid", maw, plan)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("fixed", [False, True])
def test_original_cases_distinguish_already_correct_and_remaining_gap(
    tmp_path: Path, fixed: bool
) -> None:
    expression = "stream.read(2)" if fixed else "stream.read()"
    maw = repository(
        tmp_path / "receiving", {"app.py": "def consume(stream):\n    return " + expression + "\n"}
    )
    receipt_path = run_cases(tmp_path / "cases", maw, definition())
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "executed" and receipt["exit_code"] == 0
    assert receipt["required_receiving_calls"] == receipt["observed_receiving_calls"]
    result = json.loads((receipt_path.parent / "stdout.txt").read_text())
    assert result["cases"][0]["passed"] is fixed
    assert result["cases"][1]["passed"] is True
    assert not result["donor_executed"] and result["human_acceptance"] is None


def test_cases_reject_escaping_fixtures_and_missing_controls_before_launch(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving", {"app.py": "def consume(stream):\n    return stream.read(2)\n"}
    )
    plan = definition()
    plan["cases"][0]["files"] = {"../escape.py": "raise Exception()"}
    with pytest.raises(CrabError, match="relative text fixtures"):
        run_cases(tmp_path / "not-created", maw, plan)
    assert not (tmp_path / "not-created").exists()
    plan = definition()
    plan["cases"][1]["role"] = "target"
    with pytest.raises(CrabError, match="positive control"):
        run_cases(tmp_path / "no-control", maw, plan)


def test_cases_allow_only_pinned_receiving_helpers(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving",
        {
            "app.py": "def defaults():\n    return {'value': 3}\n"
            "def consume(data):\n    return data['value'] + 1\n"
        },
    )
    plan = definition()
    plan["helpers"] = {"policy": "defaults"}
    for case in plan["cases"]:
        case.update(args=[{"$helper": "policy"}], checks=[{"pointer": "/value", "equals": 4}])
    path = run_cases(tmp_path / "helpers", maw, plan)
    assert all(c["passed"] for c in json.loads((path.parent / "stdout.txt").read_text())["cases"])
    plan["helpers"] = {"policy": "__import__"}
    with pytest.raises(CrabError, match="helpers"):
        run_cases(tmp_path / "refused", maw, plan)


def test_original_generator_case_is_executed_and_bounded(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving", {"app.py": "def consume():\n    yield from range(1000)\n"}
    )
    plan = definition()
    for case in plan["cases"]:
        case.update(args=[], checks=[{"pointer": "/value/truncated", "equals": True}])
    path = run_cases(tmp_path / "generator", maw, plan)
    result = json.loads((path.parent / "stdout.txt").read_text())
    assert all(c["passed"] for c in result["cases"])
    assert len(result["cases"][0]["actual"]["value"]["items"]) == 128


def test_private_diagnostic_predicates_compare_original_sentinel_without_code(
    tmp_path: Path,
) -> None:
    maw = repository(
        tmp_path / "receiving",
        {
            "app.py": "def consume(value):\n    if value:\n        raise ValueError(value)\n"
            "    return 'ok'\n"
        },
    )
    plan = definition()
    plan["cases"][0].update(
        args=["ORIGINAL_PRIVATE_SENTINEL"],
        checks=[{"pointer": "/exception/message", "not_contains": "ORIGINAL_PRIVATE_SENTINEL"}],
    )
    plan["cases"][1].update(args=[""], checks=[{"pointer": "/value", "equals": "ok"}])
    path = run_cases(tmp_path / "diagnostic", maw, plan)
    result = json.loads((path.parent / "stdout.txt").read_text())
    assert not result["cases"][0]["passed"] and result["cases"][1]["passed"]
    assert result["cases"][0]["checks"][0]["check_error"] is None
