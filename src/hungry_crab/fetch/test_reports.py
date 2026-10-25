"""Read JUnit data from bounded in-memory archives, never extract or execute an artifact.

The sizes an archive declares are the archive's own claims. Each member is inflated in bounded
chunks and counted as it arrives, so the limits bound the bytes actually produced; LZMA and
bzip2 members are refused, because CPython inflates them without any output bound.
"""

from __future__ import annotations

import io
import lzma
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Any

try:
    from compression.zstd import ZstdError  # type: ignore[import-not-found,unused-ignore]
except ImportError:  # Python before 3.14 has no standard-library Zstandard reader.

    class ZstdError(Exception):  # type: ignore[no-redef,unused-ignore]
        """Unavailable decompressor; never enables another compression method."""


ZIP_LIMIT = 8 * 1024 * 1024
XML_LIMIT = 1024 * 1024
CHUNK = 64 * 1024
# Actions artifacts are deflate; stored is what a zip writer uses for data that will not shrink.
COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class _SizeLimitError(Exception):
    """A member produced more bytes than its share of the limits."""


def _inflate(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, limit: int) -> bytes:
    """Up to ``limit`` bytes of one member; a deflate read never inflates much past a chunk."""
    chunks: list[bytes] = []
    total = 0
    with archive.open(entry) as stream:
        while chunk := stream.read(CHUNK):
            total += len(chunk)
            if total > limit:
                raise _SizeLimitError
            chunks.append(chunk)
    return b"".join(chunks)


def parse_junit_archive(body: bytes) -> dict[str, Any]:
    if len(body) > ZIP_LIMIT:
        return {"status": "size-limit", "tests": [], "reports": 0}
    tests: list[dict[str, Any]] = []
    reports = 0
    inflated = 0
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
                if entry.compress_type not in COMPRESSION_METHODS:
                    return {"status": "unsupported-compression", "tests": [], "reports": 0}
                source = _inflate(archive, entry, min(XML_LIMIT, ZIP_LIMIT - inflated))
                inflated += len(source)
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
    except (_SizeLimitError, MemoryError):
        return {"status": "size-limit", "tests": [], "reports": 0}
    except (
        ValueError,
        OSError,
        zipfile.BadZipFile,
        ET.ParseError,
        RuntimeError,
        zlib.error,
        lzma.LZMAError,
        ZstdError,
    ):
        return {"status": "invalid-report", "tests": [], "reports": 0}
    return {"status": "available", "tests": tests, "reports": reports}
