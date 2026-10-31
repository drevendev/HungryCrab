"""Read a documented bounded classification-only subset of Git attributes."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from .base import FileInfo


def classify_attributes(text: str, files: list[FileInfo]) -> list[dict[str, Any]]:
    """Root attributes, last matching assignment wins; never invoke a Git filter."""
    omitted: list[dict[str, Any]] = []
    baseline = {info.path: (info.generated, info.vendored) for info in files}
    for number, line in enumerate(text.splitlines()[:2000], 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        pattern = fields[0]
        if (
            any(char in pattern for char in ('"', "\\", "[", "]", "!"))
            or pattern.endswith("/")
            or len(pattern) > 200
            or ("**" in pattern and (not pattern.endswith("/**") or "**" in pattern[:-3]))
        ):
            omitted.append({"line": number, "reason": "unsupported-pattern"})
            continue
        assignments: dict[str, bool | None] = {}
        for token in fields[1:]:
            name = token.lstrip("-!").split("=", 1)[0]
            if name not in {"linguist-generated", "linguist-vendored"}:
                continue
            value: bool | None
            if token == "!" + name:
                value = None
            elif token in {name, name + "=true"}:
                value = True
            elif token in {"-" + name, name + "=false"}:
                value = False
            else:
                omitted.append({"line": number, "reason": "unsupported-attribute-value"})
                continue
            assignments[name] = value
        for info in files:
            subject = info.path.split("/")
            components = pattern.lstrip("/").split("/")
            if "/" not in pattern:
                matched = fnmatchcase(info.name, pattern)
            elif components[-1] == "**":
                matched = len(subject) >= len(components) and all(
                    fnmatchcase(value, token)
                    for value, token in zip(subject, components[:-1], strict=False)
                )
            else:
                matched = len(subject) == len(components) and all(
                    fnmatchcase(value, token)
                    for value, token in zip(subject, components, strict=True)
                )
            if not matched:
                continue
            for name, value in assignments.items():
                attribute = "generated" if name == "linguist-generated" else "vendored"
                original = baseline[info.path][0 if attribute == "generated" else 1]
                # Declarations classify files; they never waive the existing
                # vendored/generated safety exclusions.
                setattr(info, attribute, original or bool(value))
                info.classification_origin[attribute] = f".gitattributes:{number}"
                info.classification_declared[attribute] = value
    if len(text.splitlines()) > 2000:
        omitted.append({"reason": "attribute-line-limit"})
    return omitted[:100]
