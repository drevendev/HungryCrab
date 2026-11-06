"""Actual two-arm portable-digest CLI round trips on original owned fixtures."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from qualification_source_view import git, reader_preflight, source_view
from research_host import Commands, read, write

from hungry_crab.cache import Slug, prey_paths
from hungry_crab.discovery_session import live_context


def owned_repository(root: Path, files: dict[str, str], license_text: str) -> Path:
    """Create trusted control data, never install or execute its contents."""
    root.mkdir(parents=True, exist_ok=False)
    for name, content in {"LICENSE": license_text, **files}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
    git(root, "init", "--quiet", "--template=")
    git(root, "config", "core.hooksPath", os.devnull)
    git(root, "config", "user.name", "Owned qualification fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "test: original owned control",
    )
    return root


def owned_donor(
    root: Path, files: dict[str, str], license_text: str, arms: dict[str, tuple[Path, Path]]
) -> dict[str, Any]:
    """Build CI-shaped cached facts with real CLI miners, no provider requests."""
    source = owned_repository(root / "source", files, license_text)
    slug = Slug("owned", root.name)
    git(source, "remote", "add", "origin", slug.url + ".git")
    pin = git(source, "rev-parse", "HEAD").decode().strip()
    bare = root / "objects"
    shutil.copytree(source / ".git", bare)
    git(bare, "config", "core.bare", "true")
    cache = root / "acquisition-cache"
    paths = prey_paths(slug, cache)
    view_receipt = source_view(bare, pin, paths.repo)
    paths.api.mkdir()
    write(
        paths.api / "repo.json",
        {"full_name": str(slug), "default_branch": "master", "license": {"spdx_id": "MIT"}},
    )
    write(root / "source-view.json", view_receipt)
    digests = {}
    for arm, (python, cwd) in arms.items():
        commands = Commands(root / (arm + "-acquisition"), python, cwd)
        digest = root / (arm + "-digest")
        commands.call(
            ["--cache-dir", cache, "digest", slug, "--out", digest, "--fail-on-miner-error"]
        )
        digests[arm] = str(digest)
    catalog = git(source, "ls-tree", "-r", "--name-only", pin, maximum=65536).decode().splitlines()
    return {
        "id": root.name,
        "repository": str(slug),
        "sha": pin,
        "view": str(paths.repo),
        "arm_digests": digests,
        "catalog": catalog,
        "description": "Original owned engineering fixture; no provider identity asserted",
        "control_provenance": "owned-fixtures",
    }


def roundtrip(root: Path, arms: dict[str, tuple[Path, Path]], license_text: str) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=False)
    maw = owned_repository(
        root / "receiver",
        {"app.py": "def consume(value):\n    return value\n", ".crab.yml": "ledger: none\n"},
        license_text,
    )
    donor = owned_donor(
        root / "qualification-fixture",
        {
            "vendor/unindexed.yml": "owned: literal\ncontrol: unchanged\n",
            "mechanism.py": "raise AssertionError('DONOR MUST NEVER EXECUTE')\n",
        },
        license_text,
        arms,
    )
    context = live_context(maw)
    results = {}
    for arm, (python, cwd) in arms.items():
        commands = Commands(root / arm, python, cwd)
        actual = reader_preflight(python, Path(donor["view"]), donor["sha"], "vendor/unindexed.yml")
        output = commands.call(
            [
                "compare",
                donor["repository"],
                "--maw",
                maw,
                "--prey-digest",
                donor["arm_digests"][arm],
                "--prey-objects",
                donor["view"],
                "--no-ledger",
                "--no-issues",
            ]
        )
        meal = Path(re.findall(r"^meal written to (.+)$", output, re.M)[-1].strip())
        plan = {
            "scope": "owned portable evidence interoperability",
            "goals": "actual CLI fallback",
            "flows": "read original immutable data",
            "constraints": "No donor execution or inference",
            "facets": ["behavior"],
            "author": "owner-invoked control",
            "attempt": arm,
            "receiving_context": context,
        }
        write(root / (arm + "-plan.json"), plan)
        child = Path(
            commands.json(
                [
                    "explore",
                    "init",
                    "--meal-dir",
                    meal,
                    "--maw",
                    maw,
                    "--plan",
                    root / (arm + "-plan.json"),
                ]
            )["directory"]
        )
        snapshot = read(child / "snapshot.json")
        assert snapshot["file_evidence"]["prey"].get("vendor/unindexed.yml") is None
        assert Path(snapshot["menu"]["prey"]["objects"]) == Path(donor["view"])
        event = commands.json(
            ["explore", "record", child],
            {
                "kind": "decision",
                "hypothesis": "Owned interoperability witness only",
                "disposition": "not-useful",
                "reviewed": True,
                "reason": "Actual pinned source/receiving reads; no discovery or utility claim",
                "source_evidence": [
                    {"sha": donor["sha"], "path": "vendor/unindexed.yml", "start": 1, "end": 2}
                ],
                "maw_evidence": [{"sha": context["sha"], "path": "app.py", "start": 1, "end": 2}],
                "author": "owned integration control",
                "tool": "actual arm CLI",
                "attempt": arm,
            },
        )
        results[arm] = {
            "recorded": True,
            "source_fallback_exercised": True,
            "reader": actual,
            "event": event,
            "interpreter": str(python),
            "source_root": str(cwd),
            "exploration": str(child),
            "route": "portable-digest-and-objects",
        }
    value = {
        "arms": results,
        "receiving_unchanged": live_context(maw) == context,
        "native_tokens": 0,
        "donor_executed": False,
        "scope": "actual portable CLI interoperability; not full native readiness",
    }
    write(root / "verification.json", value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-python", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path, required=True)
    args = parser.parse_args()
    import sys

    repo = Path(__file__).resolve().parents[1]
    value = roundtrip(
        args.root.resolve(),
        {
            "baseline": (args.baseline_python.resolve(), args.baseline_source.resolve()),
            "candidate": (Path(sys.executable), repo),
        },
        (repo / "LICENSE").read_text(),
    )
    print(
        json.dumps(
            {
                a: {"recorded": v["recorded"], "reader": v["reader"]}
                for a, v in value["arms"].items()
            }
        )
    )


if __name__ == "__main__":
    main()
