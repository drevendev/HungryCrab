"""Literal Bundler lock evidence. Never evaluate Gemfiles or resolve packages."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


def ruby_lock(text: str, path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    section = ""
    source = ""
    versions: dict[str, str] = {}
    direct: set[str] = set()
    ambiguous: set[str] = set()
    unsupported: list[str] = []
    if len(text.encode("utf-8")) > 262_144:
        return [], {"path": path, "ecosystem": "ruby", "unsupported": ["byte-limit"]}
    for line in text.splitlines()[:10_000]:
        if not line.strip():
            continue
        if not line.startswith(" "):
            section = line.strip()
            if section in {"GIT", "PATH"}:
                unsupported.append(section.lower() + "-source")
            elif section not in {
                "GEM",
                "DEPENDENCIES",
                "PLATFORMS",
                "BUNDLED WITH",
                "RUBY VERSION",
                "CHECKSUMS",
            }:
                unsupported.append("unknown-section")
            continue
        if section == "GEM":
            if line.startswith("  remote:"):
                parsed = urlsplit(line.partition(":")[2].strip())
                source = f"{parsed.scheme}://{parsed.hostname}" if parsed.hostname else "unknown"
            match = re.fullmatch(
                r"    ([A-Za-z0-9][A-Za-z0-9_.-]{0,100}) \(([0-9][A-Za-z0-9.+_-]{0,100})\)", line
            )
            if match:
                if match.group(1) in versions and versions[match.group(1)] != match.group(2):
                    unsupported.append("multiple-platform-versions")
                    ambiguous.add(match.group(1))
                else:
                    versions[match.group(1)] = match.group(2)
        elif section == "DEPENDENCIES":
            match = re.fullmatch(
                r"  ([A-Za-z0-9][A-Za-z0-9_.-]{0,100})(?: \([^\n]{1,100}\))?(!)?", line
            )
            if match and not match.group(2):
                direct.add(match.group(1))
            else:
                unsupported.append("unsupported-direct-dependency")
    if len(text.splitlines()) > 10_000:
        unsupported.append("line-limit")
    if len(versions) > 800:
        unsupported.append("package-limit")
    records = [
        {
            "name": name,
            "spec": version,
            "kind": "runtime" if name in direct else "indirect",
            "ecosystem": "ruby",
            "manifest": path,
            "pinned": True,
        }
        for name, version in sorted(versions.items())[:800]
        if name not in ambiguous
    ]
    meta: dict[str, Any] = {
        "path": path,
        "ecosystem": "ruby",
        "package_manager": "bundler",
        "source": source[:300],
        "unsupported": sorted(set(unsupported)),
        "coverage": "partial" if unsupported else "supported-lock-sections",
    }
    return records, meta
