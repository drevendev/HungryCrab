"""License miner: SPDX, confidence, per-file exceptions and the verdict against the maw."""

from __future__ import annotations

from typing import Any

from ..licensing import classify, decide, modes_by_maw_class
from ..licensing.detect import detect_in_repo, is_license_file_name
from .base import MineContext, MinerResult

_ROOT_MANIFESTS = {
    "package.json",
    "pyproject.toml",
    "cargo.toml",
    "setup.cfg",
    "setup.py",
    "composer.json",
}


class LicenseMiner:
    name = "license"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "license.json"
    md_file = None

    def run(self, ctx: MineContext) -> MinerResult:
        files = ctx.files()
        code_files = [f.path for f in files if f.is_code and f.counted]
        manifests = [
            f.path
            for f in files
            if (f.depth == 0 and f.name.lower() in _ROOT_MANIFESTS)
            or (f.ext in {".csproj", ".fsproj"} and f.depth <= 2)
        ]
        nested = [f.path for f in files if f.depth >= 1 and is_license_file_name(f.name)][:40]
        api_spdx: str | None = None
        repo_meta = ctx.api.get("repo")
        if isinstance(repo_meta, dict) and isinstance(repo_meta.get("license"), dict):
            value = repo_meta["license"].get("spdx_id")
            api_spdx = value if isinstance(value, str) else None

        findings = detect_in_repo(
            ctx.root,
            code_files,
            root_entries=ctx.root_entries(),
            manifests=manifests,
            nested_license_files=nested,
            api_spdx=api_spdx,
            max_header_files=800 if ctx.deep else 400,
        )
        cls = classify(findings.spdx)
        data: dict[str, Any] = findings.to_dict()
        data.update(
            {
                "class": cls.value,
                "modes_by_maw_class": modes_by_maw_class(findings.spdx),
                "maw_license": ctx.maw_license,
                "verdict": decide(findings.spdx, ctx.maw_license).to_dict()
                if ctx.maw_license
                else None,
                "api_spdx": api_spdx,
            }
        )
        warnings: list[str] = []
        if findings.human_review:
            reason = "; ".join(findings.conflicts) or findings.notes[-1] if findings.notes else ""
            warnings.append(f"license needs human review ({reason or 'unclear license'})")
        return MinerResult(self.name, data, warnings=warnings)
