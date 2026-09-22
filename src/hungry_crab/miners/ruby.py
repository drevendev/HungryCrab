"""Static Ruby/Bundler dependency evidence.

Gemfiles and gemspecs are Ruby programs, so prey declarations are parsed as data only. This
module deliberately recognises a small literal subset and reports unsupported dynamic forms
instead of loading, evaluating, or otherwise executing them.
"""

from __future__ import annotations

import re
from typing import Any, cast

from .base import FileInfo, MineContext, MinerResult
from .deps import MAX_MANIFESTS, MAX_PACKAGES, Package

_RUBY_EXACT_RE = re.compile(r"^=?\s*\d+(?:\.\d+)*(?:[-+][A-Za-z0-9_.-]+)?$")
_GEM_CALL_RE = re.compile(
    r"^\s*gem\s*(?:\(\s*)?(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)(?P<tail>.*)$"
)
_GEMSPEC_DEP_RE = re.compile(
    r"^\s*[A-Za-z_][\w.]*\.(?P<method>add_(?:runtime_)?dependency|add_development_dependency)"
    r"\s*(?:\(\s*)?(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)(?P<tail>.*)$"
)
_LEADING_STRING_RE = re.compile(r"^\s*(?P<quote>['\"])(?P<value>[^'\"]*)(?P=quote)")
_GROUP_BLOCK_RE = re.compile(r"^\s*group\s+(?P<groups>.+?)\s+do\s*(?:#.*)?$")
_GROUP_NAME_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_INLINE_DEV_GROUP_RE = re.compile(
    r"\bgroup(?:s)?\s*:\s*(?:\[[^\]]*)?:(?:development|test)\b", re.IGNORECASE
)
_GENERIC_BLOCK_RE = re.compile(r"\bdo(?:\s*\|[^|]*\|)?\s*(?:#.*)?$")
_LOCK_SPEC_RE = re.compile(r"^\s{4}([A-Za-z0-9_.-]+)\s+\(([^)]+)\)\s*$")
_LOCK_DEP_RE = re.compile(r"^\s{2}([A-Za-z0-9_.-]+)(?:\s+\([^)]+\))?[! ]*\s*$")
_LOCK_SECTION_RE = re.compile(r"^[A-Z][A-Z ]+$")


def ruby_pinned(spec: str) -> bool | None:
    """Whether a statically known Ruby requirement is one exact version."""

    text = spec.strip()
    if not text:
        return False
    return bool(_RUBY_EXACT_RE.fullmatch(text))


def _literal_specs(tail: str) -> tuple[str, bool]:
    """Return literal positional requirements and whether a positional value was dynamic."""

    rest = tail
    specs: list[str] = []
    while True:
        rest = rest.lstrip()
        if not rest.startswith(","):
            break
        rest = rest[1:].lstrip()
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*\s*:", rest):
            break
        match = _LEADING_STRING_RE.match(rest)
        if match is None:
            return ", ".join(specs), True
        specs.append(match.group("value"))
        rest = rest[match.end() :]
    return ", ".join(specs), False


def _development_group(text: str) -> bool:
    return any(name.lower() in {"development", "test"} for name in _GROUP_NAME_RE.findall(text))


def parse_gemfile(text: str, manifest: str) -> tuple[list[Package], list[str]]:
    """Parse literal Gemfile ``gem`` calls without evaluating Ruby."""

    packages: list[Package] = []
    warnings: list[str] = []
    block_dev: list[bool] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        group = _GROUP_BLOCK_RE.match(raw)
        if group is not None:
            block_dev.append(any(block_dev) or _development_group(group.group("groups")))
            continue
        if stripped == "end" and block_dev:
            block_dev.pop()
            continue
        if _GENERIC_BLOCK_RE.search(raw):
            block_dev.append(any(block_dev))
            continue
        if re.match(r"^\s*eval_gemfile\b", raw):
            warnings.append(f"dynamic Ruby include not followed in {manifest}:{line_no}")
            continue
        if not re.match(r"^\s*gem\b", raw):
            continue
        match = _GEM_CALL_RE.match(raw)
        if match is None:
            warnings.append(f"dynamic Ruby dependency not parsed in {manifest}:{line_no}")
            continue
        spec, dynamic_spec = _literal_specs(match.group("tail"))
        kind = (
            "dev"
            if any(block_dev) or _INLINE_DEV_GROUP_RE.search(match.group("tail")) is not None
            else "runtime"
        )
        packages.append(
            Package(
                match.group("name"),
                spec,
                kind,
                "ruby",
                manifest,
                None if dynamic_spec else ruby_pinned(spec),
            )
        )
        if dynamic_spec:
            warnings.append(f"dynamic Ruby requirement not parsed in {manifest}:{line_no}")
    return packages, warnings


def parse_gemspec(text: str, manifest: str) -> tuple[list[Package], list[str]]:
    """Parse common literal gemspec dependency calls without evaluating Ruby."""

    packages: list[Package] = []
    warnings: list[str] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not re.search(r"\.add_(?:runtime_)?dependency|\.add_development_dependency", raw):
            continue
        match = _GEMSPEC_DEP_RE.match(raw)
        if match is None:
            warnings.append(f"dynamic Ruby dependency not parsed in {manifest}:{line_no}")
            continue
        spec, dynamic_spec = _literal_specs(match.group("tail"))
        method = match.group("method")
        kind = "dev" if method == "add_development_dependency" else "runtime"
        packages.append(
            Package(
                match.group("name"),
                spec,
                kind,
                "ruby",
                manifest,
                None if dynamic_spec else ruby_pinned(spec),
            )
        )
        if dynamic_spec:
            warnings.append(f"dynamic Ruby requirement not parsed in {manifest}:{line_no}")
    return packages, warnings


def parse_gemfile_lock(text: str, lockfile: str) -> tuple[list[dict[str, str]], list[str]]:
    """Return exact versions for direct Bundler dependencies from a lockfile."""

    resolved: dict[str, str] = {}
    direct: set[str] = set()
    in_specs = False
    in_dependencies = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if _LOCK_SECTION_RE.fullmatch(raw):
            in_specs = False
            in_dependencies = raw == "DEPENDENCIES"
            continue
        if stripped == "specs:":
            in_specs = True
            in_dependencies = False
            continue
        if in_specs:
            match = _LOCK_SPEC_RE.match(raw)
            if match is not None:
                resolved.setdefault(match.group(1), match.group(2))
        elif in_dependencies:
            match = _LOCK_DEP_RE.match(raw)
            if match is not None:
                direct.add(match.group(1))

    rows = [
        {"name": name, "version": resolved[name], "lockfile": lockfile}
        for name in sorted(direct & resolved.keys())
    ]
    missing = sorted(direct - resolved.keys())
    warnings = [f"Ruby lock resolution missing for {name} in {lockfile}" for name in missing]
    return rows, warnings


def _ruby_manifest_files(files: list[FileInfo]) -> list[FileInfo]:
    return sorted(
        (
            info
            for info in files
            if not info.vendored
            and not info.generated
            and not info.binary
            and (info.name == "Gemfile" or info.ext == ".gemspec")
        ),
        key=lambda info: (info.depth, info.path),
    )


def enrich_ruby_dependencies(result: MinerResult, ctx: MineContext) -> MinerResult:
    """Add static Ruby/Bundler evidence to a dependency-miner result in place."""

    manifests = _ruby_manifest_files(ctx.files())
    if not manifests:
        return result

    ruby_packages: list[Package] = []
    ruby_infos: list[dict[str, Any]] = []
    warnings: list[str] = []
    for info in manifests[:MAX_MANIFESTS]:
        text = ctx.read(info.path, limit=262_144)
        if info.name == "Gemfile":
            found, found_warnings = parse_gemfile(text, info.path)
            kind = "gemfile"
        else:
            found, found_warnings = parse_gemspec(text, info.path)
            kind = "gemspec"
        ruby_packages.extend(found)
        warnings.extend(found_warnings)
        ruby_infos.append({"path": info.path, "ecosystem": "ruby", "kind": kind})
    if len(manifests) > MAX_MANIFESTS:
        warnings.append(f"{len(manifests) - MAX_MANIFESTS} Ruby manifests not parsed (limit)")

    existing_packages = cast(list[Package], result.extra.get("packages", []))
    packages = [*existing_packages, *ruby_packages]
    result.extra["packages"] = packages
    result.data["package_count"] = len(packages)
    result.data["packages"] = [package.to_dict() for package in packages[:MAX_PACKAGES]]

    existing_infos = cast(list[dict[str, Any]], result.extra.get("manifests", []))
    infos = [*existing_infos, *ruby_infos]
    result.extra["manifests"] = infos
    result.data["manifests"] = infos

    names = dict(cast(dict[str, list[str]], result.extra.get("names", {})))
    names["ruby"] = sorted({package.name.lower() for package in ruby_packages})
    result.extra["names"] = names

    ecosystems = sorted(set(cast(list[str], result.data.get("ecosystems", []))) | {"ruby"})
    result.data["ecosystems"] = ecosystems
    judged = [package for package in ruby_packages if package.pinned is not None]
    pinned = sum(1 for package in judged if package.pinned)
    ruby_lockfiles = [
        str(row["path"])
        for row in cast(list[dict[str, Any]], result.data.get("lockfiles", []))
        if row.get("ecosystem") == "ruby"
    ]
    policies = dict(cast(dict[str, dict[str, Any]], result.data.get("policies", {})))
    has_gemfile = any(info.name == "Gemfile" for info in manifests[:MAX_MANIFESTS])
    policies["ruby"] = {
        "packages": len(ruby_packages),
        "runtime": sum(1 for package in ruby_packages if package.kind == "runtime"),
        "dev": sum(1 for package in ruby_packages if package.kind != "runtime"),
        "pinned_ratio": round(pinned / len(judged), 2) if judged else None,
        "lockfiles": ruby_lockfiles,
        "package_manager": "bundler" if has_gemfile or ruby_lockfiles else "rubygems",
        "central_package_management": None,
    }
    result.data["policies"] = policies

    resolutions: list[dict[str, str]] = []
    for lockfile in ruby_lockfiles:
        lock_rows, lock_warnings = parse_gemfile_lock(ctx.read(lockfile, limit=524_288), lockfile)
        resolutions.extend(lock_rows)
        warnings.extend(lock_warnings)
    result.data["ruby_resolutions"] = resolutions
    result.warnings = list(dict.fromkeys([*result.warnings, *warnings]))
    return result
