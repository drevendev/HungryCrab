"""Explicit metered agent host; domain evidence/effects remain in the installed CLI.

The qualification driver supplies pinned inputs. No model is started by importing
this module, and no scheduler, donor execution or publication is available.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from qualification_native import run_worker


def read(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def write(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def strict(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class Commands:
    """Persist command numbers before dispatch, including failed operations."""

    def __init__(
        self, root: Path, python: Path, cwd: Path, *, module_root: Path | None = None
    ) -> None:
        self.root, self.python, self.cwd = root, python, cwd
        self.root.mkdir(parents=True, exist_ok=True)
        self.env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        self.env.update(PYTHONIOENCODING="utf-8", CRAB_CACHE_DIR=str(root / "cache"))
        self.counter = root / "command-sequence.json"
        self.prefix = [str(python), "-B", "-m", "hungry_crab"]
        if module_root is not None:
            # Explicit sealed trusted tool source for an already installed
            # compatible receiving interpreter. No donor path or environment
            # override enters the child. The receiving probe remains isolated.
            self.prefix = [
                str(python),
                "-I",
                "-B",
                "-c",
                "import sys,runpy;sys.path.insert(0,"
                + repr(str(module_root.resolve()))
                + ");runpy.run_module('hungry_crab',run_name='__main__')",
            ]

    def call(self, arguments: list[object], payload: dict[str, Any] | None = None) -> str:
        sequence = int(read(self.counter)["last"]) + 1 if self.counter.exists() else 1
        write(self.counter, {"last": sequence})
        prefix = self.root / ("command-" + str(sequence))
        if payload is not None:
            path = prefix.with_suffix(".input.json")
            write(path, payload)
            arguments = [*arguments, "--input", path]
        write(
            prefix.with_suffix(".request.json"),
            {"command": self.prefix + list(map(str, arguments))},
        )
        with (
            prefix.with_suffix(".stdout").open("wb") as output,
            prefix.with_suffix(".stderr").open("wb") as errors,
        ):
            process = subprocess.run(
                [*self.prefix, *map(str, arguments)],
                cwd=self.cwd,
                env=self.env,
                stdout=output,
                stderr=errors,
                timeout=240,
            )
        if process.returncode:
            raise RuntimeError(
                prefix.with_suffix(".stderr").read_text(encoding="utf-8", errors="replace")[-1800:]
            )
        if prefix.with_suffix(".stdout").stat().st_size > 4 * 1024 * 1024:
            raise RuntimeError(
                "CLI result exceeds bounded host transport; original artifact retained"
            )
        return prefix.with_suffix(".stdout").read_text(encoding="utf-8", errors="replace")

    def json(
        self, arguments: list[object], payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return dict(json.loads(self.call(arguments, payload)))


class Meter:
    """Every semantic/probe-generation stage runs here; no supervisor callback."""

    def __init__(
        self,
        root: Path,
        *,
        cwd: Path,
        model: str,
        effort: str,
        commands: Commands | None = None,
        session: Path | None = None,
        token_limit: int = 30000,
        seconds: int = 2700,
    ) -> None:
        self.root, self.cwd = root, cwd
        self.model, self.effort = model, effort
        self.commands, self.session = commands, session
        self.limit, self.seconds = token_limit, seconds
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "meter.json"
        self.data = (
            read(self.path)
            if self.path.exists()
            else {"started": time.time(), "tokens": 0, "usage_complete": True, "stages": []}
        )
        write(self.path, self.data)

    @property
    def remaining(self) -> int:
        return self.limit - int(self.data["tokens"])

    def admission(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = {
            "prompt": prompt,
            "schema": schema,
            "model": self.model,
            "effort": self.effort,
            "transport": "host-validated-json/v1",
        }
        # Known-control receipts observed about 4.6k native-prefix tokens.
        # Reserve 6.5k for prefix/reply plus conservative request bytes/3.
        # This remains admission only; actual overshoots invalidate the trial.
        estimate = 6500 + (len(json.dumps(request, ensure_ascii=False).encode()) + 2) // 3
        left = self.seconds - (time.time() - self.data["started"])
        return {
            "estimate": estimate,
            "remaining": self.remaining,
            "remaining_seconds": left,
            "admissible": bool(
                self.data["usage_complete"] and self.remaining >= estimate and left > 0
            ),
        }

    def call(
        self, name: str, prompt: str, schema: dict[str, Any], *, attempt: str | None
    ) -> dict[str, Any] | None:
        directory = self.root / name
        request = {
            "prompt": prompt,
            "schema": schema,
            "model": self.model,
            "effort": self.effort,
            "transport": "host-validated-json/v1",
        }
        signature = identity(request)
        existing = next((r for r in self.data["stages"] if r["name"] == name), None)
        if existing and (existing["signature"] != signature or existing["attempt"] != attempt):
            raise RuntimeError("a started model operation cannot change its input or owner")
        if directory.exists():
            result_file = directory / "result.json"
            if not result_file.exists():
                self._settle(name, attempt, None)
                self.data["usage_complete"] = False
                write(self.path, self.data)
                raise RuntimeError("uncertain model operation cannot be replayed")
            result = read(result_file)
        else:
            # Admission is an explicit estimate, never the usage receipt. Include
            # the actual schema and a conservative fixed native-prefix/output
            # reserve; preserve actual overruns instead of raising trial caps.
            admission = self.admission(prompt, schema)
            estimate, left = admission["estimate"], admission["remaining_seconds"]
            if not admission["admissible"]:
                return None
            row = {
                "name": name,
                "signature": signature,
                "admission_estimate": estimate,
                "attempt": attempt,
                "state": "started",
            }
            self.data["stages"].append(row)
            write(self.path, self.data)
            write(self.root / (name + ".request.json"), request)
            result = run_worker(
                directory,
                prompt,
                [],
                lambda *_: {"refused": "no native tools"},
                cwd=self.cwd,
                model=self.model,
                effort=self.effort,
                output_schema=schema,
                constrained_output=False,
                token_limit=self.remaining,
                seconds=max(1, int(left)),
            )
        # An interrupted semantic stage can still have a complete actual usage
        # receipt. Preserve that cost even though its answer cannot be accepted.
        measured = bool(result["usage_complete"] and result.get("usage") is not None)
        complete = measured and result["status"] == "completed"
        tokens = result["usage"]["totalTokens"] if measured else None
        # Always settle before parsing semantics, even for malformed JSON,
        # interrupted work, overrun or a failed downstream CLI observation.
        self._settle(name, attempt, tokens)
        row = next(r for r in self.data["stages"] if r["name"] == name)
        if row["state"] != "settled":
            if tokens is None:
                self.data["usage_complete"] = False
            else:
                self.data["tokens"] += tokens
            row.update(state="settled", actual_tokens=tokens, native_seconds=result["seconds"])
            write(self.path, self.data)
        if not complete or not result["budget_valid"] or self.data["tokens"] > self.limit:
            return None
        from semantic_json import parse_answer

        try:
            return parse_answer(result["messages"][-1], schema)
        except (ValueError, IndexError) as error:
            row["output_validation_error"] = type(error).__name__ + ": " + str(error)[:160]
            self.data["last_stop_reason"] = "model-output-invalid"
            write(self.path, self.data)
            return None

    def _settle(self, name: str, attempt: str | None, tokens: int | None) -> None:
        if self.session is not None and self.commands is not None:
            if not attempt:
                raise RuntimeError("model work requires a known owned attempt")
            self.commands.json(
                ["discover", "usage", self.session],
                {
                    "operation": "native:" + name,
                    "attempt": attempt,
                    "author": "fully metered invoked host",
                    "tool": "native-codex/" + self.model,
                    "model_tokens": tokens,
                },
            )
