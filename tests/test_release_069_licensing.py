"""Owned grant-role/path controls, including conservative negative counterparts."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_licensing import APACHE_TEXT, BSD3_TEXT

from hungry_crab.licensing.detect import detect_from_text, detect_in_repo, find_source_header
from hungry_crab.licensing.matrix import allows_alternative
from hungry_crab.licensing.path_grants import GrantResolver
from hungry_crab.licensing.sections import composite_sections

MIT = (Path(__file__).parents[1] / "LICENSE").read_text(encoding="utf-8")


def test_source_headers_ignore_strings_and_conflicting_comments() -> None:
    assert find_source_header("# SPDX-License-Identifier: MIT\nVALUE = 1", path="a.py") == "MIT"
    assert (
        find_source_header(
            '"""\n# SPDX-License-Identifier: GPL-3.0-only\n"""\nVALUE = 1', path="a.py"
        )
        is None
    )
    assert (
        find_source_header(
            "# SPDX-License-Identifier: MIT\n# SPDX-License-Identifier: Apache-2.0\n", path="a.py"
        )
        == "NOASSERTION"
    )
    assert not allows_alternative("MIT AND Apache-2.0", "MIT")
    assert not allows_alternative("(MIT AND Apache-2.0) OR BSD-3-Clause", "MIT")
    assert allows_alternative("MIT OR Apache-2.0", "MIT")


def test_explicit_reuse_or_branch_preserves_declared_scope_and_notices() -> None:
    files = {
        "pyproject.toml": '[project]\nlicense = "MIT OR Apache-2.0"\n',
        "LICENSES/MIT.txt": MIT,
        "LICENSES/Apache-2.0.txt": APACHE_TEXT,
        "src/core.py": "# SPDX-License-Identifier: MIT\nVALUE = 1\n",
    }
    resolver = GrantResolver(Reader(files), "a" * 40)
    result = resolver.resolve("src/core.py", selected_branch="MIT")
    assert result.expression == "MIT" and result.chosen_branch == "MIT"
    assert result.declared_expression == "MIT OR Apache-2.0"
    assert set(result.evidence) == {"LICENSES/MIT.txt", "LICENSES/Apache-2.0.txt"}
    assert resolver.resolve("src/core.py", selected_branch="BSD-3-Clause").expression is None
    files["pyproject.toml"] = '[project]\nlicense = "MIT AND Apache-2.0"\n'
    assert GrantResolver(Reader(files), "a" * 40).resolve("src/core.py").expression is None


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


def test_original_byte_regions_include_bom_crlf_and_never_accept_a_truncated_grant(
    tmp_path: Path,
) -> None:
    raw = b"\xef\xbb\xbf" + MIT.replace("\n", "\r\n").encode()
    (tmp_path / "LICENSE").write_bytes(raw)
    findings = detect_in_repo(tmp_path, [])
    assert findings.spdx == "MIT"
    evidence = findings.evidence[0]
    assert evidence.byte_start is not None and evidence.byte_end == len(raw)
    assert raw[evidence.byte_start :].startswith(b"Permission is hereby")
    (tmp_path / "LICENSE").write_text(
        MIT + " " * 200_000 + "Non-commercial use only.", encoding="utf-8"
    )
    assert detect_in_repo(tmp_path, []).spdx == "NOASSERTION"


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


def test_data_catalogue_and_sidecar_do_not_relicense_implementation() -> None:
    files = {
        "apache-2.0.LICENSE": APACHE_TEXT,
        "cc-by-4.0.LICENSE": "Attribution 4.0 International\n",
        "NOTICE": "The software is licensed under the Apache License Version 2.0.",
        "src/core.py": "# SPDX-License-Identifier: Apache-2.0\nVALUE = 1\n",
        "src/other.py.LICENSE": "Research use only.",
        "src/bsd-simplified.LICENSE": BSD3_TEXT,
        "data/restricted/LICENSE": "Internal use only.",
        "data/restricted/specimen.txt": "sample text",
    }
    result = GrantResolver(Reader(files), "a" * 40).resolve("src/core.py")
    assert result.expression == "Apache-2.0" and result.material_role == "implementation"
    assert result.scope == "file-header-and-software-declaration"
    assert {r["role"] for r in result.references} == {"other-file-sidecar", "separate-named-grant"}
    assert (
        GrantResolver(Reader(files), "a" * 40).resolve("data/restricted/specimen.txt").expression
        is None
    )
    for text in ("Research use only.", MIT + "\nNo commercial use."):
        files["src/LICENSE"] = text
        assert GrantResolver(Reader(files), "a" * 40).resolve("src/core.py").expression is None


def test_configuration_topic_is_not_a_directory_grant_and_rider_remains_held() -> None:
    article = (
        "# License Texts\n\nA reporter uses configuration to select text providers.\n"
        "\n## Configuration\n\nThe reporter reads the selected file.\n"
    )
    files = {
        "LICENSE": MIT,
        "docs/LICENSE": MIT,
        "docs/license-texts.md": article,
        "docs/config.md": "# Owned configuration\n",
    }
    result = GrantResolver(Reader(files), "a" * 40).resolve("docs/config.md")
    assert result.expression == "MIT"
    assert result.references == [
        {"path": "docs/license-texts.md", "role": "documentation-not-grant"}
    ]
    files["docs/license-texts.md"] += "\nCommercial use is prohibited.\n"
    assert GrantResolver(Reader(files), "a" * 40).resolve("docs/config.md").expression is None


def test_composite_regions_and_complete_notice_remain_separate(tmp_path: Path) -> None:
    text = (
        MIT + "\nend of terms and conditions\n\n"
        "The externally maintained libraries from which parts of the Software is derived are:\n\n"
        '- owned-component, licensed as follows:\n  """\n'
        + "\n".join("    " + line for line in MIT.splitlines())
        + '\n  """\n'
    )
    (tmp_path / "LICENSE").write_text(text, encoding="utf-8")
    findings = detect_in_repo(tmp_path, [])
    assert findings.spdx == "MIT" and len(findings.scopes) == 2
    assert findings.scopes[1]["applicability"] == "unmapped-component"
    evidence = findings.evidence[0]
    assert evidence.byte_start is not None and evidence.byte_end is not None
    assert b"Permission is hereby" in text.encode()[evidence.byte_start : evidence.byte_end]
    reader = Reader(
        {"LICENSE": text, "src/core.py": "VALUE = 1", "vendor/owned-component/core.py": "VALUE = 2"}
    )
    grant = GrantResolver(reader, "a" * 40).resolve("src/core.py")
    assert grant.expression == "MIT" and "LICENSE" in grant.notices
    held = GrantResolver(reader, "a" * 40).resolve("vendor/owned-component/core.py")
    assert held.expression is None and held.material_role == "component"
