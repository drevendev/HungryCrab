"""Estimated-token pages of existing inert source evidence, never permission.

The full JSON artifact remains authoritative. Pages prioritize actual code
windows, retain exact line bounds and expose continuation for the remaining
fields, including grant/notice data. No file, model, provider or ledger effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

from .artifact_read import MAX_JSON_BYTES
from .errors import CrabError
from .source_inspection import body_excluded
from .tokens import estimate_tokens

SCHEMA = "hungry-crab.source-evidence-page/1"
SOURCE_SCHEMAS = {"hungry-crab.source-packet/1", "hungry-crab.source-index/1"}


def _fragments(text: str, maximum: int) -> Iterator[tuple[int, str]]:
    start = 0
    size = 0
    for index, char in enumerate(text):
        width = len(char.encode("utf-8"))
        if size + width > maximum:
            yield start, text[start:index]
            start, size = index, 0
        size += width
    if start < len(text) or not text:
        yield start, text[start:]


def _leaves(value: object) -> Iterator[tuple[str, object]]:
    pending: list[tuple[str, object]] = [("", value)]
    count = 0
    while pending:
        pointer, node = pending.pop()
        count += 1
        if count > 30000:
            raise CrabError("source evidence exceeds the bounded projection field count")
        if isinstance(node, dict) and node:
            pending.extend(
                (pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"), child)
                for key, child in sorted(node.items(), reverse=True)
            )
        elif isinstance(node, list) and node:
            pending.extend(
                (pointer + "/" + str(i), child) for i, child in reversed(list(enumerate(node)))
            )
        else:
            yield pointer, node


def _source_chunk(
    path: str, sha: str, pointer: str, window: dict[str, Any], group: list[str], start: int
) -> dict[str, Any]:
    return {
        "kind": "source",
        "path": path,
        "sha": sha,
        "pointer": pointer,
        "start": start,
        "end": start + len(group) - 1,
        "original_start": window["start"],
        "original_end": window["end"],
        "node_end": window.get("node_end"),
        "window_truncated": window.get("range_truncated"),
        "content": "".join(group),
    }


def _source_records(payload: dict[str, Any], maximum: int) -> tuple[list[dict[str, Any]], set[str]]:
    records: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for index, file in enumerate(payload.get("files", [])):
        if (
            not isinstance(file, dict)
            or not isinstance(file.get("path"), str)
            or not isinstance(file.get("sha"), str)
            or not isinstance(file.get("symbols", []), list)
            or not all(isinstance(symbol, dict) for symbol in file.get("symbols", []))
            or not isinstance(file.get("ranges", []), list)
        ):
            raise CrabError("source view requires structured source file records")
        path, sha = file["path"], file["sha"]
        if body_excluded(path):
            consumed.add(f"/files/{index}")
            records.append(
                {"kind": "omission", "path": path, "reason": "instruction-readme-body-excluded"}
            )
            continue
        header = {
            key: file[key]
            for key in ("path", "sha", "line_count", "analysis", "symbol_status")
            if key in file
        }
        records.append({"kind": "source-file", "file_index": index, **header})
        for si, declaration in enumerate(file.get("symbols", [])[:4]):
            consumed.add(f"/files/{index}/symbols/{si}")
            records.append(
                {
                    "kind": "navigation",
                    "path": path,
                    "sha": sha,
                    "declaration": declaration,
                    "body_supplied": False,
                }
            )
        for ri, window in enumerate(file.get("ranges", [])):
            if (
                not isinstance(window, dict)
                or not isinstance(window.get("content"), str)
                or "start" not in window
                or "end" not in window
            ):
                raise CrabError("source view requires structured source ranges")
            pointer = f"/files/{index}/ranges/{ri}/content"
            consumed.add(pointer)
            content = window["content"]
            start, stop = window["start"], window["end"]
            if (
                type(start) is not int
                or type(stop) is not int
                or not 1 <= start <= stop
                or stop - start > 1000
            ):
                records.append(
                    {"kind": "omission", "pointer": pointer, "reason": "invalid-source-range"}
                )
                continue
            lines = content.splitlines(keepends=True)
            if not lines and content == "" and start == stop:
                lines = [""]
            expected = stop - start + 1
            if len(lines) == expected - 1 and content.endswith(("\n", "\r")):
                lines.append("")
            if len(lines) != expected:
                records.append(
                    {
                        "kind": "omission",
                        "pointer": pointer,
                        "reason": "source-range-content-mismatch",
                    }
                )
                continue
            group: list[str] = []
            group_start = int(window["start"])
            size = 0

            for offset, line in enumerate(lines):
                line_size = len(line.encode("utf-8"))
                if size + line_size > maximum:
                    if group:
                        records.append(
                            _source_chunk(path, sha, pointer, window, group, group_start)
                        )
                    group = []
                    size = 0
                    group_start = int(window["start"]) + offset
                if line_size > maximum:
                    records.append(
                        {
                            "kind": "omission",
                            "pointer": pointer,
                            "line": int(window["start"]) + offset,
                            "reason": "source-line-page-budget",
                            "bytes": line_size,
                        }
                    )
                    group_start += 1
                    continue
                group.append(line)
                size += line_size
            if group:
                records.append(_source_chunk(path, sha, pointer, window, group, group_start))
    return records, consumed


def evidence_page(
    payload: dict[str, Any], *, budget: int = 1800, cursor: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Read one bounded view; a cursor binds the exact payload and estimate policy."""
    if type(budget) is not int or not 512 <= budget <= 3500:
        raise CrabError("source evidence view budget must be in 512..3500 estimated tokens")
    if payload.get("schema") not in SOURCE_SCHEMAS or not isinstance(payload.get("files"), list):
        raise CrabError("source view requires a recognized inert source packet or index")
    try:
        canonical = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise CrabError("source view requires bounded JSON data") from exc
    if len(canonical) > MAX_JSON_BYTES:
        raise CrabError("source evidence exceeds its JSON byte budget")
    identity = hashlib.sha256(canonical).hexdigest()
    records, consumed = _source_records(payload, budget)
    for pointer, value in _leaves(payload):
        if any(pointer == item or pointer.startswith(item + "/") for item in consumed):
            continue
        if isinstance(value, str):
            records.extend(
                {
                    "kind": "field",
                    "pointer": pointer,
                    "offset": offset,
                    "offset_unit": "unicode-codepoints",
                    "value_length": len(value),
                    "value": fragment,
                }
                for offset, fragment in _fragments(value, budget)
            )
        else:
            records.append({"kind": "field", "pointer": pointer, "value": value})
    if len(records) > 30000:
        raise CrabError("source evidence exceeds its projection record budget")
    offset = 0
    if cursor is not None:
        if (
            not isinstance(cursor, dict)
            or cursor.get("schema") != SCHEMA
            or cursor.get("payload_sha256") != identity
            or type(cursor.get("budget")) is not int
            or cursor.get("budget") != budget
            or type(cursor.get("offset")) is not int
            or not 0 <= cursor["offset"] <= len(records)
        ):
            raise CrabError("source evidence cursor differs from the payload or budget")
        offset = cursor["offset"]
    page: dict[str, Any] = {
        "schema": SCHEMA,
        "untrusted_data": True,
        "payload_schema": payload["schema"],
        "payload_sha256": identity,
        "payload_hash_encoding": "sorted-key compact UTF-8 JSON",
        "raw_artifact_required": True,
        "semantic_review": "not-established",
        "permission_from_view": False,
        "budget_estimate": budget,
        "offset": offset,
        "this_page_has_omissions": False,
        "record_count": len(records),
        "records": [],
        "next_cursor": None,
        "end_of_projection": False,
        "estimated_tokens": 0,
    }

    def finish(end: int) -> dict[str, Any]:
        page["this_page_has_omissions"] = any(row["kind"] == "omission" for row in page["records"])
        page["end_of_projection"] = end >= len(records)
        page["next_cursor"] = (
            None
            if end >= len(records)
            else {"schema": SCHEMA, "payload_sha256": identity, "budget": budget, "offset": end}
        )
        for _ in range(3):
            page["estimated_tokens"] = estimate_tokens(
                json.dumps(page, ensure_ascii=False, indent=2)
            )
        return page

    end = offset
    while end < len(records):
        candidate = records[end]
        page["records"].append(candidate)
        finish(end + 1)
        if page["estimated_tokens"] > budget:
            page["records"].pop()
            if page["records"]:
                break
            page["records"].append(
                {
                    "kind": "omission",
                    "record_index": end,
                    "reason": "single-record-page-budget",
                    "raw_artifact_required": True,
                }
            )
        end += 1
    finish(end)
    return page
