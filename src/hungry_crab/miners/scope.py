"""Project-vs-auxiliary evidence scope for downstream miners.

Inventory keeps auxiliary tooling visible because it is real prey evidence.  Downstream miners
must still distinguish that evidence from the repository's own stack: a helper under ``tools/``
or ``script/`` should not make an otherwise Ruby project read as Go or Rust.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .base import FileInfo, MineContext, MinerResult
from .deps import DepsMiner
from .inventory import ROLE_BY_NAME
from .ruby import enrich_ruby_dependencies
from .testing import TestingMiner

_AUXILIARY_LAYOUT_ROLES = frozenset({"scripts"})


def _layout_role(info: FileInfo) -> str | None:
    head, separator, _ = info.path.partition("/")
    if not separator:
        return None
    return ROLE_BY_NAME.get(head)


def _is_stack_declaration(info: FileInfo) -> bool:
    """Whether one inventory row can declare a project dependency ecosystem."""

    return info.manifest_kind is not None or info.lockfile or info.ext == ".gemspec"


def _has_project_declaration(files: list[FileInfo]) -> bool:
    """Whether the repository declares a stack outside an auxiliary tooling tree.

    The escape hatch matters for repositories whose actual product lives under a ``tools``-like
    directory.  In that shape there is no stronger declaration elsewhere, so the manifest stays
    project evidence instead of being hidden by a path heuristic.
    """

    return any(
        not info.vendored
        and not info.generated
        and _is_stack_declaration(info)
        and _layout_role(info) not in _AUXILIARY_LAYOUT_ROLES
        for info in files
    )


def _is_auxiliary(info: FileInfo, *, has_project_declaration: bool) -> bool:
    return (
        has_project_declaration
        and not info.vendored
        and not info.generated
        and _layout_role(info) in _AUXILIARY_LAYOUT_ROLES
    )


def _project_context(ctx: MineContext) -> tuple[MineContext, set[str]]:
    """Return a context whose inventory excludes auxiliary tooling evidence."""

    files = ctx.files()
    has_project_declaration = _has_project_declaration(files)
    auxiliary_paths = {
        info.path
        for info in files
        if _is_auxiliary(info, has_project_declaration=has_project_declaration)
    }
    if not auxiliary_paths:
        return ctx, set()

    inventory = ctx.results["inventory"]
    scoped_inventory = replace(
        inventory,
        extra={
            **inventory.extra,
            "files": [info for info in files if info.path not in auxiliary_paths],
        },
    )
    return replace(ctx, results={**ctx.results, "inventory": scoped_inventory}), auxiliary_paths


def _tag_rows(rows: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    return [{**row, "role": role} for row in rows]


class ProjectDepsMiner(DepsMiner):
    """Keep auxiliary dependency evidence visible without promoting it to project policy."""

    def run(self, ctx: MineContext) -> MinerResult:
        project_ctx, auxiliary_paths = _project_context(ctx)
        if not auxiliary_paths:
            result = enrich_ruby_dependencies(super().run(ctx), ctx)
            result.data["manifests"] = _tag_rows(result.data["manifests"], "project")
            result.data["packages"] = _tag_rows(result.data["packages"], "project")
            result.data["lockfiles"] = _tag_rows(result.data["lockfiles"], "project")
            result.data["auxiliary_manifests"] = []
            result.data["auxiliary_packages"] = []
            result.data["auxiliary_lockfiles"] = []
            return result

        full = enrich_ruby_dependencies(super().run(ctx), ctx)
        project = enrich_ruby_dependencies(super().run(project_ctx), project_ctx)
        project_manifest_paths = {row["path"] for row in project.data["manifests"]}
        project_lock_paths = {row["path"] for row in project.data["lockfiles"]}

        project.data["manifests"] = _tag_rows(project.data["manifests"], "project")
        project.data["packages"] = _tag_rows(project.data["packages"], "project")
        project.data["lockfiles"] = _tag_rows(project.data["lockfiles"], "project")
        project.data["auxiliary_manifests"] = _tag_rows(
            [row for row in full.data["manifests"] if row["path"] not in project_manifest_paths],
            "auxiliary",
        )
        project.data["auxiliary_packages"] = _tag_rows(
            [row for row in full.data["packages"] if row["manifest"] in auxiliary_paths],
            "auxiliary",
        )
        project.data["auxiliary_lockfiles"] = _tag_rows(
            [row for row in full.data["lockfiles"] if row["path"] not in project_lock_paths],
            "auxiliary",
        )
        project.extra["auxiliary_packages"] = [
            package for package in full.extra["packages"] if package.manifest in auxiliary_paths
        ]
        project.extra["auxiliary_manifests"] = project.data["auxiliary_manifests"]
        project.warnings = list(dict.fromkeys([*project.warnings, *full.warnings]))
        return project


class ProjectTestingMiner(TestingMiner):
    """Infer repository test shape from project evidence, not nested tooling helpers."""

    def run(self, ctx: MineContext) -> MinerResult:
        project_ctx, _ = _project_context(ctx)
        return super().run(project_ctx)
