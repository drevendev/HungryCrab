"""Maw policy narrows the legal verdict; it can never grant additional permissions."""

from __future__ import annotations

from pathlib import PurePosixPath

from .matrix import Mode

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".rs",
    ".go",
    ".cs",
    ".java",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".kt",
}


def nutrient_material(category: str, paths: list[str]) -> str:
    """Configs/templates are the explicit exceptions. Unknown categories fail closed."""
    if category in {"code", "architecture", "history-lesson"}:
        return "code"
    if category == "issue-lesson":
        return "idea"
    if (
        category in {"ci", "tooling", "tests", "docs", "ai-config"}
        and paths
        and all(
            ".config." in PurePosixPath(path).name
            or PurePosixPath(path).suffix.lower() not in CODE_EXTENSIONS
            for path in paths
        )
    ):
        return "template" if category == "docs" else "configuration"
    if any(PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS for path in paths):
        return "code"
    if category in {"ci", "tooling", "ai-config", "hygiene", "security", "deps", "tests"}:
        return "configuration"
    if category == "docs":
        return "template"
    return "code"


def apply_maw_policy(mode: str, *, policy: str, material: str) -> tuple[str, str]:
    if (
        policy == "strict"
        and mode == Mode.COPY.value
        and material not in {"configuration", "template"}
    ):
        return (
            Mode.REIMPLEMENT.value,
            "strict maw policy: COPY code requires clean-room REIMPLEMENT",
        )
    return mode, ""
