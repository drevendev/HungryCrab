from __future__ import annotations

from pathlib import Path

import pytest
from helpers import write_tree

from hungry_crab.licensing import (
    LicenseClass,
    MawClass,
    Mode,
    Relationship,
    classify,
    decide,
    decide_for_class,
    detect_from_text,
    detect_in_repo,
    maw_class,
    modes_by_maw_class,
    normalize,
)
from hungry_crab.licensing.detect import license_name_from_file, manifest_license

MIT_TEXT = (
    "MIT License\n\nCopyright (c) 2024 Someone\n\nPermission is hereby granted, free of charge, "
    "to any person obtaining a copy of this software and associated documentation files (the "
    '"Software"), to deal in the Software without restriction, including without limitation '
    "the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies "
    "of the Software.\n\nThe above copyright notice and this permission notice shall be included "
    "in all copies or substantial portions of the Software.\n"
)
MIT0_TEXT = MIT_TEXT.replace(
    "The above copyright notice and this permission notice shall be included in all copies or "
    "substantial portions of the Software.",
    "",
)
ISC_TEXT = (
    "ISC License\n\nPermission to use, copy, modify, and/or distribute this software for any "
    "purpose with or without fee is hereby granted, provided that the above copyright notice "
    "and this permission notice appear in all copies.\n"
)
ZERO_BSD_TEXT = (
    "Permission to use, copy, modify, and/or distribute this software for any purpose with or "
    "without fee is hereby granted.\n"
)
BSD3_TEXT = (
    "Redistribution and use in source and binary forms are permitted provided that:\n"
    "1. Redistributions of source code must retain the above copyright notice.\n"
    "3. Neither the name of the copyright holder nor the names of its contributors may be used.\n"
)
BSD2_TEXT = (
    "Redistribution and use in source and binary forms are permitted provided that:\n"
    "1. Redistributions of source code must retain the above copyright notice.\n"
)
APACHE_TEXT = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n"
GPL3_TEXT = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n\nPreamble\n"
GPL3_NOTICE = (
    "This program is free software: you can redistribute it and/or modify it under the terms of "
    "the GNU General Public License as published by the Free Software Foundation, either "
    "version 3 of the License, or (at your option) any later version.\n"
)
LGPL21_TEXT = "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 2.1, February 1999\n"
AGPL_TEXT = "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007\n"
MPL_TEXT = "Mozilla Public License Version 2.0\n\n1. Definitions\n"
UNLICENSE_TEXT = "This is free and unencumbered software released into the public domain.\n"
CC_BY_SA_TEXT = "Attribution-ShareAlike 4.0 International\n"
BUSL_TEXT = "Business Source License 1.1\nLicensor: Example\n"
PROPRIETARY_TEXT = "Copyright 2024 Example Corp. All rights reserved.\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (MIT_TEXT, "MIT"),
        (MIT0_TEXT, "MIT-0"),
        (ISC_TEXT, "ISC"),
        (ZERO_BSD_TEXT, "0BSD"),
        (BSD3_TEXT, "BSD-3-Clause"),
        (BSD2_TEXT, "BSD-2-Clause"),
        (APACHE_TEXT, "Apache-2.0"),
        (GPL3_TEXT, "GPL-3.0-only"),
        (GPL3_NOTICE, "GPL-3.0-or-later"),
        (LGPL21_TEXT, "LGPL-2.1-only"),
        (AGPL_TEXT, "AGPL-3.0-only"),
        (MPL_TEXT, "MPL-2.0"),
        (UNLICENSE_TEXT, "Unlicense"),
        (CC_BY_SA_TEXT, "CC-BY-SA-4.0"),
        (BUSL_TEXT, "BUSL-1.1"),
        (PROPRIETARY_TEXT, "LicenseRef-Proprietary"),
        ("// SPDX-License-Identifier: Apache-2.0 OR MIT\n", "Apache-2.0 OR MIT"),
        ("", None),
        ("Some unrelated text about crabs.", None),
    ],
)
def test_detect_from_text(text: str, expected: str | None) -> None:
    spdx, confidence = detect_from_text(text)
    assert spdx == expected
    assert (confidence > 0) == (expected is not None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mit", "MIT"),
        ("MIT License", "MIT"),
        ("GPL-3.0", "GPL-3.0-only"),
        ("GPLv2+", "GPL-2.0-or-later"),
        ("Apache 2.0", "Apache-2.0"),
        ("BSD", "BSD-3-Clause"),
        ("UNLICENSED", "LicenseRef-Proprietary"),
        ("SEE LICENSE IN LICENSE.txt", "NOASSERTION"),
        ("(MIT OR Apache-2.0)", "MIT OR Apache-2.0"),
        ("Zlib", "Zlib"),
        ("", None),
        (None, None),
    ],
)
def test_normalize(raw: str | None, expected: str | None) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("spdx", "expected"),
    [
        ("MIT", LicenseClass.PERMISSIVE),
        ("Apache-2.0", LicenseClass.PERMISSIVE_NOTICE),
        ("MPL-2.0", LicenseClass.FILE_COPYLEFT),
        ("LGPL-3.0-only", LicenseClass.LGPL),
        ("GPL-2.0-or-later", LicenseClass.GPL),
        ("AGPL-3.0-only", LicenseClass.AGPL),
        ("BUSL-1.1", LicenseClass.SOURCE_AVAILABLE),
        ("CC-BY-4.0", LicenseClass.DOCS_ATTRIBUTION),
        ("CC-BY-SA-4.0", LicenseClass.DOCS_SHARE_ALIKE),
        ("CC-BY-NC-4.0", LicenseClass.DOCS_RESTRICTED),
        ("MIT OR GPL-3.0-only", LicenseClass.PERMISSIVE),
        ("MIT AND GPL-3.0-only", LicenseClass.GPL),
        ("NOASSERTION", LicenseClass.UNKNOWN),
        ("Weird-License-9", LicenseClass.UNKNOWN),
        (None, LicenseClass.NONE),
    ],
)
def test_classify(spdx: str | None, expected: LicenseClass) -> None:
    assert classify(spdx) is expected


@pytest.mark.parametrize(
    ("spdx", "expected"),
    [
        ("MIT", MawClass.PERMISSIVE),
        ("MPL-2.0", MawClass.PERMISSIVE),
        ("GPL-3.0-only", MawClass.GPL),
        ("LGPL-2.1-only", MawClass.GPL),
        (None, MawClass.PROPRIETARY),
        ("BUSL-1.1", MawClass.PROPRIETARY),
    ],
)
def test_maw_class(spdx: str | None, expected: MawClass) -> None:
    assert maw_class(spdx) is expected


@pytest.mark.parametrize(
    ("prey", "maw", "mode", "human"),
    [
        ("MIT", "MIT", Mode.COPY, False),
        ("MIT", None, Mode.COPY, False),
        ("Apache-2.0", "MIT", Mode.COPY, False),
        ("Apache-2.0", "GPL-3.0-only", Mode.COPY, False),
        ("Apache-2.0", "GPL-2.0-only", Mode.IDEAS_ONLY, False),
        ("MPL-2.0", "MIT", Mode.COPY_FILE, False),
        ("LGPL-3.0-only", "MIT", Mode.REIMPLEMENT, False),
        ("LGPL-3.0-only", "GPL-3.0-only", Mode.COPY, False),
        ("GPL-3.0-only", "MIT", Mode.REIMPLEMENT, False),
        ("GPL-3.0-only", None, Mode.IDEAS_ONLY, False),
        ("GPL-3.0-only", "GPL-3.0-only", Mode.COPY, False),
        ("GPL-3.0-only", "GPL-2.0-only", Mode.IDEAS_ONLY, False),
        ("GPL-2.0-or-later", "GPL-3.0-only", Mode.COPY, False),
        ("GPL-2.0-only", "GPL-3.0-only", Mode.IDEAS_ONLY, False),
        ("GPL-3.0-only", "AGPL-3.0-only", Mode.COPY, False),
        ("AGPL-3.0-only", "GPL-3.0-only", Mode.IDEAS_ONLY, False),
        ("AGPL-3.0-only", "AGPL-3.0-only", Mode.COPY, False),
        ("BUSL-1.1", "MIT", Mode.IDEAS_ONLY, False),
        ("CC-BY-4.0", "MIT", Mode.COPY, False),
        ("CC-BY-SA-4.0", "MIT", Mode.COPY_FILE, False),
        ("CC-BY-NC-4.0", "MIT", Mode.IDEAS_ONLY, False),
        (None, "MIT", Mode.IDEAS_ONLY, True),
        # Read and not understood is not the same as "nothing is granted": a human decides.
        ("NOASSERTION", "MIT", Mode.HUMAN, True),
        ("Weird-License-9", "MIT", Mode.HUMAN, True),
    ],
)
def test_decide_matrix(prey: str | None, maw: str | None, mode: Mode, human: bool) -> None:
    verdict = decide(prey, maw)
    assert verdict.mode is mode
    assert verdict.human_review is human


def test_apache_requires_notice_and_share_alike_flag() -> None:
    assert decide("Apache-2.0", "MIT").notice_required
    assert decide("CC-BY-SA-4.0", "MIT").share_alike
    assert not decide("MIT", "MIT").notice_required


def test_modes_by_maw_class() -> None:
    assert modes_by_maw_class("MIT") == {
        "permissive": "COPY",
        "gpl": "COPY",
        "proprietary": "COPY",
    }
    assert modes_by_maw_class("GPL-3.0-only") == {
        "permissive": "REIMPLEMENT",
        "gpl": "COPY",
        "proprietary": "IDEAS_ONLY",
    }
    assert decide_for_class("MPL-2.0", MawClass.PROPRIETARY).mode is Mode.COPY_FILE


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("package.json", '{"license": "MIT"}', "MIT"),
        ("package.json", '{"license": {"type": "BSD-3-Clause"}}', "BSD-3-Clause"),
        ("package.json", '{"name": "x"}', None),
        ("pyproject.toml", '[project]\nlicense = "Apache-2.0"\n', "Apache-2.0"),
        ("pyproject.toml", '[project]\nlicense = {text = "MIT"}\n', "MIT"),
        (
            "pyproject.toml",
            '[project]\nclassifiers = ["License :: OSI Approved :: MIT License"]\n',
            "MIT",
        ),
        (
            "Foo.csproj",
            "<Project><PackageLicenseExpression>MPL-2.0</PackageLicenseExpression></Project>",
            "MPL-2.0",
        ),
        ("Cargo.toml", '[package]\nlicense = "MIT OR Apache-2.0"\n', "MIT OR Apache-2.0"),
        ("package.json", "not json", None),
    ],
)
def test_manifest_license(name: str, text: str, expected: str | None) -> None:
    assert manifest_license(name, text)[0] == expected


def test_detect_in_repo_root_license_wins_and_reports_conflicts(tmp_path: Path) -> None:
    write_tree(tmp_path, {"LICENSE": MIT_TEXT, "package.json": '{"license": "Apache-2.0"}'})
    findings = detect_in_repo(tmp_path, [], manifests=["package.json"])
    assert findings.spdx == "MIT"
    assert findings.license_files == ["LICENSE"]
    assert findings.conflicts and "Apache-2.0" in findings.conflicts[0]
    assert findings.human_review


def test_detect_in_repo_dual_license_files(tmp_path: Path) -> None:
    write_tree(tmp_path, {"LICENSE-MIT": MIT_TEXT, "LICENSE-APACHE": APACHE_TEXT})
    findings = detect_in_repo(tmp_path, [])
    assert findings.spdx == "Apache-2.0 OR MIT"
    assert not findings.human_review


def test_detect_in_repo_falls_back_to_manifest_and_headers(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "pyproject.toml": '[project]\nlicense = "MIT"\n',
            "src/a.py": "# SPDX-License-Identifier: MIT\n",
            "src/vendored.py": "# SPDX-License-Identifier: GPL-3.0-only\n",
            "vendor/lib/LICENSE": BSD3_TEXT,
        },
    )
    findings = detect_in_repo(
        tmp_path,
        ["src/a.py", "src/vendored.py"],
        manifests=["pyproject.toml"],
        nested_license_files=["vendor/lib/LICENSE"],
    )
    assert findings.spdx == "MIT"
    assert findings.confidence == pytest.approx(0.7)
    assert findings.header_counts == {"MIT": 1, "GPL-3.0-only": 1}
    kinds = {(e["kind"], e["spdx"]) for e in findings.exceptions}
    assert ("header", "GPL-3.0-only") in kinds
    assert ("nested-license", "BSD-3-Clause") in kinds


def test_detect_in_repo_without_any_license(tmp_path: Path) -> None:
    write_tree(tmp_path, {"README.md": "# nothing here\n"})
    findings = detect_in_repo(tmp_path, [])
    assert findings.spdx is None
    assert findings.human_review
    assert any("no license" in note for note in findings.notes)


# --- the four situations that used to share one NOASSERTION ---------------------------------
#
# Every case below is a repository the crab was asked to eat, and every one of them used to end
# as "unrecognised license" or "no license found". They are four different problems.


def test_a_license_file_extension_is_not_a_license_name() -> None:
    """`LICENSE.md` used to be read as a license called "md" with 0.6 confidence."""
    assert license_name_from_file("LICENSE.md") is None
    assert license_name_from_file("LICENSE.txt") is None
    assert license_name_from_file("LICENSE-APACHE") == "Apache-2.0"
    assert license_name_from_file("apache-2.0.LICENSE") == "Apache-2.0"


def test_an_unreadable_license_file_asks_a_human_instead_of_inventing_one(tmp_path: Path) -> None:
    """n8n's LICENSE.md: a real file, a real license, no text any signature matches."""
    write_tree(tmp_path, {"LICENSE.md": "# License\n\nAsk us. Seriously, write an email.\n"})
    findings = detect_in_repo(tmp_path, [])
    assert findings.resolution == "unreadable"
    assert findings.spdx == "NOASSERTION"
    assert decide(findings.spdx, "MIT").mode is Mode.HUMAN
    assert "LICENSE.md" in " ".join(findings.notes)


def test_two_license_files_offered_as_a_choice_stay_a_choice(tmp_path: Path) -> None:
    """structlog: LICENSE-APACHE next to LICENSE-MIT is dual licensing, and MIT is enough."""
    write_tree(tmp_path, {"LICENSE-APACHE": APACHE_TEXT, "LICENSE-MIT": MIT_TEXT})
    findings = detect_in_repo(tmp_path, [])
    assert findings.resolution == "dual"
    assert findings.spdx == "Apache-2.0 OR MIT"
    verdict = decide(findings.spdx, "MIT")
    assert verdict.mode is Mode.COPY and not verdict.notice_required


def test_license_files_named_after_their_license_are_found_and_are_not_a_choice(
    tmp_path: Path,
) -> None:
    """scancode: `apache-2.0.LICENSE` + `cc-by-4.0.LICENSE`. Both were invisible, and they are
    not alternatives: the code is Apache-2.0 and the license data is CC-BY-4.0."""
    write_tree(
        tmp_path,
        {"apache-2.0.LICENSE": APACHE_TEXT, "cc-by-4.0.LICENSE": "Attribution 4.0 International\n"},
    )
    findings = detect_in_repo(tmp_path, [])
    assert findings.license_files == ["apache-2.0.LICENSE", "cc-by-4.0.LICENSE"]
    assert findings.resolution == "split"
    assert findings.spdx == "Apache-2.0 AND CC-BY-4.0"
    assert decide(findings.spdx, "MIT").notice_required, "attribution survives the combination"


def test_a_licenses_directory_is_read(tmp_path: Path) -> None:
    write_tree(tmp_path, {"LICENSES/MIT.txt": MIT_TEXT, "LICENSES/Apache-2.0.txt": APACHE_TEXT})
    findings = detect_in_repo(tmp_path, [])
    assert sorted(findings.license_files) == ["LICENSES/Apache-2.0.txt", "LICENSES/MIT.txt"]
    assert findings.resolution == "split"
    assert set(findings.candidates) == {"MIT", "Apache-2.0"}


def test_one_file_that_licenses_different_parts_differently(tmp_path: Path) -> None:
    """The modelcontextprotocol shape: one LICENSE covering a relicensing transition."""
    write_tree(
        tmp_path,
        {
            "LICENSE": (
                "The project is undergoing a licensing transition from the MIT License to the "
                "Apache License, Version 2.0. Documentation contributions are licensed under "
                "CC-BY-4.0. Contributions whose authors have not granted permission remain "
                "licensed under the MIT License.\n" + APACHE_TEXT
            )
        },
    )
    findings = detect_in_repo(tmp_path, [])
    assert findings.resolution == "split"
    assert findings.candidates == ["Apache-2.0", "CC-BY-4.0", "MIT"]
    verdict = decide(findings.spdx, "MIT")
    assert verdict.mode is Mode.COPY, "every part of it is permissive; no human is needed"
    assert verdict.notice_required, "but the strictest of the three still governs the whole"


def test_a_monorepo_licensed_per_package_names_the_packages(tmp_path: Path) -> None:
    """mui-x: nothing at the root, `x-data-grid` is MIT and `x-data-grid-pro` is commercial."""
    write_tree(
        tmp_path,
        {
            "package.json": '{"name": "monorepo", "private": true}\n',
            "packages/grid/LICENSE": MIT_TEXT,
            "packages/grid-pro/LICENSE": PROPRIETARY_TEXT,
        },
    )
    findings = detect_in_repo(
        tmp_path,
        [],
        manifests=["package.json"],
        nested_license_files=["packages/grid/LICENSE", "packages/grid-pro/LICENSE"],
    )
    assert findings.resolution == "per-path"
    assert decide(findings.spdx, "MIT").mode is Mode.HUMAN
    note = " ".join(findings.notes)
    assert "packages/grid/LICENSE (MIT)" in note
    assert {item["spdx"] for item in findings.exceptions} == {"MIT", "LicenseRef-Proprietary"}


# --- relationship: a license governs strangers ----------------------------------------------


def test_own_repositories_are_copyable_whatever_their_license_says() -> None:
    """A maintainer eating their own unlicensed repository is not a licensing question."""
    verdict = decide(None, "MIT", relationship=Relationship.OWN)
    assert verdict.mode is Mode.COPY
    assert not verdict.human_review
    assert "same owner" in verdict.reason


def test_own_but_copyleft_still_asks_because_the_code_may_not_all_be_ours() -> None:
    verdict = decide("GPL-3.0-only", "MIT", relationship=Relationship.OWN)
    assert verdict.mode is Mode.COPY
    assert verdict.human_review, "owning the repository does not launder someone else's code"


def test_bypass_says_so_on_every_card() -> None:
    verdict = decide("BUSL-1.1", "MIT", relationship=Relationship.BYPASS)
    assert verdict.mode is Mode.COPY
    assert verdict.human_review
    assert "bypass" in verdict.reason


def test_a_foreign_prey_is_unaffected_by_the_new_parameter() -> None:
    assert decide("GPL-3.0-only", "MIT") == decide(
        "GPL-3.0-only", "MIT", relationship=Relationship.FOREIGN
    )
