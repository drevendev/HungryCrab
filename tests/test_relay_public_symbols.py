"""Exercise image/DBI binding rather than conflating the two PDB age fields."""

from __future__ import annotations

import json
import runpy
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_script = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/relay_public_symbols.py")
)
_symbols = SimpleNamespace(**_script)
GUID = "44294CBD-55E8-CD67-B3D5-6B689F03152D"
EXPECTED = GUID.replace("-", "") + "1"


def _pdb(age: int = 1, *, block_size: int = 4096) -> bytes:
    """Owned MSF metadata fixture with two intervening blocks; no executable content."""
    data = bytearray(block_size * 9)
    data[:32] = _script["MSF_MAGIC"]
    struct.pack_into("<6I", data, 32, block_size, 1, 9, 36, 0, 4)
    struct.pack_into("<I", data, block_size * 4, 3)
    struct.pack_into("<9I", data, block_size * 3, 4, 0xFFFFFFFF, 28, block_size + 4, 64, 5, 6, 7, 8)
    struct.pack_into("<3I", data, block_size * 8, 0xFFFFFFFF, 20091201, age)
    return bytes(data)


@pytest.mark.parametrize("info_age", [1, 3, 4])
def test_matching_dbi_age_accepts_current_and_source_indexed_pdb(info_age: int) -> None:
    summary = f"GUID: {{{GUID}}}\nAge: {info_age}\n"
    age = _symbols.dbi_age(_pdb())
    result = _symbols.classify_identity(summary, EXPECTED, actual_dbi_age=age)
    assert result["identity_status"] == "MATCHED"
    assert result["actual_age"] == info_age
    assert result["actual_dbi_age"] == 1


@pytest.mark.parametrize(
    ("info_age", "dbi", "guid", "status"),
    [
        (4, None, GUID, "IDENTITY_UNAVAILABLE_HELD"),
        (4, 0, GUID, "IDENTITY_UNAVAILABLE_HELD"),
        (4, 2, GUID, "IDENTITY_MISMATCH_HELD"),
        (0, 1, GUID, "IDENTITY_MISMATCH_HELD"),
        (4, 1, "00000000-0000-0000-0000-000000000000", "IDENTITY_MISMATCH_HELD"),
    ],
)
def test_missing_or_wrong_binding_proof_stays_held(
    info_age: int, dbi: int | None, guid: str, status: str
) -> None:
    result = _symbols.classify_identity(
        f"GUID: {{{guid}}}\nAge: {info_age}\n", EXPECTED, actual_dbi_age=dbi
    )
    assert result["identity_status"] == status


@pytest.mark.parametrize("summary", ["", f"GUID: {{{GUID}}}\n", "Age: 1\n"])
def test_incomplete_info_summary_never_qualifies(summary: str) -> None:
    result = _symbols.classify_identity(summary, EXPECTED, actual_dbi_age=1)
    assert result["identity_status"] == "IDENTITY_UNAVAILABLE_HELD"


@pytest.mark.parametrize("block_size", [512, 1024, 2048, 4096])
def test_dbi_reader_follows_stream_block_lists(block_size: int) -> None:
    assert _symbols.dbi_age(_pdb(7, block_size=block_size)) == 7


@pytest.mark.parametrize(
    ("offset", "value"),
    [
        (32, 8192),
        (40, 10),
        (44, 0xFFFFFFFF),
        (52, 9),
        (4096 * 4, 9),
        (4096 * 3, 4097),
        (4096 * 3 + 16, 0xFFFFFFFF),
        (4096 * 3 + 16, 4),
        (4096 * 3 + 32, 9),
        (4096 * 8, 0),
        (4096 * 8 + 4, 0),
    ],
)
def test_damaged_dbi_metadata_cannot_supply_an_age(offset: int, value: int) -> None:
    data = bytearray(_pdb())
    struct.pack_into("<I", data, offset, value)
    with pytest.raises(ValueError):
        _symbols.dbi_age(bytes(data))


@pytest.mark.parametrize("data", [b"", b"not a PDB", _pdb()[:-1]])
def test_truncated_or_non_pdb_data_cannot_supply_an_age(data: bytes) -> None:
    with pytest.raises(ValueError):
        _symbols.dbi_age(data)


def test_collection_retains_missing_dbi_instead_of_qualifying_summary_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return b"incomplete PDB metadata"

    def run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout=f"GUID: {{{GUID}}}\nAge: 4\n".encode(), stderr=b"")

    monkeypatch.setattr(_symbols.shutil, "which", lambda _: "owned-llvm")
    monkeypatch.setattr(_symbols.subprocess, "run", run)
    monkeypatch.setattr(_symbols.urllib.request, "urlopen", lambda *a, **k: Response())
    destination = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["symbols", "--out", str(destination)])
    _symbols.main()
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PUBLIC_RECORDS_CAPTURED_WITH_IDENTITY_HOLDS"
    assert all(record["dbi_read_error"] for record in manifest["records"])
    assert all(record["identity_status"] != "MATCHED" for record in manifest["records"])
