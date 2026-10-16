"""Trusted action adapter. Inputs reach argparse as arguments, never as shell code."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import uuid
from pathlib import Path

from hungry_crab.cli import main


def run() -> int:
    temp = Path(os.environ["RUNNER_TEMP"])
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    output = os.environ.get("CRAB_OUT") or str(temp / f"crab-feeder-{uuid.uuid4().hex}")
    cache = os.environ.get("CRAB_CACHE") or str(temp / "hungry-crab")
    maw = Path(os.environ.get("CRAB_MAW", "."))
    if not maw.is_absolute():
        maw = workspace / maw
    prey = os.environ["CRAB_PREY"]
    # uv runs in the trusted action checkout; local prey paths belong to the caller workspace.
    local_prey = workspace / Path(prey).expanduser()
    if local_prey.is_dir():
        prey = str(local_prey.resolve())
    args = [
        "--cache-dir",
        cache,
        "eat",
        prey,
        "--deterministic",
        "--maw",
        str(maw),
        "--out",
        output,
        "--json",
    ]
    for name, flag in [
        ("SINCE", "--since"),
        ("ISSUES", "--issues"),
        ("TOP", "--top"),
        ("MAX_REPO_KB", "--max-repo-kb"),
        ("DEPTH", "--depth"),
    ]:
        args += [flag, os.environ[f"CRAB_{name}"]]
    for name in ("SHALLOW", "WIKI", "ALLOW_LOSS"):
        value = os.environ[f"CRAB_{name}"].lower()
        if value not in {"true", "false"}:
            raise ValueError(f"{name.lower()} must be true or false")
        if name == "SHALLOW" and value == "false":
            args.append("--no-shallow")
        if name == "WIKI" and value == "false":
            args.append("--no-wiki")
        if name == "ALLOW_LOSS" and value == "true":
            args.append("--allow-loss")
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(args)
    if code:
        print(stdout.getvalue(), end="")
        return code
    result = json.loads(stdout.getvalue())
    bundle = Path(result["out_dir"])
    outputs = {
        "bundle-path": str(bundle),
        "menu-path": str(bundle / "menu.json"),
        "candidate-count": str(result["counts"]["total"]),
    }
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            if "\n" in value or "\r" in value:
                raise ValueError("action output paths must not contain newlines")
            handle.write(f"{key}={value}\n")
    summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
    with summary.open("a", encoding="utf-8") as handle:
        handle.write(
            f"## Hungry Crab Feeder\n\n{outputs['candidate-count']} candidates. "
            "Download the meal artifact and open `menu.md`.\n\n"
            "No model, issue creation or ledger writes. The artifact contains "
            "untrusted evidence derived from the prey.\n"
        )
    print(f"Feeder: {outputs['candidate-count']} candidates, bundle at {bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
