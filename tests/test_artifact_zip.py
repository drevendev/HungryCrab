"""Synthetic-only negative controls for inert ZIP member integrity (A2 slice)."""

from __future__ import annotations

import hashlib
import io
import stat
import struct
import zipfile

import pytest

from hungry_crab.artifact_zip import ExpectedMember, check_zip_members
from hungry_crab.errors import CrabError


def _zip(*members: tuple[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return stream.getvalue()


def _expect(name: str, data: bytes, role: str = "evidence") -> ExpectedMember:
    return ExpectedMember(name, len(data), hashlib.sha256(data).hexdigest(), role)


def test_all_declared_regular_members_match_exact_bytes() -> None:
    first, second = b'{"ok":true}', b"# Evidence\n"
    blob = _zip(("manifest.json", first), ("pages/report.md", second))
    records = check_zip_members(
        blob, (_expect("manifest.json", first), _expect("pages/report.md", second))
    )
    assert [(record.path, record.sha256, record.role) for record in records] == [
        ("manifest.json", hashlib.sha256(first).hexdigest(), "evidence"),
        ("pages/report.md", hashlib.sha256(second).hexdigest(), "evidence"),
    ]
    assert all(not hasattr(record, "trusted") for record in records)


@pytest.mark.parametrize(
    "bad_name",
    [
        "../payload.json",
        "/tmp/payload.json",
        "C:/payload.json",
        "a/../payload.json",
        "a//payload.json",
        "a\\payload.json",
        "nul.txt",
        "folder/CON.json",
        "payload.exe",
        "folder./payload.json",
        "a/./payload.md",
        "a/b/../../payload.md",
    ],
)
def test_refuse_unsafe_and_non_evidence_paths(bad_name: str) -> None:
    blob = _zip((bad_name, b"a"))
    with pytest.raises(CrabError):
        check_zip_members(blob, (_expect(bad_name, b"a"),))


def test_refuse_missing_or_extra_member() -> None:
    blob = _zip(("manifest.json", b"{}"), ("extra.md", b"extra"))
    with pytest.raises(CrabError):
        check_zip_members(blob, (_expect("manifest.json", b"{}"),))


def test_refuse_casefold_collision_in_archive() -> None:
    blob = _zip(("a.json", b"first"), ("A.JSON", b"second"))
    with pytest.raises(CrabError):
        check_zip_members(blob, (_expect("a.json", b"first"), _expect("A.JSON", b"second")))


def test_refuse_same_name_duplicate_archive_members() -> None:
    blob = _zip(("a.json", b"first"), ("a.json", b"second"))
    with pytest.raises(CrabError):
        check_zip_members(blob, (_expect("a.json", b"first"),))


def test_refuse_hash_mismatch_even_for_self_consistent_archive() -> None:
    blob = _zip(("manifest.json", b'{"ok":null}'))
    with pytest.raises(CrabError, match="hash mismatch"):
        check_zip_members(blob, (_expect("manifest.json", b'{"ok":true}'),))


def test_refuse_member_size_mismatch() -> None:
    blob = _zip(("manifest.json", b"123"))
    with pytest.raises(CrabError):
        check_zip_members(blob, (_expect("manifest.json", b"12"),))


def test_refuse_unix_symlink_even_if_named_json() -> None:
    stream = io.BytesIO()
    info = zipfile.ZipInfo("manifest.json")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(info, b"target")
    with pytest.raises(CrabError):
        check_zip_members(stream.getvalue(), (_expect("manifest.json", b"target"),))


@pytest.mark.parametrize("unsupported_flag", [0x0001, 0x0020, 0x0040, 0x1000])
def test_refuse_unsupported_zip_feature_flags(unsupported_flag: int) -> None:
    """Even matched stored bytes cannot authorize unsupported ZIP feature flags."""
    blob = bytearray(_zip(("manifest.json", b"ok"), compression=zipfile.ZIP_STORED))
    local_header = blob.index(b"PK\x03\x04")
    central_header = blob.index(b"PK\x01\x02")
    struct.pack_into("<H", blob, local_header + 6, unsupported_flag)
    struct.pack_into("<H", blob, central_header + 8, unsupported_flag)
    with pytest.raises(CrabError, match="invalid archive member encoding or size"):
        check_zip_members(bytes(blob), (_expect("manifest.json", b"ok"),))



@pytest.mark.parametrize("local_only_flag", [0x0001, 0x0020, 0x0040, 0x1000])
def test_refuse_unsupported_local_only_zip_feature_flags(local_only_flag: int) -> None:
    """Local ZIP flags must match the central directory even if bytes and hash match."""
    blob = bytearray(_zip(("manifest.json", b"ok"), compression=zipfile.ZIP_STORED))
    local_header = blob.index(b"PK\x03\x04")
    central_header = blob.index(b"PK\x01\x02")
    assert struct.unpack_from("<H", blob, central_header + 8)[0] == 0
    struct.pack_into("<H", blob, local_header + 6, local_only_flag)
    with pytest.raises(CrabError, match="local and central ZIP headers disagree"):
        check_zip_members(bytes(blob), (_expect("manifest.json", b"ok"),))


def test_refuse_local_only_zip_compression_mismatch() -> None:
    """Central ZIP_STORED must not conceal a different local compression method."""
    blob = bytearray(_zip(("manifest.json", b"ok"), compression=zipfile.ZIP_STORED))
    local_header = blob.index(b"PK\x03\x04")
    struct.pack_into("<H", blob, local_header + 8, zipfile.ZIP_DEFLATED)
    with pytest.raises(CrabError, match="local and central ZIP headers disagree"):
        check_zip_members(bytes(blob), (_expect("manifest.json", b"ok"),))


def test_refuse_high_compression_ratio() -> None:
    payload = b"x" * 10000
    blob = _zip(("manifest.json", payload))
    with pytest.raises(CrabError, match="encoding or size"):
        check_zip_members(blob, (_expect("manifest.json", payload),))


def test_refuse_bad_manifest_digest_and_bool_size() -> None:
    blob = _zip(("manifest.json", b"ok"))
    with pytest.raises(CrabError):
        check_zip_members(blob, (ExpectedMember("manifest.json", 2, "f" * 64, ""),))
    with pytest.raises(CrabError):
        check_zip_members(blob, (ExpectedMember("manifest.json", True, "f" * 64, "evidence"),))


def test_refuse_zip_that_is_not_an_archive() -> None:
    with pytest.raises(CrabError, match="corrupt"):
        check_zip_members(b"not a zip", (_expect("manifest.json", b"{}"),))
