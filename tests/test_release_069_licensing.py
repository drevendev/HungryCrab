"""Owned grant-role/path controls, including conservative negative counterparts."""

from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.licensing.detect import detect_from_text, detect_in_repo
from hungry_crab.licensing.path_grants import GrantResolver
from hungry_crab.licensing.sections import composite_sections

MIT = (Path(__file__).parents[1] / "LICENSE").read_text(encoding="utf-8")


class Reader:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read(self, sha: str, path: str) -> str | None:
        return self.files.get(path)

    def entries(self, sha: str, directory: str = "") -> dict[str, str]:
        prefix = directory + "/" if directory else ""
        return {
            path.removeprefix(prefix): "100644"
            for path in self.files
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        }


def test_notice_reference_preserves_grant_and_original_region(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").write_text(MIT, encoding="utf-8")
    (tmp_path / "COPYRIGHT").write_text(
        "Copyright (c) Dreven\n\nSee LICENSE for the full license text.\n\n", encoding="utf-8"
    )
    findings = detect_in_repo(tmp_path, [])
    assert findings.spdx == "MIT"
    assert "COPYRIGHT" in findings.notice_files
    assert findings.evidence[0].start is not None
    assert "Permission is hereby" in MIT.splitlines()[findings.evidence[0].start - 1]


def test_nyu_and_exact_formatting_keep_complete_operative_terms() -> None:
    body = MIT[MIT.index("Permission is hereby") :]
    nyu = "MIT License\nCopyright (c) 2013-2015 New York University\n\n" + body
    assert detect_from_text(nyu)[0] == "MIT"
    formatted = (
        "Owned SDK ver. 1.0\nCopyright (c) Dreven\nAll rights reserved.\nMIT License\n"
        + body.replace('(the "Software")', '(the ""Software"")').replace('"AS IS"', "*AS IS*")
    )
    assert detect_from_text(formatted)[0] == "MIT"
    for rider in ("Non-commercial use only.", "Redistribution prohibited.", "Permission required."):
        assert detect_from_text(nyu + "\n" + rider)[0] is None


def test_composite_boundaries_do_not_discard_unconsumed_text() -> None:
    text = (
        MIT + "\nend of terms and conditions\n\nThe externally maintained libraries from which "
        'parts of the Software is derived\nare:\n\n- owned-library, licensed as follows:\n  """\n'
        + "\n".join("    " + line for line in MIT.splitlines())
        + '\n  """\n'
    )
    sections = composite_sections(text)
    assert sections is not None and len(sections) == 2
    assert sections[1].role == "component-notice"
    assert detect_from_text(text)[0] == "MIT"
    assert detect_from_text(text + "\nNon-commercial use only.")[0] is None
    assert composite_sections(text[:-5]) is None


def test_equivalent_nested_grant_and_all_notices_are_retained() -> None:
    reader = Reader(
        {
            "LICENSE": MIT,
            "src/LICENSE": MIT,
            "src/NOTICE": "Original component attribution",
            "src/core.py": "print(1)",
        }
    )
    grant = GrantResolver(reader, "a" * 40).resolve("src/core.py")
    assert grant.expression == "MIT"
    assert "src/LICENSE" in grant.evidence and "src/NOTICE" in grant.notices
    reader.files["src/LICENSE"] = MIT + "\nResearch use only."
    assert GrantResolver(reader, "a" * 40).resolve("src/core.py").expression is None


@pytest.mark.parametrize("path", ["../secret.py", "/root.py", "a\\b.py", "a/../b.py", "C:/b.py"])
def test_path_grant_refuses_unsafe_paths(path: str) -> None:
    assert GrantResolver(Reader({"LICENSE": MIT}), "a" * 40).resolve(path).expression is None
