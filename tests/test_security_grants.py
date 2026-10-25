"""MIT grant establishment and bounded expression negative controls for the hardening batch."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import write_tree

from hungry_crab.attribution import obligation_for
from hungry_crab.licensing import Mode, classify, decide, detect_from_text, detect_in_repo
from hungry_crab.licensing.detect import find_spdx_identifier
from hungry_crab.licensing.matrix import LicenseClass, canonical_expression, selected_terms

MIT = (Path(__file__).parents[1] / "LICENSE").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "rider",
    [
        "The Software shall be used for Good, not Evil.",
        "Commercial use and model training are prohibited.",
        "An enterprise subscription is required for redistribution.",
        "Additional terms: the software may only be used internally.",
    ],
)
def test_mit_with_extra_operative_terms_never_grants_copy(rider: str, tmp_path: Path) -> None:
    root = write_tree(tmp_path, {"LICENSE": MIT + rider, "package.json": '{"license":"MIT"}'})
    result = detect_in_repo(root, [], manifests=["package.json"], api_spdx="MIT")
    assert result.spdx == "NOASSERTION" and result.human_review
    assert decide(result.spdx, "MIT").mode is Mode.HUMAN


def test_a_bundled_mit_notice_does_not_replace_proprietary_terms() -> None:
    text = "Enterprise license: use requires permission. Bundled component notice follows.\n" + MIT
    assert detect_from_text(text)[0] is None


@pytest.mark.parametrize(
    "restriction",
    [
        "Do not redistribute.",
        "Redistribution is forbidden.",
        "Use requires permission from the vendor.",
    ],
)
def test_a_copyright_prefix_cannot_hide_terms_before_a_bundled_mit_notice(restriction: str) -> None:
    text = f"Copyright (c) 2026 Vendor\n{restriction}\n\n{MIT}"
    assert detect_from_text(text)[0] is None


@pytest.mark.parametrize("title", ["The MIT License (MIT)", "Expat License", "MIT"])
def test_ordinary_mit_title_and_copyright_variations_remain_recognized(title: str) -> None:
    text = MIT.replace("MIT License", title).replace("Copyright (c)", "Copyright \u00a9")
    assert detect_from_text(text)[0] == "MIT"


@pytest.mark.parametrize("name", ["LICENSE", "LICENSE-MIT", "LICENSE.txt"])
def test_unreadable_root_cannot_be_replaced_by_weaker_evidence(name: str, tmp_path: Path) -> None:
    root = write_tree(
        tmp_path, {name: "Custom terms: restricted use.", "package.json": '{"license":"ISC"}'}
    )
    result = detect_in_repo(root, [], manifests=["package.json"], api_spdx="MIT")
    assert decide(result.spdx, "MIT").mode is Mode.HUMAN


def test_absent_root_and_readable_root_pointer_controls(tmp_path: Path) -> None:
    root = write_tree(tmp_path, {"package.json": '{"license":"MIT"}'})
    assert detect_in_repo(root, [], manifests=["package.json"]).spdx == "MIT"
    write_tree(root, {"LICENSE": "See LICENSES/MIT.txt", "LICENSES/MIT.txt": MIT})
    assert detect_in_repo(root, [], manifests=["package.json"]).spdx == "MIT"


@pytest.mark.parametrize("foreign", ["Business Source License 1.1", "Custom enterprise terms"])
def test_named_files_do_not_prove_an_open_core_choice(foreign: str, tmp_path: Path) -> None:
    write_tree(tmp_path, {"LICENSE-MIT": MIT, "LICENSE-ENTERPRISE": foreign})
    result = detect_in_repo(tmp_path, [])
    assert result.human_review or decide(result.spdx, "MIT").mode is not Mode.COPY


@pytest.mark.parametrize("prefix", ["", "// ", "# ", "/* ", "<!-- "])
def test_spdx_tags_preserve_the_whole_parenthesized_expression(prefix: str) -> None:
    suffix = " */" if prefix == "/* " else " -->" if prefix == "<!-- " else ""
    expression = "(MIT OR Apache-2.0) AND LicenseRef-Custom"
    assert (
        find_spdx_identifier(f"{prefix}SPDX-License-Identifier: {expression}{suffix}") == expression
    )
    assert decide(expression, "MIT").mode is Mode.HUMAN
    assert find_spdx_identifier('Example: "SPDX-License-Identifier: MIT"') is None
    assert find_spdx_identifier("# SPDX-License-Identifier: MIT AND (") == "NOASSERTION"


@pytest.mark.parametrize(
    "unknown", ["MPL-9.0", "GPL-4.0-only", "AGPL-bogus", "CC-BY-bogus", "GFDL-9.0"]
)
def test_unsupported_leaves_stay_unsupported_in_mit_expressions(unknown: str) -> None:
    assert classify(unknown) is LicenseClass.UNKNOWN
    assert decide(f"MIT AND ({unknown} OR {unknown})", "MIT").mode is Mode.HUMAN
    assert decide(f"MIT OR {unknown}", "MIT").mode is Mode.COPY
    assert decide("MIT", unknown).mode is Mode.HUMAN


def test_equivalent_choices_do_not_create_conflicts_or_header_exceptions(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        {
            "LICENSE-MIT": MIT,
            "LICENSE-APACHE": "SPDX-License-Identifier: Apache-2.0\n",
            "package.json": '{"license":"MIT OR Apache-2.0"}',
            "core.py": "# SPDX-License-Identifier: MIT OR Apache-2.0\n",
        },
    )
    result = detect_in_repo(root, ["core.py"], manifests=["package.json"])
    assert not result.conflicts and not result.exceptions and not result.human_review
    assert canonical_expression("(MIT OR Apache-2.0) AND CC-BY-4.0") == canonical_expression(
        "CC-BY-4.0 AND (Apache-2.0 OR MIT)"
    )
    assert canonical_expression("MIT OR Apache-2.0") != canonical_expression("MIT AND Apache-2.0")


@pytest.mark.parametrize(
    "wording",
    [
        "dual-licensed under MIT or Apache-2.0",
        "licensed under either the MIT License or Apache-2.0",
        "MIT License or Apache-2.0, at your option",
    ],
)
def test_established_mit_apache_choice_wording(wording: str) -> None:
    assert detect_from_text(wording)[0] == "MIT OR Apache-2.0"


def test_an_enclosing_custom_clause_cannot_inherit_an_embedded_choice() -> None:
    text = (
        "This project is dual-licensed under MIT or Apache-2.0. "
        "Redistribution requires separate permission."
    )
    assert detect_from_text(text)[0] is None


def test_compound_duties_and_actual_or_choice_are_structured() -> None:
    verdict = decide("MIT AND Apache-2.0", "MIT")
    assert verdict.notice_required
    assert set(verdict.obligations) >= {"notice-file", "copyright-notice"}
    assert obligation_for("MIT AND Apache-2.0", verdict.to_dict()).kind == "combined"
    assert selected_terms("Apache-2.0 OR MIT") == ("MIT",)
    assert obligation_for("Apache-2.0 OR MIT", {}).kind == "copyright-notice"


def test_asset_texts_do_not_invent_or_replace_a_root_grant(tmp_path: Path) -> None:
    write_tree(tmp_path, {"licenses/font-OFL-1.1.txt": "SIL font terms"})
    result = detect_in_repo(tmp_path, [])
    assert result.spdx != "MIT" and result.resolution == "per-path"
    write_tree(tmp_path, {"LICENSE": MIT})
    result = detect_in_repo(tmp_path, [])
    assert result.spdx == "MIT" and result.exceptions and result.human_review


def test_hostile_expression_nesting_fails_closed() -> None:
    assert decide("(" * 10000 + "MIT" + ")" * 10000, "MIT").mode is Mode.HUMAN


def test_a_gnu_mention_does_not_change_the_operative_grant() -> None:
    from test_licensing import FSF_PREAMBLE

    text = "GNU GENERAL PUBLIC LICENSE\nVersion 2\n" + FSF_PREAMBLE
    text += "The GNU Lesser General Public License is a different license.\n"
    assert detect_from_text(text)[0] == "GPL-2.0-only"
    assert detect_from_text("A discussion of GNU General Public License, version 3.")[0] is None


@pytest.mark.parametrize(
    "text",
    [
        'SPDX-License-Identifier: "MIT"',
        "SPDX-License-Identifier: MIT\nEnterprise terms require separate permission.",
        "SPDX-License-Identifier: MIT\nSPDX-License-Identifier: GPL-3.0-only",
        (
            "Apache License\nTERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n"
            "You may not offer this software as a hosted service."
        ),
    ],
)
def test_quoted_contradictory_and_restrictive_grants_cannot_authorize_copy(text: str) -> None:
    assert decide(detect_from_text(text)[0] or "NOASSERTION", "MIT").mode is not Mode.COPY
