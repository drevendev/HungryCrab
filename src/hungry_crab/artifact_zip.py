"""Inert, size-bounded ZIP member integrity check for future A2 admission.

This is an untrusted-byte validator, NOT a provider attestation or rights decision.
Never extract a member, hydrate a prey cache, or treat its result as permission to serve.
A2 must qualify provider/run/artifact identity outside this archive.
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import unicodedata
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from .errors import CrabError

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 256
MAX_RATIO = 200
_ALLOWED_SUFFIXES = frozenset({".json", ".md", ".txt"})
_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_BAD_WINDOWS_STEMS = frozenset({"CON", "PRN", "AUX", "NUL"}) | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
}


@dataclass(frozen=True)
class ExpectedMember:
    """An externally admitted manifest's expected bytes; NOT a trust witness."""

    path: str
    size: int
    sha256: str
    role: str


@dataclass(frozen=True)
class CheckedMember:
    """Only content integrity, deliberately no 'trusted' or 'approved' status."""

    path: str
    size: int
    sha256: str
    role: str


def _refuse(reason: str) -> CrabError:
    return CrabError(f"untrusted archive refused: {reason}")


def _canonical(path: str) -> bool:
    if (
        not path
        or len(path) > 240
        or unicodedata.normalize("NFC", path) != path
        or path.startswith("/")
        or "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        return False
    parts = path.split("/")
    if len(parts) > 16:
        return False
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or part.rstrip(" .") != part
            or ":" in part
            or part.split(".", 1)[0].upper() in _BAD_WINDOWS_STEMS
        ):
            return False
    return PurePosixPath(path).suffix.lower() in _ALLOWED_SUFFIXES


def check_zip_members(
    archive: bytes,
    expected: Sequence[ExpectedMember],
) -> tuple[CheckedMember, ...]:
    """Verify all and only declared inert file bytes, without extracting anything.

    This is preparatory A2 integrity only. Caller must separately qualify native
    provider/run/artifact provenance and source rights before any portable use.
    """
    if not isinstance(archive, bytes) or len(archive) > MAX_ARCHIVE_BYTES:
        raise _refuse("archive byte budget")
    if not 1 <= len(expected) <= MAX_MEMBERS:
        raise _refuse("manifest member count")

    declared: dict[str, ExpectedMember] = {}
    folded: set[str] = set()
    for member in expected:
        if (
            not isinstance(member, ExpectedMember)
            or not isinstance(member.path, str)
            or not _canonical(member.path)
            or not isinstance(member.role, str)
            or not member.role
            or not isinstance(member.size, int)
            or isinstance(member.size, bool)
            or not 0 <= member.size <= MAX_MEMBER_BYTES
            or not isinstance(member.sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", member.sha256) is None
        ):
            raise _refuse("invalid manifest member")
        folded_name = member.path.casefold()
        if folded_name in folded:
            raise _refuse("duplicate manifest member")
        folded.add(folded_name)
        declared[member.path] = member

    total_declared = sum(member.size for member in expected)
    if total_declared > MAX_TOTAL_BYTES:
        raise _refuse("manifest total byte budget")

    checked: list[CheckedMember] = []
    try:
        with zipfile.ZipFile(io.BytesIO(archive), "r") as bundle:
            entries = bundle.infolist()
            if len(entries) != len(expected) or len(entries) > MAX_MEMBERS:
                raise _refuse("undeclared, missing, or excess archive members")
            seen: set[str] = set()
            total_actual = 0
            for info in entries:
                name = info.filename
                if not _canonical(name):
                    raise _refuse("unsafe archive path or file type")
                folded_name = name.casefold()
                if folded_name in seen:
                    raise _refuse("duplicate archive member")
                seen.add(folded_name)
                spec = declared.get(name)
                if spec is None:
                    raise _refuse("undeclared archive member")
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    info.is_dir()
                    or (info.create_system == 3 and file_type not in (0, stat.S_IFREG))
                    or info.flag_bits & 1
                    or info.compress_type not in _ALLOWED_COMPRESSION
                    or info.file_size != spec.size
                    or info.file_size > MAX_MEMBER_BYTES
                    or (info.file_size > 0 and info.compressed_size == 0)
                    or info.file_size > info.compress_size * MAX_RATIO
                ):
                    raise _refuse("invalid archive member encoding or size")
                total_actual += info.file_size
                if total_actual > MAX_TOTAL_BYTES:
                    raise _refuse("unpacked byte budget")
                with bundle.open(info, "r") as stream:
                    payload = stream.read(spec.size + 1)
                    if len(payload) != spec.size or stream.read(1):
                        raise _refuse("actual member length mismatch")
                digest = hashlib.sha256(payload).hexdigest()
                if digest != spec.sha256:
                    raise _refuse("member hash mismatch")
                checked.append(CheckedMember(name, len(payload), digest, spec.role))
    except (
        zipfile.BadZipFile,
        RuntimeError,
        EOFError,
        zlib.error,
        OSError,
        ValueError,
    ) as exc:
        raise _refuse("corrupt or unreadable ZIP") from exc

    return tuple(checked)
