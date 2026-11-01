"""Read the installed App Server catalog without starting inference or copying credentials."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

from qualification_native import native_command


def catalog(output: Path, cwd: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)
    with (output / "stderr.log").open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(
            native_command(30000),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            env=env,
        )
        assert process.stdin is not None and process.stdout is not None
        events: queue.Queue[dict[str, Any]] = queue.Queue()

        def read() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                events.put(json.loads(line))
            events.put({"method": "host/eof"})

        threading.Thread(target=read, daemon=True).start()

        def call(identity: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
            assert process.stdin is not None
            process.stdin.write(
                json.dumps({"id": identity, "method": method, "params": params}) + "\n"
            )
            process.stdin.flush()
            while True:
                event = events.get(timeout=30)
                if event.get("method") == "host/eof":
                    raise RuntimeError("catalog host exited")
                if event.get("id") == identity:
                    if "error" in event:
                        raise RuntimeError("catalog host refused the request")
                    return dict(event["result"])

        try:
            call(
                1,
                "initialize",
                {"clientInfo": {"name": "crab_qualification_catalog", "version": "1"}},
            )
            process.stdin.write('{"method":"initialized"}\n')
            process.stdin.flush()
            data = call(2, "model/list", {"includeHidden": False, "limit": 100})
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
    (output / "catalog.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--cwd", type=Path, required=True)
    args = parser.parse_args()
    result = catalog(args.output, args.cwd)
    print(
        json.dumps(
            [
                {
                    "id": row.get("id"),
                    "model": row.get("model"),
                    "efforts": row.get("supportedReasoningEfforts"),
                    "default": row.get("isDefault"),
                }
                for row in result["data"]
            ]
        )
    )
