"""Run original JSON cases through a fixed trusted receiving-only probe template."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from .compare.candidates import _evidence_path
from .errors import CrabError
from .receiving_probe import run_probe


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
        "import json, runpy\nrunpy.run_path("
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
