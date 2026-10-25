"""Reviewed MIT COPY preparation on real synthetic Git histories, including two-hop origin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixture_builder import git

from hungry_crab.attribution import (
    ATTRIBUTIONS_PATH,
    GitSourceReader,
    parse_attributions,
    prepare_copy_pull_request,
)
from hungry_crab.errors import CrabError
from hungry_crab.nutrients import Candidate
from hungry_crab.pr_publication import PreparedPullRequest

MIT = (
    (Path(__file__).parents[1] / "LICENSE")
    .read_text(encoding="utf-8")
    .replace("Dreven", "Original Authors")
)
CODE = "# Copyright (c) 2026 Original Authors\n# SPDX-License-Identifier: MIT\nVALUE = 1\n"


def commit(root: Path) -> str:
    git(root, "init", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-m", "test: synthetic licensed source")
    return git(root, "rev-parse", "HEAD").strip()


def prepare(
    prey: Path, maw: Path, *, label: str = "example/original", spdx: str = "MIT"
) -> PreparedPullRequest:
    sha = git(prey, "rev-parse", "HEAD").strip()
    source = {"label": label, "url": f"https://github.com/{label}", "sha": sha, "license": spdx}
    card = Candidate(
        "tests",
        "tests.unit",
        "Copy example",
        "Reviewed example",
        origin="licensed",
        license_mode="COPY",
        serve_as="pr",
    )
    payload = {
        "version": 1,
        "kind": "materialization",
        "nutrient_id": card.id,
        "source": source,
        "taken": [{"maw_path": "src/example.py", "prey_path": "src/example.py", "verbatim": False}],
        "summary": "Adapted the reviewed synthetic example",
        "checks": ["reviewed source bytes"],
    }
    return prepare_copy_pull_request(
        card,
        {"prey": source, "verdict": {"mode": "COPY"}},
        json.dumps(payload),
        maw,
        title="Reviewed example",
        body=f"<!-- {card.id} -->\n",
        attribution_file="THIRD_PARTY_NOTICES.md",
        source_reader=GitSourceReader(prey),
    )


def repositories(tmp_path: Path) -> tuple[Path, Path]:
    prey, maw = tmp_path / "prey", tmp_path / "maw"
    for root in (prey, maw):
        (root / "src").mkdir(parents=True)
        (root / "src" / "example.py").write_text(CODE, encoding="utf-8")
        (root / "LICENSE").write_text(MIT, encoding="utf-8")
    return prey, maw


def test_two_hop_receipt_keeps_original_source_terms_and_notice(tmp_path: Path) -> None:
    prey, intermediary = repositories(tmp_path)
    commit(prey)
    first = prepare(prey, intermediary)
    for file in first.files:
        target = intermediary / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8")
    commit(intermediary)
    destination = tmp_path / "destination"
    (destination / "src").mkdir(parents=True)
    (destination / "src" / "example.py").write_text(CODE.replace("1", "2"), encoding="utf-8")
    second = prepare(intermediary, destination, label="example/intermediary")
    files = {file.path: file.content for file in second.files}
    record = parse_attributions(files[ATTRIBUTIONS_PATH])[0]
    assert record.source.label == "example/intermediary"
    assert record.upstream[0].source.label == "example/original"
    notice = files["THIRD_PARTY_NOTICES.md"]
    assert "example/original" in notice and "example/intermediary" in notice
    assert "Original Authors" in notice and "Permission is hereby granted" in notice
    assert parse_attributions(files[ATTRIBUTIONS_PATH])[0].to_dict() == record.to_dict()


@pytest.mark.parametrize("damage", ["copyright", "spdx", "nested-manifest", "case", "trailing-dot"])
def test_notice_package_and_filesystem_alias_refusals_have_no_maw_effects(
    tmp_path: Path, damage: str
) -> None:
    prey, maw = repositories(tmp_path)
    if damage == "nested-manifest":
        (prey / "src" / "package.json").write_text(
            '{"license":"LicenseRef-Restricted"}', encoding="utf-8"
        )
    if damage in {"copyright", "spdx"}:
        needle = "Copyright" if damage == "copyright" else "SPDX-License-Identifier"
        (maw / "src" / "example.py").write_text(
            "\n".join(line for line in CODE.splitlines() if needle not in line), encoding="utf-8"
        )
    if damage == "case":
        (maw / "src" / "example.py").rename(maw / "src" / "Example.py")
    commit(prey)
    before = {path.relative_to(maw): path.read_bytes() for path in maw.rglob("*") if path.is_file()}
    if damage == "trailing-dot":
        from test_attribution import _payload

        from hungry_crab.attribution import load_materialization_receipt

        with pytest.raises(CrabError):
            load_materialization_receipt(
                _payload(
                    taken=[
                        {
                            "maw_path": "src/example.py.",
                            "prey_path": "src/example.py",
                            "verbatim": False,
                        }
                    ]
                )
            )
    else:
        with pytest.raises(CrabError):
            prepare(prey, maw)
    assert {
        path.relative_to(maw): path.read_bytes() for path in maw.rglob("*") if path.is_file()
    } == before


def test_reuse_mit_text_is_carried_into_the_prepared_notice(tmp_path: Path) -> None:
    prey, maw = repositories(tmp_path)
    (prey / "LICENSE").unlink()
    (prey / "LICENSES").mkdir()
    (prey / "LICENSES" / "MIT.txt").write_text(MIT, encoding="utf-8")
    commit(prey)
    prepared = prepare(prey, maw)
    record = parse_attributions(
        next(file.content for file in prepared.files if file.path == ATTRIBUTIONS_PATH)
    )[0]
    assert [text.path for text in record.texts] == ["LICENSES/MIT.txt"]


def test_an_old_mit_copy_menu_cannot_override_the_current_immutable_root_grant(
    tmp_path: Path,
) -> None:
    prey, maw = repositories(tmp_path)
    (prey / "LICENSE").write_text(MIT + "Commercial use is prohibited.\n", encoding="utf-8")
    commit(prey)
    with pytest.raises(CrabError, match="root grant is unreadable"):
        prepare(prey, maw)


@pytest.mark.parametrize("damage", ["cycle", "width", "depth"])
def test_upstream_provenance_has_bounded_acyclic_traversal(damage: str) -> None:
    import copy

    from test_attribution import _record

    row = _record().to_dict()
    if damage == "cycle":
        row["upstream"] = [copy.deepcopy(row)]
    elif damage == "width":
        row["upstream"] = [copy.deepcopy(row) for _ in range(33)]
    else:
        current = row
        for depth in range(10):
            child = copy.deepcopy(_record().to_dict())
            child["source"]["sha"] = f"{depth + 1:040x}"
            current["upstream"] = [child]
            current = child
    with pytest.raises(CrabError):
        parse_attributions(json.dumps({"schema": "hungry-crab.attributions/1", "receipts": [row]}))


def test_malformed_matching_upstream_is_refused_but_an_unrelated_path_is_unaffected(
    tmp_path: Path,
) -> None:
    prey, maw = repositories(tmp_path)
    (prey / ".crab").mkdir()
    receipt = {
        "schema": "hungry-crab.attributions/1",
        "receipts": [{"taken": [{"maw_path": "src/example.py"}], "mode": "COPY"}],
    }
    (prey / ATTRIBUTIONS_PATH).write_text(json.dumps(receipt), encoding="utf-8")
    commit(prey)
    with pytest.raises(CrabError, match="invalid materialization receipt"):
        prepare(prey, maw)
    receipt["receipts"][0]["taken"][0]["maw_path"] = "unrelated.py"
    (prey / ATTRIBUTIONS_PATH).write_text(json.dumps(receipt), encoding="utf-8")
    git(prey, "add", ".")
    git(prey, "commit", "-m", "test: unrelated attribution scope")
    assert prepare(prey, maw).files
