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
        "folder/COM¹.json",
        "folder/COM².json",
        "folder/COM³.json",
        "folder/LPT¹.json",
        "folder/LPT².json",
        "folder/LPT³.json",
        "a?.json",
        "a<.json",
        "a>.json",
        'a".json',
        "a|.json",
        "a*.json",
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


@pytest.mark.parametrize(
    "hidden",
    ["\u202e", "\u200b", "\u2066", "\u0085", "\u2028", "\ud800"],
)
def test_refuse_invisible_or_ill_formed_manifest_paths(hidden: str) -> None:
    payload = b"ok"
    blob = _zip(("pages/report.md", payload))
    with pytest.raises(CrabError, match="invalid manifest member"):
        check_zip_members(blob, (_expect(f"pages/{hidden}report.md", payload),))


@pytest.mark.parametrize(
    "hidden",
    ["\u202e", "\u200b", "\u2066", "\u0085", "\u2028"],
)
def test_refuse_invisible_or_format_control_archive_paths(hidden: str) -> None:
    payload = b"ok"
    blob = _zip((f"pages/rep{hidden}ort.md", payload))
    with pytest.raises(CrabError, match="unsafe raw archive path or alias"):
        check_zip_members(blob, (_expect("pages/report.md", payload),))


def test_safe_unicode_member_path_remains_allowed() -> None:
    payload = b"{}"
    member_name = "sources/café.json"
    checked = check_zip_members(_zip((member_name, payload)), (_expect(member_name, payload),))
    assert checked[0].path == member_name


@pytest.mark.parametrize(
    "bad_name",
    [
        "folder/COM¹.json",
        "folder/LPT³.json",
        "CONIN$.txt",
        "folder/conout$.json",
        "a?.json",
        "a<.json",
        "a|.json",
    ],
)
def test_windows_unsafe_paths_rejected_in_raw_zip_and_manifest_separately(
    bad_name: str,
) -> None:
    """Raw archive names and externally declared names must each be checked."""
    payload = b"ok"
    with pytest.raises(CrabError, match="unsafe raw archive path or alias"):
        check_zip_members(_zip((bad_name, payload)), (_expect("safe.json", payload),))
    with pytest.raises(CrabError, match="invalid manifest member"):
        check_zip_members(_zip(("safe.json", payload)), (_expect(bad_name, payload),))


@pytest.mark.parametrize(
    ("original_name", "raw_name", "declared_name"),
    [
        ("manifest.jsonX", b"manifest.json\x00", "manifest.json"),
        ("pages_report.md", b"pages\\report.md", "pages/report.md"),
    ],
)
def test_refuse_raw_zip_member_name_alias(
    original_name: str, raw_name: bytes, declared_name: str
) -> None:
    """Reject original ZIP names even if zipfile normalizes them to declared paths."""
    blob = _zip((original_name, b"ok"), compression=zipfile.ZIP_STORED)
    original_bytes = original_name.encode("ascii")
    assert len(original_bytes) == len(raw_name)
    # Seekable synthetic ZIP: same-length names appear in both headers.
    assert blob.count(original_bytes) == 2
    blob = blob.replace(original_bytes, raw_name)
    with pytest.raises(CrabError, match="unsafe raw archive path or alias"):
        check_zip_members(blob, (_expect(declared_name, b"ok"),))


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


@pytest.mark.parametrize("attribute", [0x0008, 0x0010, 0x0040, 0x0400])
def test_refuse_windows_special_attributes_without_trailing_slash(attribute: int) -> None:
    """DOS metadata can flag a directory, device, label or reparse point as a file."""
    info = zipfile.ZipInfo("manifest.json")
    info.create_system = 0
    info.external_attr = attribute
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(info, b"ok")
    with pytest.raises(CrabError, match="invalid archive member encoding or size"):
        check_zip_members(stream.getvalue(), (_expect("manifest.json", b"ok"),))


def test_allow_ordinary_windows_archive_attribute() -> None:
    info = zipfile.ZipInfo("manifest.json")
    info.create_system = 0
    info.external_attr = 0x0020
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(info, b"ok")
    checked = check_zip_members(stream.getvalue(), (_expect("manifest.json", b"ok"),))
    assert checked[0].path == "manifest.json"


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFCHR, stat.S_IFDIR])
def test_refuse_special_unix_mode_even_with_dos_origin(file_type: int) -> None:
    """A DOS/FAT origin marker cannot hide special-file metadata in the mode bits."""
    info = zipfile.ZipInfo("manifest.json")
    info.create_system = 0
    info.external_attr = ((file_type | 0o644) << 16) | 0x0020
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(info, b"ok")
    with pytest.raises(CrabError, match="invalid archive member encoding or size"):
        check_zip_members(stream.getvalue(), (_expect("manifest.json", b"ok"),))


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


@pytest.mark.parametrize("damage", ["prefix", "trailer", "gap", "orphan"])
def test_refuse_unaccounted_zip_envelope_bytes(damage: str) -> None:
    """A valid listed member cannot hide unrelated ZIP envelope bytes."""
    blob = bytearray(_zip(("manifest.json", b"ok"), compression=zipfile.ZIP_STORED))
    if damage == "prefix":
        blob[:0] = b"untracked-stub"
    elif damage == "trailer":
        blob.extend(b"untracked-tail")
    else:
        directory = blob.index(b"PK\x01\x02")
        if damage == "orphan":
            extra = _zip(("hidden.txt", b"hidden"), compression=zipfile.ZIP_STORED)
            added = extra[: extra.index(b"PK\x01\x02")]
        else:
            added = b"untracked-gap"
        blob[directory:directory] = added
        eocd = blob.rfind(b"PK\x05\x06")
        struct.pack_into("<I", blob, eocd + 16, directory + len(added))
    with pytest.raises(CrabError):
        check_zip_members(bytes(blob), (_expect("manifest.json", b"ok"),))


def test_refuse_nonzero_disk_start_in_central_entry() -> None:
    """A single-disk EOCD cannot authorize a member from another disk."""
    blob = bytearray(_zip(("manifest.json", b"ok"), compression=zipfile.ZIP_STORED))
    directory = blob.index(b"PK\x01\x02")
    struct.pack_into("<H", blob, directory + 34, 1)
    with pytest.raises(CrabError):
        check_zip_members(bytes(blob), (_expect("manifest.json", b"ok"),))


def test_refuse_extra_central_directory_records() -> None:
    """Unlisted central-directory bytes are not covered by infolist()."""
    blob = bytearray(_zip(("manifest.json", b"ok"), compression=zipfile.ZIP_STORED))
    eocd = blob.rfind(b"PK\x05\x06")
    added = b"PK\x05\x05\x00\x00"
    blob[eocd:eocd] = added
    eocd += len(added)
    old_size = struct.unpack_from("<I", blob, eocd + 12)[0]
    struct.pack_into("<I", blob, eocd + 12, old_size + len(added))
    with pytest.raises(CrabError):
        check_zip_members(bytes(blob), (_expect("manifest.json", b"ok"),))


def test_refuse_mismatched_local_header_crc() -> None:
    """The content hash alone must not bless a forged local header."""
    blob = bytearray(_zip(("manifest.json", b"ok"), compression=zipfile.ZIP_STORED))
    struct.pack_into("<I", blob, 14, 0)
    with pytest.raises(CrabError, match="ZIP envelope"):
        check_zip_members(bytes(blob), (_expect("manifest.json", b"ok"),))


def test_allow_standard_streaming_zip_data_descriptor() -> None:
    """A normal non-seekable producer may emit a bounded data descriptor."""

    class NonSeekableStream(io.BytesIO):
        def seek(self, pos: int, whence: int = 0) -> int:
            raise OSError("stream cannot seek")

    stream = NonSeekableStream()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("manifest.json", b"ok")
    blob = stream.getvalue()
    assert struct.unpack_from("<H", blob, 6)[0] & 0x0008
    checked = check_zip_members(blob, (_expect("manifest.json", b"ok"),))
    assert checked[0].path == "manifest.json"
