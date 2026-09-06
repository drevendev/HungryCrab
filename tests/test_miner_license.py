from __future__ import annotations

from helpers import read_json

from hungry_crab.digest import DigestResult


def test_npm_is_mit_and_copyable(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "license.json")
    assert data["spdx"] == "MIT"
    assert data["class"] == "permissive"
    assert data["confidence"] >= 0.9
    assert data["license_files"] == ["LICENSE"]
    assert data["human_review"] is False
    assert data["conflicts"] == []
    assert data["verdict"]["mode"] == "COPY"
    assert data["modes_by_maw_class"] == {
        "permissive": "COPY",
        "gpl": "COPY",
        "proprietary": "COPY",
    }
    assert data["maw_license"] == "MIT"


def test_python_is_apache_with_notice(py_digest: DigestResult) -> None:
    data = read_json(py_digest, "license.json")
    assert data["spdx"] == "Apache-2.0"
    assert data["class"] == "permissive-notice"
    assert data["notice_files"] == ["NOTICE"]
    assert data["verdict"]["mode"] == "COPY"
    assert data["verdict"]["notice_required"] is True
    sources = {e["source"]: e["spdx"] for e in data["evidence"]}
    assert sources["file"] == "Apache-2.0"
    assert sources["manifest"] == "Apache-2.0"
    assert data["human_review"] is False


def test_dotnet_is_gpl_and_needs_a_clean_room(dotnet_digest: DigestResult) -> None:
    data = read_json(dotnet_digest, "license.json")
    assert data["spdx"] == "GPL-3.0-only"
    assert data["class"] == "gpl"
    assert data["verdict"]["mode"] == "REIMPLEMENT"
    assert data["modes_by_maw_class"] == {
        "permissive": "REIMPLEMENT",
        "gpl": "COPY",
        "proprietary": "IDEAS_ONLY",
    }
    assert data["human_review"] is False
    assert any("-only" in note for note in data["notes"])
