"""Run original JSON cases through a fixed trusted receiving-only probe template."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .analysis_context import fingerprint
from .artifact_read import parse_object, read_bytes, read_object
from .compare.candidates import _evidence_path
from .errors import CrabError
from .receiving_probe import run_probe, verify_receipt


def project_results(receipt_path: Path, maw: Path, *, value_bytes: int = 512) -> dict[str, Any]:
    """Project verified calls/checks; retain full result identities and explicit omissions.

    This is evidence transport, not a semantic verdict. Full returned objects are
    deliberately kept in the original hash-verified stdout rather than repeated
    for every model stage. A failed assertion still needs usefulness review.
    """
    from .discovery_session import live_context

    if type(value_bytes) is not int or not 64 <= value_bytes <= 8192:
        raise CrabError("case projection value limit must be 64..8192 bytes")
    context = live_context(maw)
    raw = read_object(receipt_path)
    receipt = verify_receipt(receipt_path, context, str(raw.get("probe_sha256", "")))
    if receipt.get("import_receiving") is not True or not receipt.get("required_receiving_calls"):
        raise CrabError("case projection requires actual pinned receiving target calls")
    stdout = read_bytes(receipt_path.parent / "stdout.txt", 65536)
    if hashlib.sha256(stdout).hexdigest() != receipt["artifacts"]["stdout.txt"]:
        raise CrabError("executed probe artifact changed before projection")
    data = parse_object(stdout, name="stdout.txt")
    cases = data.get("cases")
    if not isinstance(cases, list) or not 2 <= len(cases) <= 8:
        raise CrabError("receiving result needs 2..8 original cases")
    remaining = 32000
    omissions: list[dict[str, Any]] = []

    def value(item: Any, location: str) -> Any:
        nonlocal remaining
        encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > value_bytes or len(encoded) > remaining:
            omitted = {
                "omitted": True,
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
            omissions.append({"field": location, **omitted})
            return omitted
        remaining -= len(encoded)
        return item

    projected = []
    roles = set()
    for index, case in enumerate(cases):
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("label"), str)
            or not 0 < len(case["label"]) <= 200
            or case.get("role") not in {"target", "positive-control", "negative-control"}
            or type(case.get("passed")) is not bool
            or not isinstance(case.get("actual"), dict)
            or (case.get("target_called") is not None and type(case["target_called"]) is not bool)
            or not isinstance(case.get("checks"), list)
            or not 1 <= len(case["checks"]) <= 12
        ):
            raise CrabError("invalid receiving case result")
        roles.add(case["role"])
        checks = []
        for number, check in enumerate(case["checks"]):
            if (
                not isinstance(check, dict)
                or set(check) != {"pointer", "expected", "observed", "passed", "check_error"}
                or not isinstance(check["pointer"], str)
                or not check["pointer"].startswith("/")
                or len(check["pointer"]) > 500
                or type(check["passed"]) is not bool
                or (check["check_error"] is not None and not isinstance(check["check_error"], str))
            ):
                raise CrabError("invalid receiving check result")
            checks.append(
                {
                    **{k: check[k] for k in ("pointer", "passed", "check_error")},
                    **{
                        k: value(check[k], f"cases/{index}/checks/{number}/{k}")
                        for k in ("expected", "observed")
                    },
                }
            )
        projected.append(
            {
                **{k: case[k] for k in ("label", "role", "passed")},
                "target_called": case.get("target_called"),
                "checks": checks,
                "actual_exception": value(
                    case["actual"].get("exception"), f"cases/{index}/exception"
                ),
                "exception_origin": case["actual"].get("exception_origin"),
                "actual_result_sha256": fingerprint(case["actual"]),
            }
        )
    if not {"target", "positive-control"} <= roles:
        raise CrabError("receiving result lacks target or positive control")
    result = {
        "schema": "hungry-crab.receiving-case-projection/1",
        "status": receipt["status"],
        "context": context,
        "probe_sha256": receipt["probe_sha256"],
        "receipt_sha256": hashlib.sha256(read_bytes(receipt_path, 65536)).hexdigest(),
        "artifacts": receipt["artifacts"],
        "actual_receiving_calls": receipt["observed_receiving_calls"],
        "target_calls_complete": all(
            case.get("target_called") is True
            for case in cases
            if case["role"] in {"target", "positive-control"}
        ),
        "case_results_usable": all(
            case.get("target_called") is True
            and case["actual"].get("exception_origin")
            not in {"verification-harness", "unknown-traceback-bound"}
            for case in cases
            if case["role"] in {"target", "positive-control"}
        ),
        "cases": projected,
        "omissions": omissions,
        "value_byte_limit": value_bytes,
        "value_bytes_retained": 32000 - remaining,
        "interpretation": (
            "Verified executed calls/checks only; semantic usefulness review required. "
            "Unshown values stay in hash-verified stdout; omissions are not passing values."
        ),
    }
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 65536:
        raise CrabError("case projection exceeds 64 KiB; use a smaller value limit")
    return result


def _data(value: Any, *, depth: int = 0) -> None:
    if depth > 20:
        raise CrabError("original case data nesting limit")
    if isinstance(value, dict):
        if "$fixture" in value:
            path = value["$fixture"]
            if (
                set(value) != {"$fixture"}
                or not isinstance(path, str)
                or (path and not _evidence_path(path))
            ):
                raise CrabError("case fixture reference must stay inside its temporary root")
        elif any(str(k).startswith("$") for k in value) and set(value) not in (
            {"$bytes"},
            {"$stream"},
            {"$io"},
            {"$helper"},
            {"$base64"},
            {"$git_blob_sha1"},
        ):
            raise CrabError("unsupported original case data factory")
        elif any(str(k).startswith("$") for k in value) and not isinstance(
            next(iter(value.values())), str
        ):
            raise CrabError("original case data factories require text values")
        for child in value.values():
            _data(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _data(child, depth=depth + 1)
    elif value is not None and not isinstance(value, str | int | float | bool):
        raise CrabError("original cases accept JSON data only")


def run_cases(directory: Path, maw: Path, plan: dict[str, Any]) -> Path:
    if plan.get("original_receiving_owned") is not True or len(json.dumps(plan).encode()) > 32768:
        raise CrabError("cases require bounded original receiving-owned data")
    target = plan.get("target")
    if not isinstance(target, dict) or set(target) != {"path", "qualified_name"}:
        raise CrabError("cases require one explicit pinned receiving function")
    if (
        not isinstance(target["path"], str)
        or not _evidence_path(target["path"])
        or not target["path"].endswith(".py")
    ):
        raise CrabError("invalid receiving Python module path")
    parts = list(PurePosixPath(target["path"]).with_suffix("").parts)
    if (
        any(not p.isidentifier() for p in parts)
        or not isinstance(target["qualified_name"], str)
        or any(
            not p.isidentifier() or p.startswith("__") for p in target["qualified_name"].split(".")
        )
    ):
        raise CrabError("unsupported receiving module or function name")
    cases = plan.get("cases")
    if not isinstance(cases, list) or not 2 <= len(cases) <= 8:
        raise CrabError("receiving cases need a target and a positive control (2..8 cases)")
    roles = set()
    helpers = plan.get("helpers", {})
    if (
        not isinstance(helpers, dict)
        or len(helpers) > 4
        or any(
            not isinstance(k, str)
            or not k.isidentifier()
            or not isinstance(v, str)
            or any(not p.isidentifier() or p.startswith("__") for p in v.split("."))
            for k, v in helpers.items()
        )
    ):
        raise CrabError("case helpers must be explicit functions in the selected receiving module")
    if helpers:
        from .attribution import GitSourceReader
        from .discovery_session import live_context
        from .source_inspection import syntax_query

        text = GitSourceReader(maw).read(live_context(maw)["sha"], target["path"])
        declarations = (
            syntax_query(text, [{"kind": "function", "name": name} for name in helpers.values()])
            if text is not None
            else {}
        )
        known = {r.get("qualified_name", r.get("name")) for r in declarations.get("matches", [])}
        if not set(helpers.values()) <= known:
            raise CrabError("case helper is not a pinned receiving function")
    for case in cases:
        if not isinstance(case, dict) or set(case) - {
            "label",
            "role",
            "args",
            "kwargs",
            "files",
            "checks",
        }:
            raise CrabError("unknown receiving case field")
        if (
            not isinstance(case.get("label"), str)
            or not 0 < len(case["label"]) <= 200
            or case.get("role") not in {"target", "positive-control", "negative-control"}
        ):
            raise CrabError("receiving case needs a bounded label and explicit role")
        roles.add(case["role"])
        if not isinstance(case.get("args", []), list) or not isinstance(
            case.get("kwargs", {}), dict
        ):
            raise CrabError("case positional/keyword arguments must be JSON arrays/objects")
        _data(case.get("args", []))
        _data(case.get("kwargs", {}))
        files = case.get("files", {})
        if (
            not isinstance(files, dict)
            or len(files) > 16
            or any(
                not isinstance(p, str) or not _evidence_path(p) or not isinstance(text, str)
                for p, text in files.items()
            )
        ):
            raise CrabError("case files require bounded regular relative text fixtures")
        checks = case.get("checks")
        if (
            not isinstance(checks, list)
            or not 1 <= len(checks) <= 12
            or any(
                not isinstance(c, dict)
                or set(c)
                not in ({"pointer", "equals"}, {"pointer", "contains"}, {"pointer", "not_contains"})
                or not isinstance(c["pointer"], str)
                or not c["pointer"].startswith("/")
                for c in checks
            )
        ):
            raise CrabError("case checks require explicit JSON pointers and expected values")
    if not {"target", "positive-control"} <= roles:
        raise CrabError("receiving cases must include a target and a positive control")
    worker = Path(__file__).with_name("case_worker.py").resolve()
    # Only this fixed trusted template is executable; agent-provided content is
    # JSON data, never Python text or a donor program/test path.
    source = (
        "import hashlib, json, runpy\nfrom pathlib import Path\n"
        "if hashlib.sha256(Path("
        + repr(str(worker))
        + ").read_bytes()).hexdigest() != "
        + repr(hashlib.sha256(worker.read_bytes()).hexdigest())
        + ":\n    raise RuntimeError('trusted receiving case runner changed')\n"
        "runpy.run_path("
        + repr(str(worker))
        + ", init_globals={'CASE_PLAN': json.loads("
        + repr(json.dumps({"target": target, "cases": cases, "helpers": helpers}))
        + ")}, run_name='__main__')\n"
    )
    return run_probe(
        directory,
        maw,
        source,
        author=plan.get("author", "invoked host"),
        expected=plan.get("expected", "Execute selected receiving behavior and controls"),
        controls=[c["label"] for c in cases if c["role"] != "target"],
        original_receiving_owned=True,
        import_receiving=True,
        required_receiving_calls=[target],
        timeout=plan.get("timeout", 30),
    )
