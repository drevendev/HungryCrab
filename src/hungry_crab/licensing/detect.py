"""License detection from license files, manifests, file headers and the forge API."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..fs import read_text
from .matrix import LicenseClass, classify, normalize

_SPDX_ID_RE = re.compile(
    r"SPDX-License-Identifier:\s*([A-Za-z0-9.+\-]+(?:\s+(?:OR|AND|WITH)\s+[A-Za-z0-9.+\-]+)*)",
    re.IGNORECASE,
)
_LICENSE_FILE_RE = re.compile(
    r"^(LICENSE|LICENCE|COPYING|UNLICENSE|COPYRIGHT)(?:[-_.].*)?$", re.IGNORECASE
)
_NOTICE_FILE_RE = re.compile(
    r"^(NOTICE|THIRD[-_ ]PARTY[-_ ]NOTICES?|3RD[-_ ]PARTY.*)(\..*)?$", re.I
)
_NAMED_LICENSE_RE = re.compile(r"^(?:LICENSE|LICENCE|COPYING)[-_.](?P<name>[A-Za-z0-9.+]+)", re.I)
# The other convention: the license name comes first, `apache-2.0.LICENSE`, `cc-by-4.0.LICENSE`.
_SUFFIX_LICENSE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9.+-]*)[-_.](?:LICEN[CS]E|COPYING)(?:\.[A-Za-z0-9]+)?$", re.I
)
# A directory of license texts, one per license (the REUSE convention).
LICENSE_DIRS = frozenset({"licenses", "licences", "license-files"})
# `LICENSE.md` names a file format, not a license. Without this, the name reader called the
# license "md" with 0.6 confidence and the repository looked licensed under something unknown.
_TEXT_SUFFIXES = frozenset(
    {"md", "txt", "rst", "html", "htm", "adoc", "asciidoc", "markdown", "text", "doc"}
)
# Phrases that say a single file covers several situations rather than granting one license.
_SPLIT_RE = re.compile(
    r"portions of (?:this|the) \w+(?: \w+)? (?:are|is) licensed"
    r"|licensed as follows"
    r"|licens\w* transition|transitioning from the"
    r"|remain licensed under"
    r"|dual[- ]licen[cs]ed"
    r"|are not licensed",
    re.IGNORECASE,
)
# Licenses named in prose. Only consulted when `_SPLIT_RE` already said the file is a patchwork,
# so a passing mention inside an ordinary license text cannot move the verdict.
_MENTIONS: tuple[tuple[str, str], ...] = (
    ("apache license", "Apache-2.0"),
    ("apache-2.0", "Apache-2.0"),
    ("mit license", "MIT"),
    ("bsd 3-clause", "BSD-3-Clause"),
    ("bsd 2-clause", "BSD-2-Clause"),
    ("mozilla public license", "MPL-2.0"),
    ("affero general public license", "AGPL-3.0-only"),
    ("lesser general public license", "LGPL-3.0-only"),
    ("general public license", "GPL-3.0-only"),
    ("creative commons attribution 4.0", "CC-BY-4.0"),
    ("cc-by-4.0", "CC-BY-4.0"),
    ("cc-by-sa", "CC-BY-SA-4.0"),
    ("elastic license", "Elastic-2.0"),
    ("server side public license", "SSPL-1.0"),
    ("business source license", "BUSL-1.1"),
    ("sustainable use license", "LicenseRef-SustainableUse"),
    ("commons clause", "Commons-Clause"),
    ("enterprise license", "LicenseRef-Proprietary"),
    ("commercial license", "LicenseRef-Proprietary"),
)

_ISC_GRANT = (
    "permission to use, copy, modify, and/or distribute this software for any purpose "
    "with or without fee"
)

# (spdx, required phrases, forbidden phrases); evaluated on lower-cased, whitespace-collapsed text.
# More specific entries come first.
_SIGNATURES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Apache-2.0", ("apache license", "version 2.0"), ()),
    ("MPL-2.0", ("mozilla public license", "2.0"), ()),
    ("EPL-2.0", ("eclipse public license", "2.0"), ()),
    ("EPL-1.0", ("eclipse public license", "1.0"), ()),
    ("BUSL-1.1", ("business source license",), ()),
    ("SSPL-1.0", ("server side public license",), ()),
    ("Elastic-2.0", ("elastic license", "2.0"), ()),
    ("Commons-Clause", ("commons clause",), ()),
    ("Unlicense", ("this is free and unencumbered software released into the public domain",), ()),
    ("CC0-1.0", ("cc0 1.0",), ()),
    ("CC-BY-NC-SA-4.0", ("attribution-noncommercial-sharealike 4.0",), ()),
    ("CC-BY-NC-4.0", ("attribution-noncommercial 4.0",), ()),
    ("CC-BY-ND-4.0", ("attribution-noderivatives 4.0",), ()),
    ("CC-BY-SA-4.0", ("attribution-sharealike 4.0",), ()),
    ("CC-BY-4.0", ("attribution 4.0 international",), ()),
    ("CC-BY-SA-3.0", ("attribution-sharealike 3.0",), ()),
    ("CC-BY-3.0", ("attribution 3.0 unported",), ()),
    ("GFDL-1.3-only", ("gnu free documentation license",), ()),
    ("BSL-1.0", ("boost software license",), ()),
    ("WTFPL", ("do what the fuck you want to public license",), ()),
    ("Artistic-2.0", ("artistic license 2.0",), ()),
    ("PostgreSQL", ("postgresql license",), ()),
    ("Python-2.0", ("python software foundation license",), ()),
    ("BlueOak-1.0.0", ("blue oak model license",), ()),
    (
        "Zlib",
        ("altered source versions must be plainly marked as such", "must not be misrepresented"),
        (),
    ),
    (
        "ISC",
        (
            _ISC_GRANT,
            "provided that the above copyright notice and this permission notice appear",
        ),
        (),
    ),
    ("0BSD", (_ISC_GRANT,), ()),
    (
        "MIT",
        (
            "permission is hereby granted, free of charge",
            "without restriction",
            "shall be included in all copies or substantial portions",
        ),
        (),
    ),
    ("MIT-0", ("permission is hereby granted, free of charge", "without restriction"), ()),
    ("BSD-4-Clause", ("redistributions of source code must retain", "advertising materials"), ()),
    ("BSD-3-Clause", ("redistributions of source code must retain", "neither the name"), ()),
    ("BSD-2-Clause", ("redistributions of source code must retain",), ()),
)

_OSI = "License :: OSI Approved :: "
_CLASSIFIER_MAP: dict[str, str] = {
    _OSI + "MIT License": "MIT",
    _OSI + "Apache Software License": "Apache-2.0",
    _OSI + "BSD License": "BSD-3-Clause",
    _OSI + "ISC License (ISCL)": "ISC",
    _OSI + "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    _OSI + "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    _OSI + "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    _OSI + "GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    _OSI + "GNU Affero General Public License v3": "AGPL-3.0-only",
    _OSI + "GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0-or-later",
    _OSI + "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0-only",
    _OSI + "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    _OSI + "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    _OSI + "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    _OSI + "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    _OSI + "The Unlicense (Unlicense)": "Unlicense",
    _OSI + "zlib/libpng License": "Zlib",
    _OSI + "Eclipse Public License 2.0 (EPL-2.0)": "EPL-2.0",
    _OSI + "Python Software Foundation License": "PSF-2.0",
    "License :: Public Domain": "CC0-1.0",
    "License :: Other/Proprietary License": "LicenseRef-Proprietary",
}


def is_license_file_name(name: str) -> bool:
    return bool(_LICENSE_FILE_RE.match(name) or _SUFFIX_LICENSE_RE.match(name))


def license_name_from_file(name: str) -> str | None:
    """The SPDX id a license file's *name* declares, or None when the name says nothing.

    ``LICENSE-APACHE`` declares Apache-2.0. ``LICENSE.md`` declares nothing: ``md`` is a file
    format. ``apache-2.0.LICENSE`` declares Apache-2.0 the other way round.
    """
    named = _NAMED_LICENSE_RE.match(name)
    if named:
        raw = named.group("name")
        if raw.lower() in _TEXT_SUFFIXES:
            return None
        return normalize(raw)
    suffixed = _SUFFIX_LICENSE_RE.match(name)
    if suffixed:
        return normalize(suffixed.group("name"))
    return None


def licenses_mentioned(text: str) -> list[str]:
    """SPDX ids named in prose, for a file that says several licenses apply."""
    lowered = _norm(text)
    found: list[str] = []
    for phrase, spdx in _MENTIONS:
        if phrase in lowered and spdx not in found:
            found.append(spdx)
    # "affero general public license" also contains "general public license".
    if "AGPL-3.0-only" in found or "LGPL-3.0-only" in found:
        found = [item for item in found if item != "GPL-3.0-only"]
    return found


def is_split_license_text(text: str) -> bool:
    """True when one file says different parts of the repository carry different licenses."""
    return bool(_SPLIT_RE.search(text))


_QUOTES = str.maketrans({chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'})


def _norm(text: str) -> str:
    return " ".join(text.translate(_QUOTES).lower().split())


def find_spdx_identifier(text: str) -> str | None:
    match = _SPDX_ID_RE.search(text)
    if not match:
        return None
    return normalize(match.group(1).strip().rstrip("*/-> "))


def _detect_gnu(head: str) -> tuple[str | None, float]:
    if "general public license" not in head:
        return None, 0.0
    later = "any later version" in head
    if "affero" in head:
        return ("AGPL-3.0-or-later" if later else "AGPL-3.0-only"), 0.95
    if "lesser general public license" in head or "library general public license" in head:
        if "version 3" in head:
            return ("LGPL-3.0-or-later" if later else "LGPL-3.0-only"), 0.95
        if "version 2.1" in head:
            return ("LGPL-2.1-or-later" if later else "LGPL-2.1-only"), 0.95
        return "LGPL-2.0-only", 0.8
    if "version 3" in head:
        return ("GPL-3.0-or-later" if later else "GPL-3.0-only"), 0.95
    if "version 2" in head:
        return ("GPL-2.0-or-later" if later else "GPL-2.0-only"), 0.95
    return "GPL-3.0-only", 0.6


def detect_from_text(text: str) -> tuple[str | None, float]:
    """Return (spdx, confidence) for the text of a license file."""
    if not text.strip():
        return None, 0.0
    ident = find_spdx_identifier(text)
    if ident:
        return ident, 0.99
    normalized = _norm(text)
    head = normalized[:700]
    gnu, confidence = _detect_gnu(head)
    if gnu:
        return gnu, confidence
    for spdx, required, forbidden in _SIGNATURES:
        if all(phrase in normalized for phrase in required) and not any(
            phrase in normalized for phrase in forbidden
        ):
            return spdx, 0.95
    if "all rights reserved" in normalized and len(normalized) < 3000:
        return "LicenseRef-Proprietary", 0.5
    return None, 0.0


def manifest_license(name: str, text: str) -> tuple[str | None, str]:
    """(spdx, note) declared by a manifest file, or (None, '')."""
    lowered = name.lower()
    try:
        if lowered == "package.json" or lowered == "composer.json":
            data = json.loads(text)
            if isinstance(data, dict):
                value = data.get("license")
                if isinstance(value, dict):
                    value = value.get("type")
                if isinstance(value, str) and value.strip():
                    return normalize(value), ""
                legacy = data.get("licenses")
                if isinstance(legacy, list) and legacy and isinstance(legacy[0], dict):
                    first = legacy[0].get("type")
                    if isinstance(first, str):
                        return normalize(first), "legacy licenses array"
            return None, ""
        if lowered == "pyproject.toml":
            return _pyproject_license(tomllib.loads(text))
        if lowered == "cargo.toml":
            data = tomllib.loads(text)
            package = data.get("package", {})
            value = package.get("license") if isinstance(package, dict) else None
            return (normalize(value) if isinstance(value, str) else None), ""
        if lowered.endswith((".csproj", ".fsproj", ".vbproj", ".props")):
            match = re.search(
                r"<PackageLicenseExpression>\s*([^<]+?)\s*</PackageLicenseExpression>", text
            )
            if match:
                return normalize(match.group(1)), ""
            return None, ""
        if lowered == "setup.cfg":
            match = re.search(r"^\s*license\s*=\s*(.+)$", text, re.MULTILINE)
            return (normalize(match.group(1).strip()) if match else None), ""
        if lowered == "setup.py":
            match = re.search(r"""license\s*=\s*["']([^"']+)["']""", text)
            return (normalize(match.group(1)) if match else None), ""
    except (ValueError, TypeError, tomllib.TOMLDecodeError):
        return None, "unparseable manifest"
    return None, ""


def _pyproject_license(data: dict[str, Any]) -> tuple[str | None, str]:
    project = data.get("project")
    if isinstance(project, dict):
        value = project.get("license")
        if isinstance(value, str) and value.strip():
            return normalize(value), ""
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                return normalize(text.strip()), "license.text"
        classifiers = project.get("classifiers")
        if isinstance(classifiers, list):
            for item in classifiers:
                if isinstance(item, str) and item in _CLASSIFIER_MAP:
                    note = "from trove classifier"
                    if "BSD License" in item:
                        note = "trove classifier 'BSD License' does not name the variant"
                    return _CLASSIFIER_MAP[item], note
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict) and isinstance(poetry.get("license"), str):
            return normalize(poetry["license"]), "tool.poetry"
    return None, ""


@dataclass
class Evidence:
    source: str
    path: str
    spdx: str | None
    confidence: float
    note: str = ""


@dataclass
class LicenseFindings:
    spdx: str | None = None
    confidence: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)
    license_files: list[str] = field(default_factory=list)
    notice_files: list[str] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    header_counts: dict[str, int] = field(default_factory=dict)
    headers_scanned: int = 0
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    human_review: bool = False
    # Why the repository ended up with this identifier. `NOASSERTION` used to mean all four of
    # the last values at once, which is the same as meaning nothing.
    #   single      one license, one identifier
    #   dual        several license files offered as alternatives (SPDX `OR`)
    #   split       several licenses that apply to different parts (conservative, SPDX `AND`)
    #   per-path    nothing at the root, but nested packages carry their own licenses
    #   unreadable  a license file is there and no known license text matched it
    #   absent      no license file, no manifest declaration, no API license
    resolution: str = "absent"
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [asdict(item) for item in self.evidence]
        return data


def _same_family(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    if a == b:
        return True
    fam_a = a.split("-")[0].upper()
    fam_b = b.split("-")[0].upper()
    if fam_a == fam_b == "BSD":
        return True
    return (
        classify(a) == classify(b)
        and a.rstrip("+").split("-or-later")[0] == b.rstrip("+").split("-or-later")[0]
    )


def _license_candidates(
    root: Path, entries: Sequence[str], findings: LicenseFindings
) -> list[tuple[str, str | None]]:
    """Every license file at the root or in a `LICENSES/` directory, with what its name declares.

    Notice files are recorded on ``findings`` on the way past, because they are found by the same
    walk and nothing else looks at the root.
    """
    found: list[tuple[str, str | None]] = []
    for name in sorted(entries):
        if _NOTICE_FILE_RE.match(name) and (root / name).is_file():
            findings.notice_files.append(name)
            continue
        if not is_license_file_name(name):
            continue
        if (root / name).is_file():
            found.append((name, license_name_from_file(name)))
    # Read the directory name from the listing, not from the probe: on Windows `root / "licenses"`
    # happily opens `LICENSES/` and would then report a path that does not exist on Linux.
    for directory in sorted(name for name in entries if name.lower() in LICENSE_DIRS):
        path = root / directory
        if not path.is_dir():
            continue
        try:
            children = sorted(child for child in path.iterdir() if child.is_file())
        except OSError:
            continue
        # `LICENSES/MIT.txt`: here the whole stem is the license name, by the REUSE convention.
        found.extend((f"{path.name}/{child.name}", normalize(child.stem)) for child in children)
    return found


def _resolve_unclear(findings: LicenseFindings, split_mentions: Sequence[str]) -> None:
    """Separate the three situations that used to share one `NOASSERTION`.

    A file that says several licenses apply, a license file nobody can read, and a repository
    that licenses nothing at the root but licenses its packages are three different problems with
    three different answers. ``NOASSERTION`` is SPDX's own word for "looked, cannot assert", and
    it classifies as unknown, which is the mode ``HUMAN``.
    """
    # A repository that offers a choice of licenses has already answered the question; a file that
    # merely mentions other licenses cannot take that choice away.
    if split_mentions and findings.resolution != "dual":
        current = findings.spdx.split(" AND ") if findings.spdx else []
        combined = sorted({*split_mentions, *current})
        if set(combined) != set(current):
            findings.resolution = "split"
            findings.candidates = combined
            findings.spdx = " AND ".join(combined)
            findings.notes.append(
                "the license file says different parts carry different licenses "
                f"({', '.join(combined)}); the most restrictive one is used for the whole "
                "repository until a nutrient names its own path"
            )
        return
    if findings.spdx is not None:
        return
    if findings.license_files:
        findings.resolution = "unreadable"
        findings.spdx = "NOASSERTION"
        findings.notes.append(
            f"{', '.join(findings.license_files)} is present but matches no known license text: "
            "a human has to read it"
        )
        return
    if findings.exceptions:
        paths = ", ".join(f"{item['path']} ({item['spdx']})" for item in findings.exceptions[:5])
        findings.resolution = "per-path"
        findings.spdx = "NOASSERTION"
        findings.notes.append(
            f"no license at the root, but {len(findings.exceptions)} nested license file(s) "
            f"carry their own: {paths}. Each package has to be judged on its own"
        )


def detect_in_repo(
    root: Path,
    code_files: Sequence[str],
    *,
    root_entries: Sequence[str] | None = None,
    manifests: Sequence[str] = (),
    nested_license_files: Sequence[str] = (),
    api_spdx: str | None = None,
    max_header_files: int = 400,
) -> LicenseFindings:
    """Combine every source of truth into one finding for the repository at ``root``."""
    findings = LicenseFindings()
    entries = list(root_entries) if root_entries is not None else _list_root(root)

    named: list[tuple[str, str]] = []  # (relative path, spdx) for files whose *name* declares one
    alternatives = True  # every named file used the `LICENSE-<name>` form: a choice, not a split
    split_mentions: list[str] = []
    for rel, declared in _license_candidates(root, entries, findings):
        path = root / rel
        text = read_text(path, limit=200_000)
        spdx, confidence = detect_from_text(text)
        note = ""
        if spdx is None and declared:
            spdx, note, confidence = declared, "from the file name", 0.6
        findings.license_files.append(rel)
        findings.evidence.append(Evidence("file", rel, spdx, confidence, note))
        if spdx and declared:
            named.append((rel, spdx))
            alternatives = alternatives and bool(_NAMED_LICENSE_RE.match(PurePosixPath(rel).name))
        if is_split_license_text(text):
            for mention in licenses_mentioned(text):
                if mention not in split_mentions:
                    split_mentions.append(mention)

    primary = max(
        (e for e in findings.evidence if e.source == "file" and e.spdx),
        key=lambda e: e.confidence,
        default=None,
    )

    for rel in manifests:
        path = root / rel
        if not path.is_file():
            continue
        spdx, note = manifest_license(path.name, read_text(path, limit=500_000))
        if spdx:
            findings.evidence.append(Evidence("manifest", rel, spdx, 0.7, note))

    if api_spdx and api_spdx.upper() != "NOASSERTION":
        findings.evidence.append(Evidence("api", "repos/{owner}/{repo}", normalize(api_spdx), 0.9))

    if primary is not None:
        findings.spdx, findings.confidence = primary.spdx, primary.confidence
        findings.resolution = "single"
        distinct = sorted({spdx for _, spdx in named})
        findings.candidates = distinct
        if len(distinct) > 1:
            # `LICENSE-MIT` next to `LICENSE-APACHE` offers a choice. `cc-by-4.0.LICENSE` next to
            # `apache-2.0.LICENSE` does not: those cover different parts of the repository, and
            # picking the nicer one is how a documentation license gets ignored.
            docs = any(classify(spdx).value.startswith("docs") for spdx in distinct)
            if alternatives and not docs:
                findings.resolution = "dual"
                findings.spdx = " OR ".join(distinct)
                findings.notes.append("several named license files: treated as dual licensing")
            else:
                findings.resolution = "split"
                findings.spdx = " AND ".join(distinct)
                findings.notes.append(
                    "several license files that are not alternatives "
                    f"({', '.join(rel for rel, _ in named)}): every one of them is assumed to "
                    "apply somewhere, so the most restrictive wins"
                )
    else:
        fallback = max(
            (e for e in findings.evidence if e.spdx),
            key=lambda e: e.confidence,
            default=None,
        )
        if fallback is not None:
            findings.spdx, findings.confidence = fallback.spdx, fallback.confidence
            findings.resolution = "single"
            findings.notes.append(f"no license text found; using the {fallback.source} declaration")
        elif not findings.license_files:
            findings.notes.append("no license file, manifest declaration or API license")

    for item in findings.evidence:
        if item.spdx and findings.spdx and item.spdx != findings.spdx:
            if " OR " in findings.spdx and item.spdx in findings.spdx.split(" OR "):
                continue
            if _same_family(item.spdx, findings.spdx) and item.source == "manifest":
                findings.notes.append(
                    f"{item.path} declares {item.spdx}, text says {findings.spdx}"
                )
                continue
            findings.conflicts.append(f"{item.source} {item.path} says {item.spdx}")

    _scan_headers(root, code_files, findings, max_header_files)

    for rel in nested_license_files:
        path = root / rel
        if not path.is_file():
            continue
        spdx, confidence = detect_from_text(read_text(path, limit=200_000))
        if spdx and spdx != findings.spdx:
            findings.exceptions.append(
                {"path": rel, "spdx": spdx, "confidence": confidence, "kind": "nested-license"}
            )

    _resolve_unclear(findings, split_mentions)

    cls = classify(findings.spdx)
    findings.human_review = bool(findings.conflicts) or cls in (
        LicenseClass.NONE,
        LicenseClass.UNKNOWN,
        LicenseClass.SOURCE_AVAILABLE,
    )
    if findings.spdx and findings.spdx.startswith(("GPL-", "LGPL-", "AGPL-")):
        findings.notes.append("'-only' assumed unless headers say 'or later'")
    return findings


def _list_root(root: Path) -> list[str]:
    try:
        return [entry.name for entry in root.iterdir()]
    except OSError:
        return []


def _scan_headers(
    root: Path, code_files: Sequence[str], findings: LicenseFindings, limit: int
) -> None:
    sample = list(code_files)
    if len(sample) > limit:
        step = len(sample) / limit
        sample = [sample[int(i * step)] for i in range(limit)]
    counts: dict[str, int] = {}
    exceptions = 0
    for rel in sample:
        text = read_text(root / rel, limit=4000)
        ident = find_spdx_identifier(text)
        findings.headers_scanned += 1
        if not ident:
            continue
        counts[ident] = counts.get(ident, 0) + 1
        if findings.spdx and not _same_family(ident, findings.spdx) and exceptions < 50:
            findings.exceptions.append({"path": rel, "spdx": ident, "kind": "header"})
            exceptions += 1
    findings.header_counts = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    if exceptions:
        findings.notes.append(f"{exceptions} file headers declare a different license")
