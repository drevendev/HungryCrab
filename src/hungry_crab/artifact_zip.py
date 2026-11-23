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
import struct
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
_BAD_WINDOWS_STEMS = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}) | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789¹²³"
}
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_WINDOWS_SPECIAL_ATTRIBUTES = 0x0008 | 0x0010 | 0x0040 | 0x0400


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
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in path)
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
            or any(char in _WINDOWS_FORBIDDEN for char in part)
            or part.split(".", 1)[0].upper() in _BAD_WINDOWS_STEMS
        ):
            return False
    return PurePosixPath(path).suffix.lower() in _ALLOWED_SUFFIXES


def _check_zip_envelope(archive: bytes, entries: Sequence[zipfile.ZipInfo]) -> None:
    """Admit a single-disk ZIP with no unaccounted byte ranges.

    The A2 evidence carrier intentionally does not accept prepended stubs,
    comments, archive extra fields, ZIP64 or unrelated local records.
    This does not attest who produced the ZIP.
    """
    eocd = len(archive) - 22
    if eocd < 0 or archive[eocd : eocd + 4] != b"PK\x05\x06":
        raise _refuse("invalid ZIP envelope")
    (
        disk,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_start,
        comment_size,
    ) = struct.unpack_from("<4H2IH", archive, eocd + 4)
    if (
        disk != 0
        or directory_disk != 0
        or disk_entries != len(entries)
        or total_entries != len(entries)
        or comment_size != 0
        or directory_start + directory_size != eocd
    ):
        raise _refuse("invalid ZIP envelope")

    # Parse the entire central directory: zipfile's infolist() alone does not
    # prove that no unlisted records or opaque directory bytes remain.
    cursor = directory_start
    for info in entries:
        if cursor + 46 > eocd or archive[cursor : cursor + 4] != b"PK\x01\x02":
            raise _refuse("invalid ZIP envelope")
        name_size, extra_size, entry_comment_size = struct.unpack_from("<3H", archive, cursor + 28)
        member_disk = struct.unpack_from("<H", archive, cursor + 34)[0]
        local_offset = struct.unpack_from("<I", archive, cursor + 42)[0]
        cursor += 46 + name_size + extra_size + entry_comment_size
        if (
            member_disk != 0
            or extra_size != 0
            or entry_comment_size != 0
            or cursor > eocd
            or local_offset != info.header_offset
        ):
            raise _refuse("invalid ZIP envelope")
    if cursor != eocd:
        raise _refuse("invalid ZIP envelope")

    # Every local header, payload and optional data descriptor must be
    # contiguous, with no prefix, gap, orphan record or trailing payload.
    boundary = 0
    for info in sorted(entries, key=lambda item: item.header_offset):
        offset = info.header_offset
        if (
            offset != boundary
            or offset + 30 > directory_start
            or archive[offset : offset + 4] != b"PK\x03\x04"
        ):
            raise _refuse("invalid ZIP envelope")
        name_size, extra_size = struct.unpack_from("<2H", archive, offset + 26)
        payload_end = offset + 30 + name_size + extra_size + info.compress_size
        if extra_size != 0 or payload_end > directory_start:
            raise _refuse("invalid ZIP envelope")
        local_crc, local_compressed, local_unpacked = struct.unpack_from(
            "<3I", archive, offset + 14
        )
        if info.flag_bits & 0x0008:
            if (
                local_crc not in (0, info.CRC)
                or local_compressed not in (0, info.compress_size)
                or local_unpacked not in (0, info.file_size)
            ):
                raise _refuse("invalid ZIP envelope")
            descriptor = struct.pack("<3I", info.CRC, info.compress_size, info.file_size)
            if archive[payload_end : payload_end + 12] == descriptor:
                boundary = payload_end + 12
            elif archive[payload_end : payload_end + 16] == b"PK\x07\x08" + descriptor:
                boundary = payload_end + 16
            else:
                raise _refuse("invalid ZIP envelope")
        else:
            if (local_crc, local_compressed, local_unpacked) != (
                info.CRC,
                info.compress_size,
                info.file_size,
            ):
                raise _refuse("invalid ZIP envelope")
            boundary = payload_end
        if boundary > directory_start:
            raise _refuse("invalid ZIP envelope")
    if boundary != directory_start:
        raise _refuse("invalid ZIP envelope")


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
            _check_zip_envelope(archive, entries)
            seen: set[str] = set()
            total_actual = 0
            for info in entries:
                # zipfile may normalize a raw NUL suffix or Windows separators.
                # Never admit an alias merely because its normalized spelling
                # matches a declared manifest path.
                name = info.filename
                raw_name = info.orig_filename
                if raw_name != name or not _canonical(raw_name):
                    raise _refuse("unsafe raw archive path or alias")
                folded_name = name.casefold()
                if folded_name in seen:
                    raise _refuse("duplicate archive member")
                seen.add(folded_name)
                spec = declared.get(name)
                if spec is None:
                    raise _refuse("undeclared archive member")
                # ZIP's central directory does not certify the local header.
                # zipfile.open() checks its filename, but does not reject
                # unsupported flags or compression mismatches in that header.
                offset = info.header_offset
                if (
                    offset < 0
                    or offset + 30 > len(archive)
                    or archive[offset : offset + 4] != b"PK\x03\x04"
                ):
                    raise _refuse("invalid local ZIP header")
                local_flags = int.from_bytes(archive[offset + 6 : offset + 8], "little")
                local_method = int.from_bytes(archive[offset + 8 : offset + 10], "little")
                if local_flags != info.flag_bits or local_method != info.compress_type:
                    raise _refuse("local and central ZIP headers disagree")
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                # Admit only documented flags that this inert reader supports:
                # data descriptor, UTF-8 names, and Deflate option bits.
                supported_flags = 0x0008 | 0x0800
                if info.compress_type == zipfile.ZIP_DEFLATED:
                    supported_flags |= 0x0002 | 0x0004
                if (
                    info.is_dir()
                    # Windows volume, directory, device and reparse-point entries
                    # may have ordinary filenames; filename-based is_dir() misses them.
                    or info.external_attr & _WINDOWS_SPECIAL_ATTRIBUTES
                    # The claimed origin OS cannot authorize special POSIX mode bits.
                    or file_type not in (0, stat.S_IFREG)
                    or info.flag_bits & ~supported_flags
                    or info.compress_type not in _ALLOWED_COMPRESSION
                    or info.file_size != spec.size
                    or info.file_size > MAX_MEMBER_BYTES
                    or (info.file_size > 0 and info.compress_size == 0)
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
        NotImplementedError,
        EOFError,
        zlib.error,
        OSError,
        ValueError,
    ) as exc:
        raise _refuse("corrupt or unreadable ZIP") from exc

    return tuple(checked)
