"""Bounded, inert relations between already-parsed GitHub Actions workflows.

This is an additive sidecar: ci.json is unchanged.  All entries originate from
an explicitly supplied inventory; this miner never fetches or runs workflows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .base import MineContext, MinerResult

SCHEMA = "hungry-crab.ci-relations/1"
MAX_WORKFLOWS = 64
MAX_RELATIONS = 256
_LOCAL_WORKFLOW = re.compile(r"\A\./\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")


def _target_status(
    uses: str, inventory: Mapping[str, Mapping[str, Any]]
) -> tuple[str | None, str]:
    """Resolve only an exact local YAML target with a workflow_call trigger."""
    if "${{" in uses or "}}" in uses:
        return None, "expression-unresolved"
    if uses.startswith("./"):
        if _LOCAL_WORKFLOW.fullmatch(uses) is None:
            return None, "invalid-local-path"
        target = uses[2:]
        workflow = inventory.get(target)
        if workflow is None:
            return target, "not-in-supplied-inventory"
        triggers = workflow.get("triggers")
        if not isinstance(triggers, list) or "workflow_call" not in triggers:
            return target, "target-not-workflow-call"
        return target, "resolved-workflow-call"
    if uses.startswith(("../", "/", "\\")):
        return None, "invalid-local-path"
    return None, "remote-unresolved"


def build_ci_relations(
    workflows: Sequence[Mapping[str, Any]],
    *,
    workflow_limit: int = MAX_WORKFLOWS,
    relation_limit: int = MAX_RELATIONS,
) -> dict[str, Any]:
    """Build a bounded, deterministic callsite ledger from parsed CI rows.

    No filesystem or network lookups occur. An unindexed target is *unknown*,
    not a claim that the file is absent. Limits smaller than defaults support
    explicit truncation tests and caller budgets; no caller can raise the caps.
    """
    if not 1 <= workflow_limit <= MAX_WORKFLOWS:
        raise ValueError("workflow_limit must be between 1 and 64")
    if not 1 <= relation_limit <= MAX_RELATIONS:
        raise ValueError("relation_limit must be between 1 and 256")

    ordered = sorted(workflows, key=lambda wf: str(wf.get("path", "")))
    indexed = ordered[:workflow_limit]
    inventory = {str(wf.get("path", "")): wf for wf in indexed}
    records: list[dict[str, str | None]] = []
    truncated_relations = False
    for workflow in indexed:
        jobs = workflow.get("jobs")
        if not isinstance(jobs, list):
            continue
        for job in sorted(
            (item for item in jobs if isinstance(item, dict)),
            key=lambda item: str(item.get("id", "")),
        ):
            uses = job.get("uses")
            if not isinstance(uses, str):
                continue
            if len(records) >= relation_limit:
                truncated_relations = True
                break
            target, status = _target_status(uses, inventory)
            records.append(
                {
                    "caller_path": str(workflow.get("path", "")),
                    "caller_job": str(job.get("id", "")),
                    "raw_uses": uses,
                    "target_path": target,
                    "status": status,
                }
            )
        if truncated_relations:
            break

    workflows_truncated = len(ordered) > workflow_limit
    return {
        "schema": SCHEMA,
        "inventory_workflows": len(ordered),
        "indexed_workflows": len(indexed),
        "workflow_limit": workflow_limit,
        "relation_limit": relation_limit,
        "truncated": workflows_truncated or truncated_relations,
        "truncation": {
            "workflows": workflows_truncated,
            "relations": truncated_relations,
        },
        "relations": records,
    }


class CiRelationsMiner:
    """Separate JSON sidecar, dependent only on the existing CI parser output."""

    name = "ci_relations"
    requires = ("ci",)
    json_file = "ci_relations.json"
    md_file: None = None

    def run(self, ctx: MineContext) -> MinerResult:
        rows = ctx.data("ci").get("workflows", [])
        workflows: list[Mapping[str, Any]] = (
            [wf for wf in rows if isinstance(wf, dict)] if isinstance(rows, list) else []
        )
        return MinerResult(self.name, build_ci_relations(workflows))
