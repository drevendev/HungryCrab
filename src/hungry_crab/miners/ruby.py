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
_ARGUMENT_COMMA_RE = re.compile(r"\s*,\s*")
_KEYWORD_ARGUMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*:")
_STRING_ARGUMENT_RE = re.compile(r"(?P<quote>['\"])(?P<value>[^'\"]*)(?P=quote)")
# Possessive whitespace, and group names that end on a non-space: `group\s+(.+?)\s+do` let
# every position inside one long whitespace run rescan the rest of it.
_GROUP_BLOCK_RE = re.compile(r"^\s*+group\s++(?P<groups>.*?\S)\s++do\s*+(?:#.*)?$")
_GROUP_NAME_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
# One match per `group:` key that always succeeds and swallows its bracket list, so the search
# resumes after the list. A pattern that had to find `:development` inside the list failed at
# every `group:[` of an unclosed list and rescanned the rest of the line each time.
_INLINE_GROUP_RE = re.compile(
    r"\bgroups?\s*:\s*(?:\[(?P<listed>[^\]]*)|(?P<named>:(?:development|test)\b))?",
    re.IGNORECASE,
)
_DEV_GROUP_SYMBOL_RE = re.compile(r":(?:development|test)\b", re.IGNORECASE)
_END_BLOCK_RE = re.compile(r"^end\s*(?:#.*)?$")
_END_TERMINATED_BLOCK_RE = re.compile(
    r"^\s*(?:if|unless|case|begin|class|module|def|while|until|for)\b"
)
_GENERIC_BLOCK_RE = re.compile(r"\bdo(?:\s*\|[^|]*\|)?\s*(?:#.*)?$")
_LOCK_SPEC_RE = re.compile(r"^\s{4}([A-Za-z0-9_.-]+)\s+\(([^)]+)\)\s*$")
# `[! ]*+`: two adjacent space-matching stars split a trailing run quadratically many ways.
_LOCK_DEP_RE = re.compile(r"^\s{2}([A-Za-z0-9_.-]+)(?:\s+\([^)]+\))?[! ]*+\s*$")
_LOCK_SECTION_RE = re.compile(r"^[A-Z][A-Z ]+$")


def ruby_pinned(spec: str) -> bool | None:
    """Whether a statically known Ruby requirement is one exact version."""

    text = spec.strip()
    if not text:
        return False
    return bool(_RUBY_EXACT_RE.fullmatch(text))


def _literal_specs(tail: str) -> tuple[str, bool]:
    """Return literal positional requirements and whether a positional value was dynamic."""

    # Positions, not slices: copying the rest of a 256 KiB line once per argument was
    # quadratic in the line.
    position = 0
    specs: list[str] = []
    while True:
        comma = _ARGUMENT_COMMA_RE.match(tail, position)
        if comma is None:
            break
        position = comma.end()
        if _KEYWORD_ARGUMENT_RE.match(tail, position):
            break
        match = _STRING_ARGUMENT_RE.match(tail, position)
        if match is None:
            return ", ".join(specs), True
        specs.append(match.group("value"))
        position = match.end()
    return ", ".join(specs), False


def _development_group(text: str) -> bool:
    return any(name.lower() in {"development", "test"} for name in _GROUP_NAME_RE.findall(text))


def _inline_development_group(tail: str) -> bool:
    """Whether a ``gem`` call's options say ``group: :development`` or list it in brackets."""
    return any(
        match.group("named") or _DEV_GROUP_SYMBOL_RE.search(match.group("listed") or "")
        for match in _INLINE_GROUP_RE.finditer(tail)
    )


_SHOWN_ITEMS = 5


def _shown(items: list[str]) -> str:
    """The first few items and a count of the rest."""
    more = len(items) - _SHOWN_ITEMS
    return ", ".join(items[:_SHOWN_ITEMS]) + (f" and {more} more" if more > 0 else "")


def _line_warnings(lines_by_note: dict[str, list[int]], manifest: str) -> list[str]:
    """One warning per kind of unparsed line, however many lines share it.

    A prey Gemfile can hold forty thousand dynamic lines; one warning each made the manifest
    tens of megabytes (#324).
    """
    warnings: list[str] = []
    for note, lines in lines_by_note.items():
        if len(lines) == 1:
            warnings.append(f"{note} in {manifest}:{lines[0]}")
        else:
            numbers = _shown([str(line) for line in lines])
            warnings.append(f"{note} in {manifest} on {len(lines)} lines: {numbers}")
    return warnings


def parse_gemfile(text: str, manifest: str) -> tuple[list[Package], list[str]]:
    """Parse literal Gemfile ``gem`` calls without evaluating Ruby."""

    packages: list[Package] = []
    notes: dict[str, list[int]] = {}
    block_dev: list[bool] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        group = _GROUP_BLOCK_RE.match(raw)
        if group is not None:
            block_dev.append(any(block_dev) or _development_group(group.group("groups")))
            continue
        if _END_BLOCK_RE.fullmatch(stripped) is not None and block_dev:
            block_dev.pop()
            continue
        if _END_TERMINATED_BLOCK_RE.match(raw):
            block_dev.append(any(block_dev))
            continue
        if _GENERIC_BLOCK_RE.search(raw):
            block_dev.append(any(block_dev))
            continue
        if re.match(r"^\s*eval_gemfile\b", raw):
            notes.setdefault("dynamic Ruby include not followed", []).append(line_no)
            continue
        if not re.match(r"^\s*gem\b", raw):
            continue
        match = _GEM_CALL_RE.match(raw)
        if match is None:
            notes.setdefault("dynamic Ruby dependency not parsed", []).append(line_no)
            continue
        spec, dynamic_spec = _literal_specs(match.group("tail"))
        kind = (
            "dev" if any(block_dev) or _inline_development_group(match.group("tail")) else "runtime"
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
            notes.setdefault("dynamic Ruby requirement not parsed", []).append(line_no)
    return packages, _line_warnings(notes, manifest)


def parse_gemspec(text: str, manifest: str) -> tuple[list[Package], list[str]]:
    """Parse common literal gemspec dependency calls without evaluating Ruby."""

    packages: list[Package] = []
    notes: dict[str, list[int]] = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not re.search(r"\.add_(?:runtime_)?dependency|\.add_development_dependency", raw):
            continue
        match = _GEMSPEC_DEP_RE.match(raw)
        if match is None:
            notes.setdefault("dynamic Ruby dependency not parsed", []).append(line_no)
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
            notes.setdefault("dynamic Ruby requirement not parsed", []).append(line_no)
    return packages, _line_warnings(notes, manifest)


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
    warnings: list[str] = []
    if len(missing) == 1:
        warnings.append(f"Ruby lock resolution missing for {missing[0]} in {lockfile}")
    elif missing:
        warnings.append(
            f"Ruby lock resolution missing for {len(missing)} dependencies in {lockfile}: "
            f"{_shown(missing)}"
        )
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
