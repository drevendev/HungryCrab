"""Fixed original receiving-case runner: JSON fixtures/predicates, never donor code."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import importlib
import io
import json
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import FrameType
from typing import Any, Literal


class CallWitness:
    """Observe only this case's main invocation, chaining the outer trusted trace."""

    def __init__(self, path: Path, qualified_name: str) -> None:
        self.path = str(path.resolve())
        self.qualified_name = qualified_name
        self.called = False
        self.previous = sys.getprofile()
        self.filenames: dict[str, str] = {}

    def exception_origin(self, error: Exception, root: Path) -> str:
        """Do not attribute a serializer/factory failure to the receiving code."""
        trace = error.__traceback__
        runner = Path(__file__).resolve()
        for _ in range(128):
            if trace is None:
                return "verification-harness"
            filename = Path(trace.tb_frame.f_code.co_filename)
            if filename.is_absolute():
                path = filename.resolve()
                if path != runner and path.is_relative_to(root):
                    return "receiving"
            trace = trace.tb_next
        return "unknown-traceback-bound"

    def __call__(
        self,
        frame: FrameType,
        event: Literal["call", "return", "c_call", "c_return", "c_exception"],
        arg: Any,
    ) -> None:
        if event == "call":
            name = frame.f_code.co_filename
            if name not in self.filenames:
                self.filenames[name] = str(Path(name).resolve())
            if (
                self.filenames[name] == self.path
                and frame.f_code.co_qualname == self.qualified_name
            ):
                self.called = True
        if self.previous is not None:
            self.previous(frame, event, arg)


class BoundedStream(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            raise ValueError("original control: unbounded read")
        return super().read(size)


def encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return encode(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [encode(v) for v in value]
    if isinstance(value, Iterator):
        import itertools

        items = list(itertools.islice(value, 129))
        return {"items": [encode(v) for v in items[:128]], "truncated": len(items) > 128}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return {"type": type(value).__name__}


def execute(plan: dict[str, Any], root: Path) -> None:
    target = plan["target"]
    path = Path(target["path"])
    parts = list(path.with_suffix("").parts)
    if parts[0] == "src":
        parts.pop(0)
    if parts[-1] == "__init__":
        parts.pop()
    sys.path[:0] = [str(root), str(root / "src"), str(root / path.parent)]
    module = importlib.import_module(".".join(parts))
    function: Any = module
    for part in target["qualified_name"].split("."):
        function = getattr(function, part)
    results = []
    for case in plan["cases"]:
        with tempfile.TemporaryDirectory(prefix="crab-original-case-") as temporary:
            fixture = Path(temporary)
            for name, text in case.get("files", {}).items():
                destination = fixture / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding="utf-8", newline="\n")

            def decode(value: Any, fixture: Path = fixture) -> Any:
                if isinstance(value, dict):
                    if set(value) == {"$fixture"}:
                        return fixture / value["$fixture"]
                    if set(value) == {"$bytes"}:
                        return base64.b64decode(value["$bytes"], validate=True)
                    if set(value) == {"$base64"}:
                        return base64.b64encode(value["$base64"].encode()).decode()
                    if set(value) == {"$git_blob_sha1"}:
                        data = value["$git_blob_sha1"].encode()
                        return hashlib.sha1(
                            b"blob " + str(len(data)).encode() + b"\0" + data,
                            usedforsecurity=False,
                        ).hexdigest()
                    if set(value) == {"$stream"}:
                        return BoundedStream(value["$stream"].encode())
                    if set(value) == {"$io"}:
                        return io.BytesIO(value["$io"].encode())
                    if set(value) == {"$helper"}:
                        helper: Any = module
                        for part in plan["helpers"][value["$helper"]].split("."):
                            helper = getattr(helper, part)
                        return helper()
                    return {k: decode(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [decode(v) for v in value]
                return value

            witness = CallWitness(root / path, target["qualified_name"])

            try:
                # Helpers/argument factories are outside the main-call witness.
                # A control/helper call must not mask a target's arity failure.
                args, kwargs = decode(case.get("args", [])), decode(case.get("kwargs", {}))
                sys.setprofile(witness)
                try:
                    value = function(*args, **kwargs)
                    actual = {"value": encode(value)}
                finally:
                    sys.setprofile(witness.previous)
            except Exception as exc:
                actual = {
                    "exception": {"type": type(exc).__name__, "message": str(exc)[:1000]},
                    "exception_origin": witness.exception_origin(exc, root.resolve()),
                }
            checks = []
            for check in case["checks"]:
                observed: Any = actual
                try:
                    for part in check["pointer"].split("/")[1:]:
                        key = part.replace("~1", "/").replace("~0", "~")
                        observed = (
                            observed[int(key)] if isinstance(observed, list) else observed[key]
                        )
                    if "equals" in check:
                        expected = check["equals"]
                        passed = observed == expected
                    else:
                        key = "contains" if "contains" in check else "not_contains"
                        expected = check[key]
                        passed = (
                            expected in observed if key == "contains" else expected not in observed
                        )
                    error = None
                except (KeyError, IndexError, TypeError, ValueError):
                    observed, passed = None, False
                    expected = check.get("equals", check.get("contains", check.get("not_contains")))
                    error = "missing-pointer-or-incompatible-predicate"
                checks.append(
                    {
                        "pointer": check["pointer"],
                        "expected": expected,
                        "observed": observed,
                        "passed": passed,
                        "check_error": error,
                    }
                )
            results.append(
                {
                    "label": case["label"],
                    "role": case["role"],
                    "actual": actual,
                    "target_called": witness.called,
                    "checks": checks,
                    "passed": all(c["passed"] for c in checks),
                }
            )
    print(
        json.dumps(
            {
                "schema": "hungry-crab.receiving-cases-result/1",
                "cases": results,
                "human_acceptance": None,
                "donor_executed": False,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    execute(globals()["CASE_PLAN"], Path.cwd())
