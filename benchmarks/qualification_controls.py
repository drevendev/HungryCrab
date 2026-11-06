"""Prepare original engineering fixtures for the actual full qualification executor.

No model is invoked here. Fixture truth is withheld from investigators/reviewers:
they receive actual receiving requirements, source and observations, not verdicts.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qualification_cycle import controller_need
from qualification_interop import owned_donor, owned_repository
from qualification_readiness import host_fingerprint
from qualification_source_view import git
from research_host import Commands, write

from hungry_crab.analysis_context import installed_source_identity
from hungry_crab.discovery_session import live_context

RUNTIME = """from dataclasses import dataclass

@dataclass(frozen=True)
class Units:
    value: int

def validate_units(units: Units) -> int:
    if units.value < 0:
        raise ValueError('negative units')
    return units.value
"""

DESIGN = """def describe_units(value):
    return {'raw': value}
"""


def prepare(root: Path, baseline_python: Path, baseline_source: Path) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[1]
    root.mkdir(parents=True, exist_ok=False)
    license_text = (repo / "LICENSE").read_text(encoding="utf-8")
    arms = {
        "baseline": (baseline_python, baseline_source),
        "candidate": (Path(sys.executable), repo),
    }
    # Short regular functions plus enum/header/test distractors. None are run.
    sources = [
        (
            "bounded-units",
            "Nonnegative numeric validation and raw-value descriptions",
            (
                "def validate_units(value):\n    if value < 0:\n        raise V"
                "alueError('negative units')\n    return value\n\ndef describe_u"
                "nits(value):\n    return {'raw': value}\n"
            ),
        ),
        (
            "typed-units",
            "Integer-domain validation and explicit diagnostic classification",
            (
                "def validate_units(value):\n    if type(value) is not int:\n  "
                "      raise ValueError('integer required')\n    if value < 0:"
                "\n        raise ValueError('negative units')\n    return value"
                "\n\ndef describe_units(value):\n    if type(value) is not int:\n"
                "        return {'raw': value, 'status': 'invalid-type'}\n    "
                "return {'raw': value, 'status': 'negative' if value < 0 else"
                " 'valid'}\n"
            ),
        ),
    ]
    pool = []
    for name, description, body in sources:
        donor = owned_donor(
            root / name,
            {
                "validation.py": "# Validation vocabulary is not an implementation proof.\n" + body,
                "constants.py": "from enum import Enum\nclass UnitKind(Enum):\n    INTEGER = 'i"
                "nteger'\n",
                "tests/test_validation.py": "raise AssertionError('DONOR EXECUTION FORBIDDEN')\n",
            },
            license_text,
            arms,
        )
        donor["description"] = description
        pool.append(donor)
    definitions: dict[str, Any] = {}
    for mode, source in (("runtime", RUNTIME), ("design", DESIGN)):
        maw = owned_repository(
            root / (mode + "-receiver"),
            {"units.py": source, ".crab.yml": "ledger: none\n"},
            license_text,
        )
        context = live_context(maw)
        refs = [
            {"sha": context["sha"], "path": "units.py", "start": 1, "end": len(source.splitlines())}
        ]
        requirements = (
            [
                (
                    "Nonnegative units validation",
                    "Reject negative Units.value with ValueError; accept Units(2) unchanged.",
                    "validate_units",
                ),
                (
                    "Strict integer units validation",
                    (
                        "Reject Units(True) as a non-integer domain input with ValueE"
                        "rror; accept Units(2) unchanged."
                    ),
                    "validate_units",
                ),
            ]
            if mode == "runtime"
            else [
                (
                    "Raw numeric input preservation",
                    (
                        "Preserve the original raw numeric or boolean input in the de"
                        "scription without silently coercing it."
                    ),
                    "describe_units",
                ),
                (
                    "Explicit bounded input classification",
                    (
                        "Design an additive description status that distinguishes val"
                        "id nonnegative integers, negative integers and non-integer i"
                        "nputs while preserving the raw value."
                    ),
                    "describe_units",
                ),
            ]
        )
        needs = []
        for index, (flow, expected, target) in enumerate(requirements, 1):
            need = {
                "id": "n" + str(index),
                "flow": flow,
                "expected": expected,
                "observed": "Actual selected implementation is supplied as source; judge "
                "it directly.",
                "impact": "Preserve existing raw values and supported integer behavior",
                "constraints": "Original owned control. No donor execution/copying/publicati"
                "on. No aesthetic claim.",
                "verification_mode": mode,
                "source_material": "implementation",
                "maw_evidence": refs,
                "queries": [flow],
                "terms": [target],
                "target": {"path": "units.py", "qualified_name": target}
                if mode == "runtime"
                else {},
                "backlog": {
                    "status": "checked",
                    "pending": [],
                    "as_of": datetime.now(UTC).isoformat(),
                    "default_ref": "HEAD",
                    "default_sha": context["sha"],
                    "method": "Original owned fixture; only selected/default source exists",
                },
                "novelty": {
                    "prefixes": ["units.py"],
                    "pending_collection": {
                        "complete": True,
                        "method": "owned original fixture; no pending revisions",
                    },
                    "implementation_scope_complete": True,
                    "bindings": [
                        {
                            "role": role,
                            "sha": context["sha"],
                            "files": [
                                {
                                    "path": "units.py",
                                    "content_sha256": hashlib.sha256(source.encode()).hexdigest(),
                                }
                            ],
                            "scope_complete": True,
                        }
                        for role in ("receiving-selected", "receiving-default")
                    ],
                    "backlog_summary": [],
                    "global_historical_absence_proven": False,
                },
            }
            if mode == "runtime":
                need["runtime_inputs"] = {"probe_format": "original-python", "imports": ["units"]}
            needs.append(need)
        limits = {
            "model_tokens": 30000,
            "seconds": 2700,
            "requests": 16,
            "donors": 6,
            "hypotheses": 12,
            "replacement_rounds": 2,
        }
        definitions[mode] = {
            "context": context,
            "receiving_python": sys.executable,
            "needs": needs,
            "plan": {
                "goal": "Complete actual owned source investigations with explicit stops",
                "receiving_context": context,
                "needs": [controller_need(n) for n in needs],
                "limits": limits,
            },
        }
    # Distinct mode profiles have one fixed shared host configuration.
    value = {
        "purpose": "engineering-control",
        "control_data": "owned-fixtures",
        "baseline": {"commit": "915779769747908a14b1a0f7fdecdd79d16dce43", "version": "0.6.8"},
        "candidate": {
            "source_identity": installed_source_identity()["source_sha256"],
            "final_tested_head": git(repo, "rev-parse", "HEAD").decode().strip(),
        },
        "execution_host_fingerprint": host_fingerprint(repo),
        "host": {
            "model": "gpt-6.1-sol",
            "effort": "medium",
            "transport": "native-constrained-json/v2",
            "minimum_assessment_reserve": 6500,
            "initial_donor_bytes": 4096,
            "source_continuation_rounds": 2,
            "candidate_metadata_seeds": True,
            "candidate_read_planner": True,
            "seed_focus": "implementation",
            "defer_oversized_source": True,
        },
        "limits": limits,
        "maws": definitions,
        "pool": [{**row, "batch": mode} for mode in definitions for row in pool],
        "arm_python": {k: str(v[0]) for k, v in arms.items()},
        "tool_cwd": {k: str(v[1]) for k, v in arms.items()},
        "matching_skills": {k: str(v[1] / "skills/eat/SKILL.md") for k, v in arms.items()},
        "trial_root": str(root / "trials"),
        "native_cwd": str(root / "empty-worker"),
    }
    (root / "empty-worker").mkdir()
    # Refuse invalid controller plans before ANY arm can spend native tokens.
    # This is the actual installed CLI, not a duplicated schema validator.
    for mode, definition in definitions.items():
        plan_path = root / (mode + "-preflight-plan.json")
        write(plan_path, definition["plan"])
        Commands(root / (mode + "-preflight-cli"), Path(sys.executable), repo).json(
            [
                "discover",
                "init",
                root / (mode + "-preflight-session"),
                "--maw",
                definition["context"]["root"],
                "--plan",
                plan_path,
            ]
        )
    write(root / "control-profile.json", value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-python", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root.resolve(), args.baseline_python.resolve(), args.baseline_source.resolve())
    print("Prepared owned full-executor controls; no inference or success claimed")


if __name__ == "__main__":
    main()
