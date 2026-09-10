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
from hungry_crab.licensing.detect import (
    is_license_file_name,
    license_name_from_file,
    manifest_license,
)
from hungry_crab.licensing.matrix import fits_gpl_maw, governing_id, restricts_copying

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
APACHE_TEXT = (
    "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n\n"
    "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n"
)
# Every FSF licence text opens with this sentence, and the GNU signature asks for it (#86).
FSF_PREAMBLE = (
    "Everyone is permitted to copy and distribute verbatim copies\n"
    "of this license document, but changing it is not allowed.\n"
)
GPL3_TEXT = (
    "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n\n" + FSF_PREAMBLE + "\nPreamble\n"
)
GPL3_NOTICE = (
    "This program is free software: you can redistribute it and/or modify it under the terms of "
    "the GNU General Public License as published by the Free Software Foundation, either "
    "version 3 of the License, or (at your option) any later version.\n"
)
LGPL21_TEXT = "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 2.1, February 1999\n" + FSF_PREAMBLE
AGPL_TEXT = "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007\n" + FSF_PREAMBLE
MPL_TEXT = (
    "Mozilla Public License Version 2.0\n\n1. Definitions\n"
    "1.1. Contributor means each individual or legal entity that creates, contributes to "
    "the creation of, or owns Covered Software.\n"
)
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
        # Redundant brackets go; brackets that carry the meaning stay.
        ("(MIT OR Apache-2.0) AND CC-BY-4.0", "(MIT OR Apache-2.0) AND CC-BY-4.0"),
        ("MIT OR Apache-2.0 AND CC-BY-4.0", "MIT OR Apache-2.0 AND CC-BY-4.0"),
        ("(mit OR apache-2.0) AND cc-by-4.0", "(MIT OR Apache-2.0) AND CC-BY-4.0"),
        (
            "GPL-2.0-only WITH Classpath-exception-2.0",
            "GPL-2.0-only WITH Classpath-exception-2.0",
        ),
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
        # AND binds tighter than OR, so this one really is permissive.
        ("MIT OR Apache-2.0 AND CC-BY-4.0", LicenseClass.PERMISSIVE),
        # The brackets say otherwise: CC-BY applies whichever branch is taken.
        ("(MIT OR Apache-2.0) AND CC-BY-4.0", LicenseClass.DOCS_ATTRIBUTION),
        ("(MIT OR Apache-2.0) AND GPL-3.0-only", LicenseClass.GPL),
        ("(GPL-3.0-only OR MIT) AND BUSL-1.1", LicenseClass.SOURCE_AVAILABLE),
        # Unbalanced: unreadable, and unreadable is not permissive.
        ("(MIT OR Apache-2.0", LicenseClass.UNKNOWN),
        ("NOASSERTION", LicenseClass.UNKNOWN),
        ("Weird-License-9", LicenseClass.UNKNOWN),
        (None, LicenseClass.NONE),
    ],
)
def test_classify(spdx: str | None, expected: LicenseClass) -> None:
    assert classify(spdx) is expected


@pytest.mark.parametrize(
    ("prey", "mode", "notice"),
    [
        ("(MIT OR Apache-2.0) AND CC-BY-4.0", Mode.COPY, True),
        ("(MIT OR Apache-2.0) AND GPL-3.0-only", Mode.REIMPLEMENT, False),
        ("(GPL-3.0-only OR MIT) AND BUSL-1.1", Mode.IDEAS_ONLY, False),
        ("(MIT OR Apache-2.0", Mode.HUMAN, False),
    ],
)
def test_grouped_expression_is_not_flattened(prey: str, mode: Mode, notice: bool) -> None:
    """A bracketed term binds every branch of the choice beside it.

    Flattening the expression and re-reading it with SPDX precedence used to turn each of these
    into `COPY` or something close to it, which is the one direction the matrix must never fail
    in. See HungryCrab#57.
    """
    verdict = decide_for_class(prey, MawClass.PERMISSIVE, "MIT")
    assert verdict.mode is mode
    assert verdict.notice_required is notice


def test_governing_id_names_the_term_that_decided() -> None:
    assert governing_id("(MIT OR Apache-2.0) AND GPL-3.0-only") == "GPL-3.0-only"
    assert governing_id("MIT OR GPL-3.0-only") == "MIT"
    assert governing_id(None) is None


def test_fits_gpl_maw_reads_the_structure() -> None:
    assert fits_gpl_maw("GPL-2.0-only OR GPL-3.0-only", "GPL-3.0-only")
    assert not fits_gpl_maw("GPL-2.0-only AND GPL-3.0-only", "GPL-3.0-only")
    assert not fits_gpl_maw(None, "GPL-3.0-only")
    assert not fits_gpl_maw("(GPL-2.0-only", "GPL-3.0-only")


def test_a_copyleft_term_keeps_its_version_inside_an_expression() -> None:
    """The GPL branch compares versions, and only one term of an expression has one."""
    fits = decide_for_class("(MIT OR Apache-2.0) AND GPL-2.0-only", MawClass.GPL, "GPL-2.0-only")
    assert fits.mode is Mode.COPY
    clash = decide_for_class("(MIT OR Apache-2.0) AND GPL-3.0-only", MawClass.GPL, "GPL-2.0-only")
    assert clash.mode is Mode.IDEAS_ONLY


@pytest.mark.parametrize(
    ("prey", "expected"),
    [
        # OR is the recipient's choice: the compatible branch is available in either spelling.
        ("GPL-2.0-only OR GPL-2.0-or-later", Mode.COPY),
        ("GPL-2.0-or-later OR GPL-2.0-only", Mode.COPY),
        # AND binds both terms, so the incompatible one decides, in either spelling.
        ("GPL-2.0-or-later AND GPL-2.0-only", Mode.IDEAS_ONLY),
        ("GPL-2.0-only AND GPL-2.0-or-later", Mode.IDEAS_ONLY),
    ],
)
def test_copyleft_compatibility_does_not_depend_on_operand_order(prey: str, expected: Mode) -> None:
    """Two terms of the same class used to hand the answer to whichever came first.

    `min`/`max` keep the first element of a tie, so the reduction to one identifier made
    `GPL-2.0-or-later AND GPL-2.0-only` answer as though the `-or-later` term were the only one —
    more permissive than the expression. Reported in review of #57.
    """
    assert decide_for_class(prey, MawClass.GPL, "GPL-3.0-only").mode is expected


def test_a_non_copyleft_term_does_not_block_a_gpl_maw() -> None:
    assert decide_for_class("MIT AND GPL-3.0-only", MawClass.GPL, "GPL-3.0-only").mode is Mode.COPY


@pytest.mark.parametrize(
    "prey",
    [
        "GPL-2.0-only OR BUSL-1.1",
        "GPL-2.0-only OR LicenseRef-Proprietary",
        "GPL-2.0-only OR Weird-License-9",
        "GPL-2.0-only OR CC-BY-NC-4.0",
    ],
)
def test_a_branch_of_a_choice_has_to_stand_on_its_own(prey: str) -> None:
    """A branch that is merely not copyleft is not therefore a way into a GPL maw.

    `OR` is a choice, and taking the BUSL branch of `GPL-2.0-only OR BUSL-1.1` leaves the code
    under BUSL. Answering "compatible" because the branch is not copyleft turned every
    source-available, proprietary or unreadable alternative into an escape hatch. Reported in
    review of #57.
    """
    assert decide_for_class(prey, MawClass.GPL, "GPL-3.0-only").mode is not Mode.COPY


def test_a_compatible_copyleft_branch_still_satisfies_the_choice() -> None:
    verdict = decide_for_class("GPL-2.0-only OR GPL-3.0-only", MawClass.GPL, "GPL-3.0-only")
    assert verdict.mode is Mode.COPY


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


# --- a licence is not the licence it mentions (#86) -------------------------------------------
#
# A source-available licence names the licence it converts to, and a custom licence names the
# licence the rest of the product is under. Both used to be read as the licence they named.


def _busl(change: str) -> str:
    """The stock BUSL-1.1 template, with its Change License filled in."""
    return (
        "Business Source License 1.1\n\nParameters\n\nLicensor: Acme Corp\n"
        f"Licensed Work: Widget\nChange Date: 2030-01-01\nChange License: {change}\n\n"
        "Terms\n\nThe Licensor hereby grants you the right to copy, modify, create derivative "
        "works, redistribute, and make non-production use of the Licensed Work.\n"
    )


FSL_ALV2_TEXT = (
    "Functional Source License, Version 1.1, ALv2 Future License\n\n"
    "Future License Grant: on the second anniversary of the date the Software was made "
    "available, the Software is also licensed under the Apache License, Version 2.0.\n"
)
FSL_MIT_TEXT = (
    "Functional Source License, Version 1.1, MIT Future License\n\n"
    "Future License Grant: on the second anniversary the Software is also licensed under MIT.\n"
)
COMMONS_CLAUSE_TEXT = (
    APACHE_TEXT + "\nCommons Clause License Condition v1.0\n\nThe Software is provided to you by "
    "the Licensor under the License, subject to the following condition.\n"
)
SUSTAINABLE_USE_TEXT = (
    "Sustainable Use License\nVersion 1.0\n\nAcceptance: by using the software, you agree to "
    "all of the terms and conditions below.\n"
)
POLYFORM_NC_TEXT = "PolyForm Noncommercial License 1.0.0\n<https://polyformproject.org/licenses/noncommercial/1.0.0>\n"
CUSTOM_MENTIONING_APACHE = (
    "TIMESCALE LICENSE AGREEMENT\n\nBACKGROUND. The Company makes some of its software available "
    "under the Apache License, Version 2.0, and the rest under this Agreement. Licensee may not "
    "provide the Timescale software to third parties as a hosted service.\n"
)
CUSTOM_MENTIONING_GPL = (
    "ACME COMMERCIAL LICENSE\n\nThis is not the GNU General Public License, version 3. Use of "
    "the software requires a paid subscription.\n"
)
APACHE_NOTICE = (
    'Licensed under the Apache License, Version 2.0 (the "License");\n'
    "you may not use this file except in compliance with the License.\n"
)
MPL_NOTICE = (
    "This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Sentry and CockroachDB name Apache, HashiCorp names MPL, MariaDB names the GPL.
        (_busl("Apache License, Version 2.0"), "BUSL-1.1"),
        (_busl("Mozilla Public License, version 2.0"), "BUSL-1.1"),
        (
            _busl(
                "Version 2 or later of the GNU General Public License as published by the "
                "Free Software Foundation"
            ),
            "BUSL-1.1",
        ),
        (FSL_ALV2_TEXT, "FSL-1.1-ALv2"),
        (FSL_MIT_TEXT, "FSL-1.1-MIT"),
        (COMMONS_CLAUSE_TEXT, "Commons-Clause"),
        (SUSTAINABLE_USE_TEXT, "LicenseRef-SustainableUse"),
        (POLYFORM_NC_TEXT, "PolyForm-Noncommercial-1.0.0"),
        # No signature of its own: unreadable, which asks a human, rather than Apache.
        (CUSTOM_MENTIONING_APACHE, None),
        (CUSTOM_MENTIONING_GPL, None),
        # And the licences themselves still read as themselves, in both of their usual forms.
        (APACHE_NOTICE, "Apache-2.0"),
        (MPL_NOTICE, "MPL-2.0"),
    ],
)
def test_a_licence_is_not_the_licence_it_mentions(text: str, expected: str | None) -> None:
    assert detect_from_text(text)[0] == expected


@pytest.mark.parametrize(
    "spdx",
    [
        "FSL-1.1-ALv2",
        "FSL-1.1-MIT",
        "PolyForm-Noncommercial-1.0.0",
        "PolyForm-Small-Business-1.0.0",
        "LicenseRef-PolyForm",
        "LicenseRef-SustainableUse",
    ],
)
def test_the_new_source_available_families_classify_as_such(spdx: str) -> None:
    assert classify(spdx) is LicenseClass.SOURCE_AVAILABLE


@pytest.mark.parametrize(
    "change", ["Apache License, Version 2.0", "Mozilla Public License, version 2.0"]
)
def test_the_stock_busl_template_is_never_offered_for_copying(change: str) -> None:
    """End to end, for the two templates that used to come out COPY and COPY_FILE."""
    spdx, _ = detect_from_text(_busl(change))
    assert decide(spdx, "MIT").mode is Mode.IDEAS_ONLY


def test_restricts_copying_draws_the_line_after_attribution() -> None:
    for spdx in ("MIT", "Apache-2.0", "CC-BY-4.0"):
        assert not restricts_copying(spdx), spdx
    for spdx in ("MPL-2.0", "LGPL-3.0-only", "GPL-3.0-only", "BUSL-1.1", "NOASSERTION"):
        assert restricts_copying(spdx), spdx


def test_a_split_the_root_declares_takes_in_the_files_that_say_where_it_falls(
    tmp_path: Path,
) -> None:
    """The Timescale shape: Apache at the root, a custom licence under `tsl/` that mentions Apache.

    The custom licence has no signature, so it is unreadable, and the root says the repository
    is split. It used to be read as Apache and the whole repository came out COPY.
    """
    write_tree(
        tmp_path,
        {
            "LICENSE": (
                "Source code in this repository is variously licensed under the Apache License, "
                "Version 2.0 (see LICENSE-APACHE) and the Timescale License "
                "(see tsl/LICENSE-TIMESCALE).\n"
            ),
            "LICENSE-APACHE": APACHE_TEXT,
            "tsl/LICENSE-TIMESCALE": CUSTOM_MENTIONING_APACHE,
        },
    )
    findings = detect_in_repo(tmp_path, [], nested_license_files=["tsl/LICENSE-TIMESCALE"])
    nested = {e["path"]: e["spdx"] for e in findings.exceptions if e["kind"] == "nested-license"}
    assert nested == {"tsl/LICENSE-TIMESCALE": "NOASSERTION"}, "the custom licence is not Apache"
    assert findings.resolution == "split"
    assert decide(findings.spdx, "MIT").mode is Mode.HUMAN
    assert findings.human_review
    assert "tsl/LICENSE-TIMESCALE (NOASSERTION)" in " ".join(findings.notes)


def test_a_declared_split_with_a_source_available_part_is_not_copyable(tmp_path: Path) -> None:
    """The open-core `ee/` shape, with the part it names readable."""
    write_tree(
        tmp_path,
        {
            "LICENSE": (
                "Portions of this software are licensed as follows: everything under ee/ is "
                "covered by ee/LICENSE, and the rest is under the Apache License, Version 2.0.\n"
            ),
            "LICENSE-APACHE": APACHE_TEXT,
            "ee/LICENSE": _busl("Apache License, Version 2.0"),
        },
    )
    findings = detect_in_repo(tmp_path, [], nested_license_files=["ee/LICENSE"])
    assert findings.resolution == "split"
    assert findings.spdx is not None and "BUSL-1.1" in findings.spdx
    assert decide(findings.spdx, "MIT").mode is Mode.IDEAS_ONLY


def test_a_nested_restriction_the_root_is_silent_about_is_flagged(tmp_path: Path) -> None:
    """Without a declared split the verdict stays the root's — and says it does not cover `ee/`."""
    write_tree(
        tmp_path, {"LICENSE": APACHE_TEXT, "ee/LICENSE": _busl("Apache License, Version 2.0")}
    )
    findings = detect_in_repo(tmp_path, [], nested_license_files=["ee/LICENSE"])
    assert {e["spdx"] for e in findings.exceptions} == {"BUSL-1.1"}
    assert findings.spdx == "Apache-2.0"
    assert findings.human_review
    assert "ee/LICENSE (BUSL-1.1)" in " ".join(findings.notes)


def test_a_nested_licence_that_only_asks_for_a_notice_is_not_a_restriction(
    tmp_path: Path,
) -> None:
    write_tree(tmp_path, {"LICENSE": MIT_TEXT, "vendor/x/LICENSE": APACHE_TEXT})
    findings = detect_in_repo(tmp_path, [], nested_license_files=["vendor/x/LICENSE"])
    assert {e["spdx"] for e in findings.exceptions} == {"Apache-2.0"}
    assert not findings.human_review


def test_an_unreadable_nested_licence_is_kept_but_not_flagged_without_a_split(
    tmp_path: Path,
) -> None:
    """Kept, because dropping it is the bug; not flagged, because a font licence is not news."""
    write_tree(
        tmp_path, {"LICENSE": MIT_TEXT, "assets/fonts/LICENSE": "Font licence. See the foundry.\n"}
    )
    findings = detect_in_repo(tmp_path, [], nested_license_files=["assets/fonts/LICENSE"])
    assert {e["spdx"] for e in findings.exceptions} == {"NOASSERTION"}
    assert findings.spdx == "MIT"
    assert not findings.human_review


def test_a_licence_outside_the_project_is_recorded_but_not_weighed(tmp_path: Path) -> None:
    """Found running the first version of this fix on real prey (#86).

    linguist's sample corpus carries `samples/Text/filenames/LICENSE.mysql`, which is test data,
    and the crab's own `.venv` carries a licence per installed package. Weighing them raised the
    review flag on both; dropping them would repeat the original bug.
    """
    write_tree(tmp_path, {"LICENSE": APACHE_TEXT, "vendor/lib/LICENSE": GPL3_TEXT})
    findings = detect_in_repo(tmp_path, [], vendored_license_files=["vendor/lib/LICENSE"])
    assert {(e["kind"], e["spdx"]) for e in findings.exceptions} == {
        ("vendored-license", "GPL-3.0-only")
    }
    assert findings.spdx == "Apache-2.0"
    assert not findings.human_review


def test_a_declared_split_does_not_take_in_licences_from_outside_the_project(
    tmp_path: Path,
) -> None:
    write_tree(
        tmp_path,
        {
            "LICENSE": (
                "Portions of this software are licensed as follows: the rest is under the "
                "Apache License, Version 2.0.\n"
            ),
            "LICENSE-APACHE": APACHE_TEXT,
            "vendor/lib/LICENSE": GPL3_TEXT,
        },
    )
    findings = detect_in_repo(tmp_path, [], vendored_license_files=["vendor/lib/LICENSE"])
    assert findings.spdx == "Apache-2.0"
    assert decide(findings.spdx, "MIT").mode is Mode.COPY


def test_the_licence_miner_weighs_the_project_and_only_records_the_rest(tmp_path: Path) -> None:
    """The same split, through the miner, where `counted` decides which kind a file is."""
    import json

    from hungry_crab.cache import Target
    from hungry_crab.digest import DigestOptions, run_digest

    repo = write_tree(
        tmp_path / "repo",
        {
            "LICENSE": APACHE_TEXT,
            "ee/LICENSE": _busl("Apache License, Version 2.0"),
            "vendor/lib/LICENSE": GPL3_TEXT,
            "src/app.py": "print('crab')\n",
        },
    )
    options = DigestOptions(out=tmp_path / "out", cache_root=tmp_path / "cache", miners=["license"])
    result = run_digest(Target(path=repo), options)
    data = json.loads((result.out_dir / "license.json").read_text(encoding="utf-8"))
    kinds = {(e["kind"], e["path"]) for e in data["exceptions"]}
    assert ("nested-license", "ee/LICENSE") in kinds
    assert ("vendored-license", "vendor/lib/LICENSE") in kinds
    assert data["human_review"], "ee/ is the project and it is source-available"
    assert "vendor/lib/LICENSE" not in " ".join(data["notes"])


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("LICENSE", True),
        ("LICENSE.md", True),
        ("LICENSE.txt", True),
        ("LICENSE-MIT", True),
        ("LICENSE.MIT", True),
        ("COPYING.LESSER", True),
        ("apache-2.0.LICENSE", True),
        ("LICENSE.mysql", True),
        ("license.py", False),
        ("license.json", False),
        ("license.ts", False),
        ("license.yml", False),
        ("x.license.json", False),
    ],
)
def test_a_licence_file_name_is_not_a_source_or_a_data_file(name: str, expected: bool) -> None:
    """The crab read its own `miners/license.py` and frozen `license.json` digests as licences."""
    assert is_license_file_name(name) is expected


def test_a_source_file_called_license_does_not_decide_a_split(tmp_path: Path) -> None:
    """The regression this fix would otherwise have introduced, through the miner.

    Once nested licence files join a split the root declares, anything the name matcher accepts
    can decide the verdict. A `license.py` read as an unreadable licence made the repository HUMAN.
    """
    import json

    from hungry_crab.cache import Target
    from hungry_crab.digest import DigestOptions, run_digest

    repo = write_tree(
        tmp_path / "repo",
        {
            "LICENSE": (
                "Portions of this software are licensed as follows: the rest is under the "
                "Apache License, Version 2.0.\n"
            ),
            "LICENSE-APACHE": APACHE_TEXT,
            "src/licensing/license.py": "def detect(text):\n    return None\n",
            "src/licensing/license.json": '{"spdx": "Apache-2.0"}\n',
        },
    )
    options = DigestOptions(out=tmp_path / "out", cache_root=tmp_path / "cache", miners=["license"])
    result = run_digest(Target(path=repo), options)
    data = json.loads((result.out_dir / "license.json").read_text(encoding="utf-8"))
    assert data["spdx"] == "Apache-2.0"
    assert not [e for e in data["exceptions"] if "licensing/" in e["path"]]
