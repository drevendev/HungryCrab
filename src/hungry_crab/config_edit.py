"""Narrow, comment-preserving writes to a maw-owned YAML section."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from .errors import UsageError


def replace_section(text: str, name: str, value: dict[str, Any]) -> str:
    node = yaml.compose(text)
    if node is None:
        return (
            text
            + ("" if not text or text.endswith("\n") else "\n")
            + yaml.safe_dump({name: value}, sort_keys=False)
        )
    if not isinstance(node, yaml.MappingNode) or node.flow_style:
        raise UsageError("tuning needs a block-style .crab.yml mapping")
    if any(
        isinstance(t, yaml.tokens.AnchorToken | yaml.tokens.AliasToken) for t in yaml.scan(text)
    ):
        raise UsageError(
            "tuning cannot rewrite YAML anchors or aliases", hint="edit scoring manually"
        )
    newline = "\r\n" if "\r\n" in text else "\n"
    block = yaml.safe_dump({name: value}, sort_keys=False, allow_unicode=True).replace(
        "\n", newline
    )
    pairs = [(key, val) for key, val in node.value if key.value == name]
    if len(pairs) > 1:
        raise UsageError(f"duplicate .crab.yml key {name!r}")
    if not pairs:
        return text + ("" if text.endswith(("\n", "\r")) else newline) + block
    key, val = pairs[0]
    lines = text.splitlines(keepends=True)
    start = key.start_mark.line
    last = val
    while (
        isinstance(last, yaml.MappingNode | yaml.SequenceNode)
        and last.value
        and not last.flow_style
    ):
        last = last.value[-1][1] if isinstance(last, yaml.MappingNode) else last.value[-1]
    end = last.end_mark.line + (1 if last.end_mark.column else 0)
    # Preserve comments, including those attached to the old inline mapping.
    comment_lines = [line for line in lines[start + 1 : end] if line.lstrip().startswith("#")]
    if val.end_mark.line == start:
        tail = lines[start][val.end_mark.column :].strip()
        if tail.startswith("#"):
            block = block.replace(newline, "  " + tail + newline, 1)
    elif "#" in lines[start][key.end_mark.column :]:
        tail = "#" + lines[start][key.end_mark.column :].split("#", 1)[1].strip()
        block = block.replace(newline, "  " + tail + newline, 1)
    return "".join(lines[:start]) + block + "".join(comment_lines) + "".join(lines[end:])


def atomic_text(path: Path, text: str) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise UsageError(f"configuration write must not traverse a symlink: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
