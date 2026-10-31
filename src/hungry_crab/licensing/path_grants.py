"""Immutable, bounded applicable-grant resolution shared by analysis and publication."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from .detect import (
    _copyright_metadata,
    combined_root_grant,
    detect_from_text,
    find_source_header,
    is_license_file_name,
    license_name_from_file,
    manifest_license,
)
from .matrix import (
    LicenseClass,
    allows_alternative,
    canonical_expression,
    classify,
    license_terms,
    normalize,
)
from .sections import attribution_reference, composite_sections


class GrantReader(Protocol):
    def read(self, sha: str, path: str) -> str | None: ...

    def entries(self, sha: str, directory: str = "") -> dict[str, str] | None: ...


@dataclass
class PathGrant:
    path: str
    expression: str | None = None
    evidence: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    reason: str = "unresolved source grant"
    material_role: str = "unknown"
    scope: str = "unresolved"
    obligations: list[str] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    declared_expression: str | None = None
    chosen_branch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GrantResolver:
    """Read regular immutable entries only; never fetch a referenced URL."""

    def __init__(self, reader: GrantReader, sha: str, source_url: str | None = None) -> None:
        self.reader, self.sha, self.source_url = reader, sha, source_url
        self._texts: dict[str, str | None] = {}
        self._listings: dict[str, dict[str, str] | None] = {}
        self._remaining = 8 * 1024 * 1024

    def _entries(self, directory: str) -> dict[str, str] | None:
        if directory not in self._listings:
            entries = self.reader.entries(self.sha, directory)
            self._listings[directory] = (
                entries if entries is None or len(entries) <= 10_000 else None
            )
        return self._listings[directory]

    def _read(self, path: str) -> str | None:
        if path not in self._texts:
            if len(self._texts) >= 96 or self._remaining <= 0:
                return None
            parent, _, name = path.rpartition("/")
            entries = self._entries(parent)
            text = (
                self.reader.read(self.sha, path)
                if entries and entries.get(name) in {"100644", "100755"}
                else None
            )
            self._texts[path] = (
                text if text is not None and len(text.encode("utf-8")) <= 200_000 else None
            )
            self._remaining -= len(text.encode("utf-8")) if text is not None else 0
        return self._texts[path]

    def document_grant(self, path: str) -> tuple[str | None, list[str]]:
        text = self._read(path)
        if text is None:
            return None, []
        grant = detect_from_text(text)[0]
        if grant:
            return grant, [path]
        reference = attribution_reference(text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if reference and all(_copyright_metadata(line) for line in lines[:-1]):
            target = PurePosixPath(path).parent / reference
            if target.as_posix() != path and target.name == reference:
                return detect_from_text(self._read(target.as_posix()) or "")[0], [
                    path,
                    target.as_posix(),
                ]
        # A complete short documentation notice may link its own repository's
        # root grant. Interpret the path in THIS commit, not the moving URL.
        if self.source_url and len(text) < 2500:
            links = re.findall(r"\[[^\]\n]{1,100}\]\((https://[^\s)]+)\)", text)
            grant_links = [
                link for link in links if link.startswith(self.source_url.rstrip("/") + "/blob/")
            ]
            candidates: list[tuple[str, str]] = []
            for link in grant_links:
                relative = urlsplit(link).path.split("/blob/", 1)[-1].split("/", 1)
                if (
                    len(relative) == 2
                    and "/" not in relative[1]
                    and is_license_file_name(relative[1])
                ):
                    candidates.append((link, relative[1]))
            if len(candidates) == 1:
                link, grant_target = candidates[0]
                remaining = text.replace(link, "GRANT")
                clean = re.sub(r"^---\n.*?\n---\n", "", remaining, count=1, flags=re.S)
                clean = re.sub(r"^# License\s*", "", clean.lstrip(), count=1)
                # Only a declarative grant paragraph and attribution paragraphs;
                # arbitrary suffix/policy prose cannot be stripped as navigation.
                paragraphs = [p.strip() for p in clean.strip().split("\n\n") if p.strip()]
                grant_paragraph = r"Licensed under the \[Apache License, Version 2\.0\]\(GRANT\)\."
                copyright_paragraph = (
                    r"Copyright \(C\) [0-9,-]{4,30} \[The ORT Project Copyright Holders\]"
                    r"\(https://github\.com/oss-review-toolkit/ort/blob/[^/]+/NOTICE\)\."
                )
                affiliation = (
                    "OSS Review Toolkit (ORT) is a [Linux Foundation project]"
                    "(https://www.linuxfoundation.org) and part of [ACT]"
                    "(https://automatecompliance.org/)."
                )
                if (
                    paragraphs
                    and all(
                        re.fullmatch(copyright_paragraph, p)
                        or re.fullmatch(grant_paragraph, p)
                        or p == affiliation
                        for p in paragraphs
                    )
                    and any(re.fullmatch(grant_paragraph, p) for p in paragraphs)
                ):
                    actual = detect_from_text(self._read(grant_target) or "")[0]
                    if actual == "Apache-2.0":
                        return actual, [path, grant_target]
        return None, [path]

    def _topic_document(self, path: str) -> bool:
        """Recognize a bounded configuration article, never a grant document.

        The conservative repository overview still reports these candidates.
        This narrower path answer does not turn a documentation topic into
        authority over all sibling files merely because its name starts LICENSE.
        """
        name = PurePosixPath(path).name
        if re.fullmatch(r"(?:license|copyright)-[a-z]+(?:-[a-z]+){0,4}\.md", name) is None:
            return False
        text = self._read(path)
        if text is None or not 80 <= len(text) <= 20_000:
            return False
        lines = text.splitlines()
        title = name[:-3].replace("-", " ")
        if not lines or lines[0].casefold() != "# " + title:
            return False
        if detect_from_text(text)[0] is not None or re.search(
            r"permission is hereby|licensed under|redistribution|reproduction|"
            r"commercial use|non.?commercial|prohibited|forbidden|"
            r"(?:may|must|shall)\s+(?:not|only)",
            text,
            re.I,
        ):
            return False
        return "\n## " in text and bool(
            re.search(r"\b(?:configuration|reporters?|scanners?|providers?)\b", text, re.I)
        )

    def resolve(self, path: str, *, selected_branch: str | None = None) -> PathGrant:
        result = PathGrant(path)
        parts = PurePosixPath(path).parts
        if (
            not parts
            or len(parts) > 32
            or path != PurePosixPath(path).as_posix()
            or any(p in {".", ".."} for p in parts)
            or path.startswith("/")
            or "\\" in path
            or ":" in path
            or any(ord(c) < 32 for c in path)
        ):
            result.reason = "unsafe source path"
            return result
        if any(
            part.casefold()
            in {
                "vendor",
                "vendors",
                "vendored",
                "third_party",
                "third-party",
                "thirdparty",
                "node_modules",
                ".venv",
                "venv",
            }
            for part in parts[:-1]
        ):
            result.reason = "unmapped component scope; explicit component review required"
            result.material_role = "component"
            return result
        root = self._entries("")
        if root is None:
            result.reason = "source root unavailable"
            return result
        roots: dict[str, str] = {}
        declared_scope: str | None = None
        composite_labels: list[str] = []
        if (
            sum(is_license_file_name(name) or name.upper().startswith("NOTICE") for name in root)
            > 32
        ):
            result.reason = "root licence/notice file budget exceeded"
            return result
        for name in sorted(root):
            if name.upper().startswith("NOTICE"):
                if self._read(name) is None:
                    result.reason = "required source notice unavailable"
                    return result
                result.notices.append(name)
            if is_license_file_name(name):
                grant, refs = self.document_grant(name)
                result.evidence.extend(refs)
                if grant is None:
                    result.reason = "unresolved operative root grant"
                    return result
                roots[name] = grant
                sections = composite_sections(self._read(name) or "")
                if sections:
                    result.notices.append(name)
                    composite_labels.extend(s.label.casefold() for s in sections[1:])
        if not roots:
            body = self._read(path)
            declared_header = find_source_header(body or "", path=path)
            declarations = [declared_header] if declared_header else []
            for name in (
                "package.json",
                "pyproject.toml",
                "Cargo.toml",
                "composer.json",
                "setup.cfg",
            ):
                if name in root:
                    declaration, _ = manifest_license(name, self._read(name) or "")
                    if declaration:
                        declarations.append(declaration)
            # A file's explicit alternative may narrow a manifest OR, never AND.
            for declaration in declarations:
                if all(
                    canonical_expression(other) == canonical_expression(declaration)
                    or allows_alternative(declaration, other)
                    for other in declarations
                ):
                    declared_scope = declaration
                    break
            terms = license_terms(declared_scope)
            for directory in ("LICENSES", "licenses", "LICENCES", "licences"):
                candidates = self._entries(directory)
                if not candidates or len(candidates) > 100:
                    continue
                for name in sorted(candidates):
                    term = normalize(PurePosixPath(name).stem)
                    if term not in terms:
                        continue
                    scoped = f"{directory}/{name}"
                    actual, refs = self.document_grant(scoped)
                    if actual == term:
                        roots[scoped] = actual
                        result.evidence.extend(refs)
            if not terms or set(roots.values()) != set(terms):
                roots.clear()
        if not roots or len(roots) > 32:
            result.reason = "no bounded established root grant"
            return result
        expression = declared_scope or combined_root_grant(roots)
        body = self._read(path)
        if body is None:
            result.reason = "source is unavailable or not a regular file"
            return result
        header = find_source_header(body, path=path)
        header_text = "\n".join(body.splitlines()[:80]).casefold()
        if composite_labels and (
            any(
                part.casefold()
                in {"vendor", "vendored", "third_party", "third-party", "thirdparty"}
                for part in parts
            )
            or any(
                label in header_text or label in path.casefold().split("/")
                for label in composite_labels
            )
        ):
            result.reason = "unmapped composite component scope"
            result.material_role = "component"
            return result
        implementation = PurePosixPath(path).suffix.casefold() in {
            ".py",
            ".pyi",
            ".rs",
            ".go",
            ".js",
            ".ts",
            ".tsx",
            ".cs",
            ".java",
            ".rb",
        } and not any(
            part.casefold() in {"data", "fixtures", "samples", "licenses", "licences"}
            for part in parts[:-1]
        )
        code_split = False
        # A declared software/data split with an established code grant and an
        # explicit file header can scope a code path without importing specimens.
        if expression and " AND " in expression and header:
            notice = "\n".join(self._read(name) or "" for name in result.notices).casefold()
            if (
                implementation
                and header in roots.values()
                and re.search(r"software is licensed under the apache license version 2\.0", notice)
                and header == "Apache-2.0"
            ):
                expression = header
                code_split = True
        for depth in range(1, len(parts)):
            directory = "/".join(parts[:depth])
            entries = self._entries(directory)
            if entries is None:
                result.reason = "source directory unavailable"
                return result
            for name in sorted(entries):
                scoped = f"{directory}/{name}"
                if name.upper().startswith("NOTICE"):
                    if self._read(scoped) is None:
                        result.reason = "scoped notice unavailable"
                        return result
                    result.notices.append(scoped)
                if is_license_file_name(name):
                    if self._topic_document(scoped):
                        result.references.append(
                            {"path": scoped, "role": "documentation-not-grant"}
                        )
                        continue
                    # A file-specific sidecar covers its named file, not every
                    # sibling. A named grant catalog is not directory authority
                    # when an explicit software/data declaration and file header
                    # already established this implementation's scope.
                    sidecar = name.rsplit(".", 1)[0] if name.lower().endswith(".license") else ""
                    if sidecar and "." in sidecar and sidecar != parts[-1]:
                        result.references.append({"path": scoped, "role": "other-file-sidecar"})
                        continue
                    named = license_name_from_file(name)
                    if code_split and named is not None:
                        named = "BSD-2-Clause" if named.casefold() == "bsd-simplified" else named
                        grant, _ = self.document_grant(scoped)
                        if (
                            classify(named) not in {LicenseClass.UNKNOWN, LicenseClass.NONE}
                            and canonical_expression(grant) == canonical_expression(named)
                        ) or (
                            name.casefold().endswith(".license")
                            and classify(grant)
                            in {LicenseClass.PERMISSIVE, LicenseClass.PERMISSIVE_NOTICE}
                        ):
                            result.references.append(
                                {"path": scoped, "role": "separate-named-grant"}
                            )
                            continue
                    grant, refs = self.document_grant(scoped)
                    result.evidence.extend(refs)
                    if grant is None or canonical_expression(grant) != canonical_expression(
                        expression
                    ):
                        result.reason = "differing or unresolved nested grant: " + scoped
                        return result
                if name in {
                    "package.json",
                    "pyproject.toml",
                    "Cargo.toml",
                    "composer.json",
                    "setup.cfg",
                }:
                    declared, _ = manifest_license(name, self._read(scoped) or "")
                    if declared and canonical_expression(declared) != canonical_expression(
                        expression
                    ):
                        result.reason = "differing package grant: " + scoped
                        return result
        result.declared_expression = expression
        if selected_branch and (
            canonical_expression(selected_branch) != canonical_expression(expression)
            and not allows_alternative(expression, selected_branch)
        ):
            result.reason = "requested branch is not an established OR alternative"
            return result
        choice = selected_branch or header
        if (
            choice
            and canonical_expression(choice) != canonical_expression(expression)
            and allows_alternative(expression, choice)
        ):
            expression = choice
            result.chosen_branch = choice
        if header and canonical_expression(header) != canonical_expression(expression):
            result.reason = "differing source header"
            return result
        if not expression or not license_terms(expression):
            return result
        result.expression = expression
        result.material_role = "implementation" if implementation else "data-or-documentation"
        result.scope = (
            "file-header-and-software-declaration" if code_split else "inherited-equivalent-grants"
        )
        result.obligations = ["retain-applicable-grant-texts", "retain-applicable-notices"]
        result.evidence = list(dict.fromkeys(result.evidence))
        result.notices = list(dict.fromkeys(result.notices))
        result.reason = "verified immutable path grant; retain all applicable notices"
        return result
