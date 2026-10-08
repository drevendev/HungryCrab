"""Read JUnit data from bounded in-memory archives, never extract or execute an artifact."""

from __future__ import annotations

import io
import lzma
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Any

ZIP_LIMIT = 8 * 1024 * 1024
XML_LIMIT = 1024 * 1024


def parse_junit_archive(body: bytes) -> dict[str, Any]:
    if len(body) > ZIP_LIMIT:
        return {"status": "size-limit", "tests": [], "reports": 0}
    tests: list[dict[str, Any]] = []
    reports = 0
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(e.file_size for e in entries) > ZIP_LIMIT:
                return {"status": "size-limit", "tests": [], "reports": 0}
            for entry in entries:
                if not entry.filename.lower().endswith(".xml") or entry.is_dir():
                    continue
                if entry.file_size > XML_LIMIT or entry.flag_bits & 1:
                    return {"status": "size-limit", "tests": [], "reports": 0}
                source = archive.read(entry)
                markup = source.replace(b"\0", b"").upper()
                if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
                    return {"status": "unsafe-xml", "tests": [], "reports": 0}
                root = ET.fromstring(source)
                if root.tag not in {"testsuites", "testsuite"}:
                    continue
                reports += 1
                for case in root.iter("testcase"):
                    if len(tests) >= 20_000:
                        return {"status": "case-limit", "tests": [], "reports": reports}
                    failed = case.find("failure") is not None or case.find("error") is not None
                    # Surefire's explicit flaky markers record a failed attempt followed by
                    # success. A run/job retry alone cannot provide this test-level evidence.
                    flaky = (
                        case.find("flakyFailure") is not None or case.find("flakyError") is not None
                    ) and not failed
                    tests.append(
                        {
                            "name": case.get("name", "")[:200],
                            "class": case.get("classname", "")[:200],
                            "failed": failed,
                            "flaky_rerun": flaky,
                        }
                    )
    except (
        ValueError,
        OSError,
        zipfile.BadZipFile,
        ET.ParseError,
        RuntimeError,
        zlib.error,
        lzma.LZMAError,
    ):
        return {"status": "invalid-report", "tests": [], "reports": 0}
    return {"status": "available", "tests": tests, "reports": reports}
