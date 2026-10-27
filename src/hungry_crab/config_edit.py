"""Narrow, comment-preserving writes to a maw-owned YAML section."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from .errors import UsageError


def _member_lines(node: yaml.Node, prefix: tuple[str, ...] = ()) -> dict[int, tuple[str, ...]]:
    result: dict[int, tuple[str, ...]] = {}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            path = (*prefix, key.value)
            result[key.start_mark.line] = path
            result.update(_member_lines(value, path))
    return result


def _comments(text: str) -> dict[int, str]:
    """Find comments outside YAML scalars, including quoted strings containing '#'."""
    scalars = [token for token in yaml.scan(text) if isinstance(token, yaml.tokens.ScalarToken)]
    result = {}
    offset = 0
    for number, line in enumerate(text.splitlines(keepends=True)):
        for column, character in enumerate(line):
            if character == "#" and not any(
                token.start_mark.index <= offset + column < token.end_mark.index
                for token in scalars
            ):
                result[number] = line[column:].strip()
                break
        offset += len(line)
    return result


def replace_section(text: str, name: str, value: dict[str, Any]) -> str:
    try:
        node = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise UsageError("configuration rewrite needs one valid YAML document") from exc
    if node is None:
        return (
            text
            + ("" if not text or text.endswith("\n") else "\n")
            + yaml.safe_dump({name: value}, sort_keys=False)
        )
    if not isinstance(node, yaml.MappingNode) or node.flow_style:
        raise UsageError("tuning needs a block-style .crab.yml mapping")
    if any(key.start_mark.column for key, _ in node.value) or any(
        isinstance(token, yaml.tokens.DocumentEndToken) for token in yaml.scan(text)
    ):
        raise UsageError(
            "tuning cannot rewrite indented roots or document-end markers",
            hint="edit scoring manually",
        )
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
    if isinstance(val, yaml.MappingNode) and val.flow_style and val.value:
        raise UsageError(
            "tuning cannot preserve a nonempty flow-style section", hint="edit scoring manually"
        )
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
    comments = _comments(text)
    old_members = _member_lines(node)
    rendered = yaml.compose(block)
    assert rendered is not None
    new_members = {path: line for line, path in _member_lines(rendered).items()}
    block_lines = block.splitlines(keepends=True)
    comment_lines = []
    for number in range(start, end):
        comment = comments.get(number)
        if comment is None:
            continue
        if lines[number].lstrip().startswith("#"):
            comment_lines.append(lines[number])
            continue
        member = old_members.get(number)
        destination = new_members.get(member) if member is not None else None
        if destination is None:
            comment_lines.append(comment + newline)
        else:
            # Keep the historical header spelling, and preserve child comments verbatim.
            if number == start and val.end_mark.line != start:
                comment = "#" + comment[1:].strip()
            block_lines[destination] = (
                block_lines[destination].rstrip("\r\n") + "  " + comment + newline
            )
    block = "".join(block_lines)
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
