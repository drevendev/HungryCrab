"""Small helpers shared by the tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hungry_crab.digest import DigestResult


def unfenced_text(markdown: str) -> str:
    """Text outside Markdown code fences, for checking active publication prose."""
    lines: list[str] = []
    fence = ""
    for line in markdown.splitlines():
        if fence:
            if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = ""
        elif match := re.match(r"^(`{3,}|~{3,})", line):
            fence = match[1]
        else:
            lines.append(line)
    assert not fence, "publication contains an unterminated code fence"
    return "\n".join(lines)


def read_json(result: DigestResult, name: str) -> dict[str, Any]:
    data = json.loads((result.out_dir / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def read_md(result: DigestResult, name: str) -> str:
    return (result.out_dir / name).read_text(encoding="utf-8")


def copy_repo(source: Path, target: Path, *, with_git: bool = False) -> Path:
    """Copy a fixture repository so a test can write into it (ledger, .crab.yml)."""
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if not with_git and rel.parts and rel.parts[0] == ".git":
            continue
        destination = target / rel
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
    return target


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Create files from a {relative path: content} mapping and return ``root``."""
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return root
