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
    if isinstance(val, yaml.MappingNode) and not val.flow_style:
        block = _restore_child_inline_comments(val, lines, block, name, newline)
    return "".join(lines[:start]) + block + "".join(comment_lines) + "".join(lines[end:])


def _restore_child_inline_comments(
    old_value: yaml.MappingNode, old_lines: list[str], block: str, section: str, newline: str
) -> str:
    """Keep owner notes beside surviving scalar child keys, including nested keys."""
    comments: dict[tuple[str, ...], str] = {}

    def collect(mapping: yaml.MappingNode, path: tuple[str, ...]) -> None:
        for key, child in mapping.value:
            if not isinstance(key, yaml.ScalarNode):
                continue
            child_path = (*path, key.value)
            if isinstance(child, yaml.ScalarNode) and child.end_mark.line == key.start_mark.line:
                suffix = old_lines[key.start_mark.line][child.end_mark.column :].strip()
                if suffix.startswith("#"):
                    comments[child_path] = suffix
            elif isinstance(child, yaml.MappingNode) and not child.flow_style:
                suffix = old_lines[key.start_mark.line][key.end_mark.column :]
                before, marker, after = suffix.partition("#")
                if marker and before.strip() == ":":
                    comments[child_path] = "#" + after.rstrip("\r\n")
                collect(child, child_path)

    collect(old_value, ())
    if not comments:
        return block
    replacement = yaml.compose(block)
    if not isinstance(replacement, yaml.MappingNode):
        return block
    section_values = [value for key, value in replacement.value if key.value == section]
    if len(section_values) != 1 or not isinstance(section_values[0], yaml.MappingNode):
        return block
    positions: dict[tuple[str, ...], int] = {}

    def locate(mapping: yaml.MappingNode, path: tuple[str, ...]) -> None:
        for key, child in mapping.value:
            if not isinstance(key, yaml.ScalarNode):
                continue
            child_path = (*path, key.value)
            if isinstance(child, yaml.ScalarNode) and child.end_mark.line == key.start_mark.line:
                positions[child_path] = key.start_mark.line
            elif isinstance(child, yaml.MappingNode) and not child.flow_style:
                positions[child_path] = key.start_mark.line
                locate(child, child_path)

    locate(section_values[0], ())
    lines = block.splitlines(keepends=True)
    for path, comment in comments.items():
        line_index = positions.get(path)
        if line_index is not None:
            lines[line_index] = lines[line_index].rstrip("\r\n") + "  " + comment + newline
    return "".join(lines)


def verified_replace_section(
    text: str, name: str, value: dict[str, Any], expected: dict[str, Any]
) -> str:
    """Refuse a layout that would change YAML policy or fail to parse after replacement."""
    updated = replace_section(text, name, value)
    try:
        actual = yaml.safe_load(updated)
    except yaml.YAMLError as exc:
        raise UsageError(
            f"cannot safely rewrite YAML section {name!r}", hint="edit scoring manually"
        ) from exc
    if actual != expected:
        raise UsageError(
            f"cannot safely rewrite YAML section {name!r}", hint="edit scoring manually"
        )
    return updated


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
