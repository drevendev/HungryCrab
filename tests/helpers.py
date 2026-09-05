"""Small helpers shared by the tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hungry_crab.digest import DigestResult


def read_json(result: DigestResult, name: str) -> dict[str, Any]:
    data = json.loads((result.out_dir / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def read_md(result: DigestResult, name: str) -> str:
    return (result.out_dir / name).read_text(encoding="utf-8")


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Create files from a {relative path: content} mapping and return ``root``."""
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return root
