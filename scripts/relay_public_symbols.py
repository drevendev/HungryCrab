"""Read public Microsoft symbol metadata for two recovered historical module identities.

This Actions-only diagnostic accepts no dump, process, arbitrary URL or executable input.
The resulting public records aid address attribution; they do not unwind a call stack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

SYMBOLS = (
    ("ntdll.pdb", "44294CBD55E8CD67B3D56B689F03152D1"),
    ("ws2_32.pdb", "9DC73321ED08893007DF02E641F44B871"),
)
MAX_PDB_BYTES = 64 * 1024 * 1024
MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00\x00"
# Fixed public module-relative observations, extracted without private stack bytes.
OBSERVED_RVAS = {
    "ntdll.pdb": (0x16A4FA,),
    "ws2_32.pdb": (0x2566A, 0x1FCCD, 0x21DC8),
}


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("truncated PDB metadata")
    return int.from_bytes(data[offset : offset + 4], "little")


def dbi_age(data: bytes) -> int:
    """Read only the fixed DBI header through a bounded MSF7 stream directory."""
    if len(data) > MAX_PDB_BYTES or data[:32] != MSF_MAGIC:
        raise ValueError("unsupported PDB container")
    block_size = _u32(data, 32)
    block_count = _u32(data, 40)
    directory_size = _u32(data, 44)
    block_map = _u32(data, 52)
    if block_size not in (512, 1024, 2048, 4096) or block_count * block_size != len(data):
        raise ValueError("invalid MSF block layout")
    if not 20 <= directory_size <= min(len(data), 1024 * 1024) or directory_size % 4:
        raise ValueError("invalid MSF directory size")
    directory_blocks = (directory_size + block_size - 1) // block_size
    if block_map >= block_count or directory_blocks * 4 > block_size:
        raise ValueError("unsupported MSF directory block map")

    def block(index: int) -> bytes:
        if index >= block_count:
            raise ValueError("MSF block outside the PDB")
        return data[index * block_size : (index + 1) * block_size]

    mapping = block(block_map)
    directory = b"".join(block(_u32(mapping, index * 4)) for index in range(directory_blocks))[
        :directory_size
    ]
    stream_count = _u32(directory, 0)
    if not 4 <= stream_count <= 4096 or 4 + stream_count * 4 > len(directory):
        raise ValueError("invalid MSF stream directory")
    position = 4 + stream_count * 4
    for index in range(4):
        size = _u32(directory, 4 + index * 4)
        if size == 0xFFFFFFFF:
            if index == 3:
                raise ValueError("DBI stream absent")
            continue
        if size > len(data):
            raise ValueError("invalid MSF stream size")
        pages = (size + block_size - 1) // block_size
        if position + pages * 4 > len(directory):
            raise ValueError("truncated MSF stream block list")
        if index == 3:
            if size < 64:
                raise ValueError("truncated DBI header")
            for page in range(pages):
                if _u32(directory, position + page * 4) >= block_count:
                    raise ValueError("DBI block outside the PDB")
            header = block(_u32(directory, position))[:64]
            if _u32(header, 0) != 0xFFFFFFFF or _u32(header, 4) not in (19990903, 20091201):
                raise ValueError("unsupported DBI header")
            return _u32(header, 8)
        position += pages * 4
    raise ValueError("DBI stream unavailable")


def classify_identity(
    summary: str, expected: str, *, actual_dbi_age: int | None = None
) -> dict[str, object]:
    """Apply OpenValidate4's image binding rule; absent/legacy DBI stays held."""
    guid = re.search(r"(?mi)^\s*GUID:\s*\{([0-9a-f-]{36})\}\s*$", summary)
    age = re.search(r"(?m)^\s*Age:\s*([0-9]+)\s*$", summary)
    actual_guid = None if guid is None else guid[1].replace("-", "").upper()
    actual_age = None if age is None else int(age[1])
    expected_age = int(expected[32:], 16)
    if actual_guid is None or actual_age is None:
        status = "IDENTITY_UNAVAILABLE_HELD"
    elif actual_guid != expected[:32].upper() or actual_age < expected_age:
        status = "IDENTITY_MISMATCH_HELD"
    elif actual_dbi_age is None or actual_dbi_age == 0:
        status = "IDENTITY_UNAVAILABLE_HELD"
    elif actual_dbi_age != expected_age:
        status = "IDENTITY_MISMATCH_HELD"
    else:
        status = "MATCHED"
    return {
        "identity_status": status,
        "actual_guid": actual_guid,
        "actual_age": actual_age,
        "actual_dbi_age": actual_dbi_age,
        "expected_age": expected_age,
        "match_rule": "exact GUID; PDB Info age >= image age; nonzero DBI age == image age",
    }


def public_address_labels(text: str, rvas: tuple[int, ...]) -> list[dict[str, object]]:
    """Label nearest public entries within an image section, never infer stack frames."""
    sections: dict[int, tuple[int, int]] = {}
    for match in re.finditer(
        r"(?ms)^\s*SECTION HEADER #(\d+)\s*\n.*?"
        r"^\s*([0-9A-Fa-f]+) virtual size\s*\n"
        r"^\s*([0-9A-Fa-f]+) virtual address\s*$",
        text,
    ):
        sections[int(match[1])] = (int(match[3], 16), int(match[2], 16))
    entries: dict[int, list[tuple[int, str]]] = {}
    for match in re.finditer(
        r"(?m)^.*S_PUB32[^\r\n]*`([^`\r\n]+)`\r?\n"
        r"\s*flags = function, addr = ([0-9]+):([0-9]+)\s*$",
        text,
    ):
        section = int(match[2])
        # LLVM prints these segment/offset pairs in decimal, unlike section addresses.
        entries.setdefault(section, []).append((int(match[3]), match[1]))
    labels: list[dict[str, object]] = []
    for rva in rvas:
        candidates = [
            (base + offset, name)
            for section, (base, size) in sections.items()
            if base <= rva < base + size
            for offset, name in entries.get(section, [])
            if 0 <= offset < size and base + offset <= rva
        ]
        if not candidates:
            labels.append({"rva": f"0x{rva:X}", "status": "PUBLIC_ENTRY_UNAVAILABLE"})
            continue
        nearest = max(address for address, _ in candidates)
        labels.append(
            {
                "rva": f"0x{rva:X}",
                "status": "NEAREST_PUBLIC_ENTRY_ONLY",
                "entry_rva": f"0x{nearest:X}",
                "offset": rva - nearest,
                "names": sorted({name for address, name in candidates if address == nearest}),
            }
        )
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    destination = parser.parse_args().out
    destination.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, object]] = []
    manifest: dict[str, object] = {
        "schema": 2,
        "status": "CAPTURE_IN_PROGRESS",
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "workflow_sha": os.environ.get("GITHUB_SHA"),
        "records": records,
        "interpretation": "Public symbol records only; no unwound stack or causal finding",
    }

    def save() -> None:
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    save()
    try:
        tool = next(
            (
                path
                for name in ("llvm-pdbutil", *(f"llvm-pdbutil-{n}" for n in range(22, 15, -1)))
                if (path := shutil.which(name))
            ),
            None,
        )
        if tool is None:
            raise RuntimeError("runner has no supported llvm-pdbutil; symbol capture unavailable")
        manifest["tool"] = subprocess.run(
            [tool, "--version"], capture_output=True, check=True, timeout=10
        ).stdout.decode("utf-8", errors="replace")
        for filename, identity in SYMBOLS:
            url = f"https://msdl.microsoft.com/download/symbols/{filename}/{identity}/{filename}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(MAX_PDB_BYTES + 1)
            if len(data) > MAX_PDB_BYTES:
                raise ValueError("public PDB exceeds the diagnostic byte limit")
            pdb = destination / filename
            pdb.write_bytes(data)
            result = subprocess.run(
                [tool, "dump", "--summary", "--section-headers", "--publics", str(pdb)],
                capture_output=True,
                check=True,
                timeout=120,
            )
            (destination / f"{filename}.txt").write_bytes(result.stdout)
            (destination / f"{filename}.stderr.txt").write_bytes(result.stderr)
            dbi_error: str | None = None
            actual_dbi_age: int | None
            try:
                actual_dbi_age = dbi_age(data)
            except ValueError as error:
                actual_dbi_age = None
                dbi_error = str(error)
            summary = result.stdout.decode("utf-8", errors="replace")
            identity_result = classify_identity(summary, identity, actual_dbi_age=actual_dbi_age)
            records.append(
                {
                    "pdb": filename,
                    "expected_symbol_id": identity,
                    "source": url,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "dbi_read_error": dbi_error,
                    **identity_result,
                    "address_labels": (
                        public_address_labels(summary, OBSERVED_RVAS[filename])
                        if identity_result["identity_status"] == "MATCHED"
                        else []
                    ),
                }
            )
            save()
        manifest["status"] = (
            "PUBLIC_RECORDS_CAPTURED_WITH_IDENTITY_HOLDS"
            if any(record["identity_status"] != "MATCHED" for record in records)
            else "PUBLIC_RECORDS_CAPTURED"
        )
    except Exception as error:
        manifest["status"] = "FAILED"
        manifest["error"] = str(error)
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
