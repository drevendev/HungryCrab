"""Complete preparation regressions for review findings #436, #437 and #439."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixture_builder import git
from test_attribution import _record
from test_copy_security import MIT, commit, prepare, repositories

from hungry_crab.attribution import ATTRIBUTIONS_PATH, CarriedText, TakenFile, parse_attributions
from hungry_crab.errors import CrabError
from hungry_crab.licensing import Mode, decide, detect_from_text, detect_in_repo
from hungry_crab.licensing.matrix import canonical_expression


@pytest.mark.parametrize(
    "notice",
    [
        "Copyright (c) 2026 Vendor. Redistribution requires separate permission.",
        "Copyright (c) 2026 Vendor; use requires an enterprise subscription.",
        "Copyright (c) 2026 Vendor. You cannot redistribute this software.",
        "Copyright (c) 2026 Vendor Redistribution requires permission",
        "Copyright (c) 2026 Vendor. Redistribution Requires Separate Permission.",
        "Copyright (c) 2026 Vendor Redistribution Requires Separate Permission",
        "Copyright (c) 2026 Vendor REDISTRIBUTION REQUIRES SEPARATE PERMISSION",
        "Copyright (c) 2026 Vendor You Must Ask The Authors",
        "Copyright (c) 2026 Vendor Distribution Requires Permission From The Authors",
        "Copyright (c) 2026 Vendor Must Pay Foundation",
        "Copyright (c) 2026 University of Example You Must Pay Authors",
    ],
)
@pytest.mark.parametrize("tag_only", [False, True])
def test_restrictive_copyright_metadata_never_authorizes_copy(
    tmp_path: Path, notice: str, tag_only: bool
) -> None:
    prey, maw = repositories(tmp_path)
    text = notice + "\n" + ("SPDX-License-Identifier: MIT\n" if tag_only else MIT)
    (prey / "LICENSE").write_text(text, encoding="utf-8")
    finding = detect_in_repo(prey, [])
    assert finding.human_review and decide(finding.spdx, "MIT").mode is Mode.HUMAN
    commit(prey)
    before = _files(maw)
    with pytest.raises(CrabError, match="root grant"):
        prepare(prey, maw)
    assert _files(maw) == before


@pytest.mark.parametrize(
    "notice",
    [
        "Copyright (C) 2010-2026, 2027 Alice Crab",
        "Copyright 2026 A. N. Other",
        "Copyright 2026 Example, Inc. <authors@example.test>",
        "Copyright 2026 Example, Inc. <authors@example.test>.",
        "COPYRIGHT (C) 2026 FREE SOFTWARE FOUNDATION, INC.",
        "Copyright 2026 The University of Example",
        "Copyright 2026 sindresorhus",
        "SPDX-FileCopyrightText: 2026 Original Authors",
        "\u00a9 2026 \u5f20\u4e09",
        "Copyright (c) 2026 Original Authors All rights reserved.",
        "Copyright (c) 2026 Alice Crab.",
        "/* Copyright (c) 2026 Alice Crab */",
        "<!-- Copyright (c) 2026 Alice Crab -->",
    ],
)
def test_bounded_copyright_metadata_retains_ordinary_mit_notices(notice: str) -> None:
    text = MIT.replace("Copyright (c) 2026 Original Authors", notice)
    assert detect_from_text(text)[0] == "MIT"
    assert detect_from_text(notice + "\nSPDX-License-Identifier: MIT\n")[0] == "MIT"


@pytest.mark.parametrize(
    "suffix",
    [
        ".",
        " All rights reserved.",
        ". All rights reserved.",
        " <authors@example.test>",
        " <authors@example.test>.",
        " All rights reserved. <authors@example.test>.",
        " <authors@example.test>. All rights reserved.",
    ],
)
def test_copyright_metadata_suffixes_do_not_change_the_grant(suffix: str) -> None:
    notice = "Copyright (c) 2026 Alice Crab" + suffix
    text = MIT.replace("Copyright (c) 2026 Original Authors", notice)
    assert detect_from_text(text)[0] == "MIT"
    assert detect_from_text(notice + "\nSPDX-License-Identifier: MIT\n")[0] == "MIT"
    restricted = "Copyright (c) 2026 Vendor. Redistribution requires permission" + suffix
    assert detect_from_text(restricted + "\n" + MIT)[0] is None


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _compound(prey: Path, maw: Path, expression: str, *, reuse: bool = False) -> None:
    # No per-file declaration should mask whether the root expression is verified correctly.
    for root in (prey, maw):
        (root / "src/example.py").write_text("VALUE = 1\n", encoding="utf-8")
    if reuse:
        (prey / "LICENSE").unlink()
        (prey / "LICENSES").mkdir()
        (prey / "LICENSES/MIT.txt").write_text(MIT, encoding="utf-8")
        (prey / "LICENSES/Apache-2.0.txt").write_text(
            "SPDX-License-Identifier: Apache-2.0\n", encoding="utf-8"
        )
        (prey / "package.json").write_text(json.dumps({"license": expression}), encoding="utf-8")
    else:
        (prey / "LICENSE").write_text(f"SPDX-License-Identifier: {expression}\n", encoding="utf-8")
        (prey / "LICENSE-MIT").write_text(MIT, encoding="utf-8")
        (prey / "LICENSE-APACHE").write_text(
            "SPDX-License-Identifier: Apache-2.0\n", encoding="utf-8"
        )


@pytest.mark.parametrize("reuse", [False, True])
def test_an_immutable_and_grant_cannot_be_replaced_by_an_or_receipt(
    tmp_path: Path, reuse: bool
) -> None:
    prey, maw = repositories(tmp_path)
    _compound(prey, maw, "MIT AND Apache-2.0", reuse=reuse)
    commit(prey)
    before = _files(maw)
    with pytest.raises(CrabError, match="root grant contradicts"):
        prepare(prey, maw, spdx="MIT OR Apache-2.0")
    assert _files(maw) == before


@pytest.mark.parametrize("operator", ["AND", "OR"])
@pytest.mark.parametrize("reuse", [False, True])
def test_proven_compound_grants_keep_their_operators_and_selected_notices(
    tmp_path: Path, operator: str, reuse: bool
) -> None:
    prey, maw = repositories(tmp_path)
    _compound(prey, maw, f"(MIT {operator} Apache-2.0)", reuse=reuse)
    if not reuse:
        finding = detect_in_repo(prey, [])
        assert canonical_expression(finding.spdx) == canonical_expression(
            f"MIT {operator} Apache-2.0"
        )
    commit(prey)
    prepared = prepare(prey, maw, spdx=f"Apache-2.0 {operator} MIT")
    record = parse_attributions(
        next(f.content for f in prepared.files if f.path == ATTRIBUTIONS_PATH)
    )[0]
    assert record.obligation.kind == ("combined" if operator == "AND" else "copyright-notice")
    carried = "\n".join(text.text for text in record.texts)
    assert "Permission is hereby granted" in carried
    if operator == "AND":
        assert "Apache-2.0" in carried and "NOTICE" in record.obligation.text
    else:
        assert not any("APACHE" in text.path.upper() for text in record.texts)


def _upstream(prey: Path, *, depth: int, damaged: int | None) -> None:
    records = []
    for hop in range(depth + 1):
        row = _record(
            sha=f"{hop + 1:040x}",
            label=f"example/ancestor-{hop}",
            taken=(TakenFile("src/example.py", "src/example.py", False),),
        ).to_dict()
        text = MIT.replace("Original Authors", f"Ancestor {hop} Authors")
        row["texts"] = [] if hop == damaged else [CarriedText("LICENSE", text).to_dict()]
        if records:
            records[-1]["upstream"] = [row]
        records.append(row)
    (prey / ".crab").mkdir()
    (prey / ATTRIBUTIONS_PATH).write_text(
        json.dumps({"schema": "hungry-crab.attributions/1", "receipts": [records[0]]}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("depth", [0, 1, 3, 7])
def test_every_retained_ancestor_must_carry_its_required_notice(tmp_path: Path, depth: int) -> None:
    prey, maw = repositories(tmp_path)
    _upstream(prey, depth=depth, damaged=depth)
    commit(prey)
    before = _files(maw)
    with pytest.raises(CrabError) as raised:
        prepare(prey, maw)
    assert "lost its licence notices" in (raised.value.hint or "")
    assert _files(maw) == before


def test_complete_upstream_chain_keeps_distinct_copyright_notices(tmp_path: Path) -> None:
    prey, maw = repositories(tmp_path)
    _upstream(prey, depth=3, damaged=None)
    commit(prey)
    prepared = prepare(prey, maw)
    notice = next(f.content for f in prepared.files if f.path == "THIRD_PARTY_NOTICES.md")
    for hop in range(4):
        assert f"Ancestor {hop} Authors" in notice and f"example/ancestor-{hop}" in notice


def test_verified_own_relationship_cannot_waive_nested_third_party_notices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prey, maw = repositories(tmp_path)
    _upstream(prey, depth=1, damaged=1)
    commit(prey)
    git(maw, "init", "-b", "main")
    git(maw, "remote", "add", "origin", "https://github.com/example/maw.git")
    monkeypatch.setattr(
        "hungry_crab.attribution.client_for",
        lambda *_args, **_kwargs: SimpleNamespace(
            repo=lambda _: {"full_name": "example/original", "fork": False}
        ),
    )
    from test_attribution import _card, _payload

    from hungry_crab.attribution import GitSourceReader, prepare_copy_pull_request

    source = {
        "label": "example/original",
        "url": "https://github.com/example/original",
        "sha": git(prey, "rev-parse", "HEAD").strip(),
        "license": "MIT",
    }
    card = _card()
    payload = _payload(
        source=source,
        taken=[{"maw_path": "src/example.py", "prey_path": "src/example.py", "verbatim": True}],
    )
    with pytest.raises(CrabError) as raised:
        prepare_copy_pull_request(
            card,
            {
                "prey": {**source, "repository": {"fork": False}},
                "verdict": {"relationship": "own", "mode": "COPY"},
            },
            payload,
            maw,
            title="Reviewed copy",
            body=f"<!-- {card.id} -->\n",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=GitSourceReader(prey),
        )
    assert "lost its licence notices" in (raised.value.hint or "")
