"""Dependencies miner: normalized packages, lock-file and pinning policy per ecosystem."""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any

from ..typeutil import as_dict
from .base import FileInfo, MineContext, MinerResult
from .inventory import LOCKFILES

_NPM_EXACT_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")
_PY_REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*?)\s*$")
_DOTNET_EXACT_RE = re.compile(r"^\d+(?:\.\d+){1,3}(?:-[\w.]+)?$")
_GO_REQUIRE_RE = re.compile(r"^\s*([\w./~-]+)\s+(v[\w.+-]+)(\s*//\s*indirect)?", re.MULTILINE)
_GO_KEYWORDS = frozenset({"module", "go", "require", "toolchain", "replace", "exclude", "retract"})

MAX_MANIFESTS = 40
MAX_PACKAGES = 800


@dataclass
class Package:
    name: str
    spec: str
    kind: str
    ecosystem: str
    manifest: str
    pinned: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def npm_pinned(spec: str) -> bool | None:
    text = spec.strip()
    if not text or text in ("*", "latest", "next"):
        return False
    prefixes = ("npm:", "workspace:", "file:", "link:", "git", "http", "github:", "catalog:")
    if text.startswith(prefixes) or "/" in text:
        return None
    return bool(_NPM_EXACT_RE.match(text.lstrip("=v")))


def split_python_requirement(req: str) -> tuple[str, str] | None:
    body = req.split(";", 1)[0].split("#", 1)[0].strip()
    if not body or body.startswith(("-", "git+", "http", "file:", ".")):
        return None
    match = _PY_REQ_RE.match(body)
    if not match:
        return None
    return match.group(1).lower(), match.group(3)


def python_pinned(spec: str) -> bool:
    return "==" in spec and not spec.strip().startswith(("~=", ">=", "<", ">", "!="))


def parse_package_json(text: str, manifest: str) -> tuple[list[Package], dict[str, Any]]:
    data = json.loads(text)
    if not isinstance(data, dict):
        return [], {}
    packages: list[Package] = []
    for field_name, kind in (
        ("dependencies", "runtime"),
        ("devDependencies", "dev"),
        ("peerDependencies", "peer"),
        ("optionalDependencies", "optional"),
    ):
        block = data.get(field_name)
        if isinstance(block, dict):
            for name, spec in block.items():
                if isinstance(name, str) and isinstance(spec, str):
                    packages.append(Package(name, spec, kind, "npm", manifest, npm_pinned(spec)))
    scripts = data.get("scripts")
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    info: dict[str, Any] = {
        "path": manifest,
        "ecosystem": "npm",
        "name": data.get("name") if isinstance(data.get("name"), str) else None,
        "version": data.get("version") if isinstance(data.get("version"), str) else None,
        "private": bool(data.get("private")),
        "type": data.get("type") if isinstance(data.get("type"), str) else None,
        "scripts": {k: str(v)[:160] for k, v in scripts.items()}
        if isinstance(scripts, dict)
        else {},
        "engines": data.get("engines") if isinstance(data.get("engines"), dict) else {},
        "package_manager": (
            data.get("packageManager") if isinstance(data.get("packageManager"), str) else None
        ),
        "workspaces": workspaces if isinstance(workspaces, list) else [],
    }
    return packages, info


def _iter_requirements(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def parse_pyproject(text: str, manifest: str) -> tuple[list[Package], dict[str, Any]]:
    data = tomllib.loads(text)
    packages: list[Package] = []
    project = as_dict(data.get("project"))
    build = as_dict(data.get("build-system"))
    tool = as_dict(data.get("tool"))

    def add(reqs: list[str], kind: str) -> None:
        for req in reqs:
            parsed = split_python_requirement(req)
            if parsed:
                name, spec = parsed
                packages.append(Package(name, spec, kind, "python", manifest, python_pinned(spec)))

    add(_iter_requirements(project.get("dependencies")), "runtime")
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for group, reqs in optional.items():
            add(_iter_requirements(reqs), f"optional:{group}")
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group, reqs in groups.items():
            add(_iter_requirements(reqs), f"group:{group}")
    add(_iter_requirements(build.get("requires")), "build")
    poetry = as_dict(tool.get("poetry"))
    for section, kind in (("dependencies", "runtime"), ("dev-dependencies", "dev")):
        block = poetry.get(section)
        if isinstance(block, dict):
            for name, spec in block.items():
                if name.lower() == "python":
                    continue
                spec_text = (
                    spec
                    if isinstance(spec, str)
                    else str(spec.get("version", ""))
                    if isinstance(spec, dict)
                    else ""
                )
                packages.append(
                    Package(
                        name.lower(),
                        spec_text,
                        kind,
                        "python",
                        manifest,
                        spec_text.startswith("=="),
                    )
                )
    scripts = project.get("scripts")
    info: dict[str, Any] = {
        "path": manifest,
        "ecosystem": "python",
        "name": project.get("name") if isinstance(project.get("name"), str) else None,
        "version": project.get("version") if isinstance(project.get("version"), str) else None,
        "requires_python": project.get("requires-python")
        if isinstance(project.get("requires-python"), str)
        else None,
        "build_backend": build.get("build-backend")
        if isinstance(build.get("build-backend"), str)
        else None,
        "tools": sorted(str(k) for k in tool),
        "scripts": len(scripts) if isinstance(scripts, dict) else 0,
        "dependency_groups": sorted(str(k) for k in groups) if isinstance(groups, dict) else [],
    }
    return packages, info


def parse_requirements(text: str, manifest: str) -> tuple[list[Package], dict[str, Any]]:
    packages: list[Package] = []
    lowered = manifest.lower()
    kind = "dev" if any(tag in lowered for tag in ("dev", "test", "lint", "docs")) else "runtime"
    for line in text.splitlines():
        parsed = split_python_requirement(line)
        if parsed:
            name, spec = parsed
            packages.append(Package(name, spec, kind, "python", manifest, python_pinned(spec)))
    return packages, {"path": manifest, "ecosystem": "python", "kind": "requirements"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_msbuild(text: str, manifest: str) -> tuple[list[Package], dict[str, Any]]:
    root = ET.fromstring(text)
    packages: list[Package] = []
    props: dict[str, str] = {}
    project_refs = 0
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "PackageReference":
            name = element.get("Include") or element.get("Update")
            version = element.get("Version") or element.get("VersionOverride")
            if version is None:
                child = next((c for c in element if _local(c.tag) == "Version"), None)
                version = child.text.strip() if child is not None and child.text else None
            if name:
                spec = version or ""
                pinned = bool(_DOTNET_EXACT_RE.match(spec)) if spec else None
                packages.append(Package(name, spec, "runtime", "dotnet", manifest, pinned))
        elif tag == "PackageVersion":
            name = element.get("Include")
            version = element.get("Version") or ""
            if name:
                packages.append(
                    Package(
                        name,
                        version,
                        "central",
                        "dotnet",
                        manifest,
                        bool(_DOTNET_EXACT_RE.match(version)),
                    )
                )
        elif tag == "ProjectReference":
            project_refs += 1
        elif tag in (
            "TargetFramework", "TargetFrameworks", "OutputType", "Nullable", "LangVersion",
            "TreatWarningsAsErrors", "IsPackable", "IsTestProject", "ImplicitUsings",
            "RestorePackagesWithLockFile", "ManagePackageVersionsCentrally",
            "PackageLicenseExpression", "GenerateDocumentationFile", "EnableNETAnalyzers",
            "AnalysisLevel", "Deterministic", "ContinuousIntegrationBuild",
        ) and element.text:  # fmt: skip
            props[tag] = element.text.strip()
    frameworks = props.get("TargetFrameworks") or props.get("TargetFramework") or ""
    info: dict[str, Any] = {
        "path": manifest,
        "ecosystem": "dotnet",
        "target_frameworks": [f for f in frameworks.split(";") if f],
        "output_type": props.get("OutputType"),
        "project_references": project_refs,
        "properties": props,
    }
    return packages, info


def parse_cargo(text: str, manifest: str) -> tuple[list[Package], dict[str, Any]]:
    data = tomllib.loads(text)
    packages: list[Package] = []
    for section, kind in (
        ("dependencies", "runtime"),
        ("dev-dependencies", "dev"),
        ("build-dependencies", "build"),
    ):
        block = data.get(section)
        if isinstance(block, dict):
            for name, spec in block.items():
                version = (
                    spec
                    if isinstance(spec, str)
                    else str(spec.get("version", ""))
                    if isinstance(spec, dict)
                    else ""
                )
                packages.append(
                    Package(name, version, kind, "rust", manifest, version.startswith("="))
                )
    package = as_dict(data.get("package"))
    info = {
        "path": manifest,
        "ecosystem": "rust",
        "name": package.get("name"),
        "edition": package.get("edition"),
        "workspace": isinstance(data.get("workspace"), dict),
    }
    return packages, info


def parse_go_mod(text: str, manifest: str) -> tuple[list[Package], dict[str, Any]]:
    packages = [
        Package(
            m.group(1), m.group(2), "indirect" if m.group(3) else "runtime", "go", manifest, True
        )
        for m in _GO_REQUIRE_RE.finditer(text)
        if m.group(1) not in _GO_KEYWORDS
    ]
    go_version = re.search(r"^go\s+(\S+)", text, re.MULTILINE)
    return packages, {
        "path": manifest,
        "ecosystem": "go",
        "go_version": go_version.group(1) if go_version else None,
    }


def parse_manifest(info: FileInfo, text: str) -> tuple[list[Package], dict[str, Any]] | None:
    name = info.name
    lowered = name.lower()
    if lowered == "package.json":
        return parse_package_json(text, info.path)
    if lowered == "pyproject.toml":
        return parse_pyproject(text, info.path)
    if lowered.startswith("requirements") and lowered.endswith(".txt"):
        return parse_requirements(text, info.path)
    if info.ext in {".csproj", ".fsproj", ".vbproj"} or name == "Directory.Packages.props":
        return parse_msbuild(text, info.path)
    if lowered == "cargo.toml":
        return parse_cargo(text, info.path)
    if lowered == "go.mod":
        return parse_go_mod(text, info.path)
    return None


def _package_manager(ecosystem: str, lockfiles: list[str], npm_field: str | None) -> str | None:
    if ecosystem == "npm":
        if npm_field:
            return npm_field.split("@", 1)[0]
        for lock, manager in (
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lock", "bun"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
        ):
            if any(path.endswith(lock) for path in lockfiles):
                return manager
        return "npm"
    if ecosystem == "python":
        for lock, manager in (
            ("uv.lock", "uv"),
            ("poetry.lock", "poetry"),
            ("pdm.lock", "pdm"),
            ("Pipfile.lock", "pipenv"),
        ):
            if any(path.endswith(lock) for path in lockfiles):
                return manager
        return "pip"
    if ecosystem == "dotnet":
        return "nuget"
    return {"rust": "cargo", "go": "go"}.get(ecosystem)


class DepsMiner:
    name = "deps"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "deps.json"
    md_file = None

    def run(self, ctx: MineContext) -> MinerResult:
        files = ctx.files()
        manifests = sorted(
            (
                f
                for f in files
                if f.manifest_kind in {"npm", "python", "dotnet", "rust", "go"}
                and not f.vendored
                and not f.generated
                and f.ext not in {".sln", ".slnx"}
                and f.name
                not in {"global.json", "Directory.Build.props", "Pipfile", "setup.py", "setup.cfg"}
            ),
            key=lambda f: (f.depth, f.path),
        )
        packages: list[Package] = []
        infos: list[dict[str, Any]] = []
        warnings: list[str] = []
        for info in manifests[:MAX_MANIFESTS]:
            text = ctx.read(info.path, limit=262_144)
            try:
                parsed = parse_manifest(info, text)
            except (ValueError, tomllib.TOMLDecodeError, ET.ParseError) as exc:
                warnings.append(f"could not parse {info.path}: {type(exc).__name__}")
                continue
            if parsed is None:
                continue
            found, meta = parsed
            packages.extend(found)
            infos.append(meta)
        if len(manifests) > MAX_MANIFESTS:
            warnings.append(f"{len(manifests) - MAX_MANIFESTS} manifests not parsed (limit)")

        lockfiles = [
            {"path": f.path, "ecosystem": LOCKFILES[f.name]}
            for f in files
            if f.lockfile and not f.vendored and f.depth <= 3
        ]
        ecosystems = sorted({p.ecosystem for p in packages} | {m["ecosystem"] for m in infos})
        policies: dict[str, dict[str, Any]] = {}
        root_npm = next(
            (m for m in infos if m.get("ecosystem") == "npm" and m["path"] == "package.json"), None
        )
        for eco in ecosystems:
            own = [p for p in packages if p.ecosystem == eco and p.kind not in {"central"}]
            judged = [p for p in own if p.pinned is not None]
            pinned = sum(1 for p in judged if p.pinned)
            eco_locks = [lock["path"] for lock in lockfiles if lock["ecosystem"] == eco]
            central = any(
                m.get("ecosystem") == "dotnet"
                and str(m.get("properties", {}).get("ManagePackageVersionsCentrally", "")).lower()
                == "true"
                for m in infos
            )
            policies[eco] = {
                "packages": len(own),
                "runtime": sum(1 for p in own if p.kind == "runtime"),
                "dev": sum(1 for p in own if p.kind != "runtime"),
                "pinned_ratio": round(pinned / len(judged), 2) if judged else None,
                "lockfiles": eco_locks,
                "package_manager": _package_manager(
                    eco, eco_locks, root_npm.get("package_manager") if root_npm else None
                ),
                "central_package_management": central if eco == "dotnet" else None,
            }
        names = {
            eco: sorted({p.name.lower() for p in packages if p.ecosystem == eco})
            for eco in ecosystems
        }
        target_frameworks = sorted(
            {
                tf
                for m in infos
                if m.get("ecosystem") == "dotnet"
                for tf in m.get("target_frameworks", [])
            }
        )
        data: dict[str, Any] = {
            "ecosystems": ecosystems,
            "package_count": len(packages),
            "packages": [p.to_dict() for p in packages[:MAX_PACKAGES]],
            "manifests": infos,
            "lockfiles": lockfiles,
            "policies": policies,
            "npm_scripts": root_npm["scripts"] if root_npm else {},
            "engines": root_npm["engines"] if root_npm else {},
            "workspaces": root_npm["workspaces"] if root_npm else [],
            "target_frameworks": target_frameworks,
            "requires_python": next(
                (m["requires_python"] for m in infos if m.get("requires_python")), None
            ),
            "build_backend": next(
                (m["build_backend"] for m in infos if m.get("build_backend")), None
            ),
            "python_tools": sorted({t for m in infos for t in m.get("tools", [])}),
        }
        extra = {"packages": packages, "names": names, "manifests": infos}
        return MinerResult(self.name, data, extra=extra, warnings=warnings)
