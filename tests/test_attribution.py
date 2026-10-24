"""COPY pull requests: the materialization receipt, the attributions file and the notice.

The boundary under test is the one #70's thread settled on: a notice row exists only for
material the crab actually carried into the maw, recorded when the pull request was prepared,
never reconstructed from a ledger status or from whichever prey proposed the nutrient first.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from helpers import unfenced_text

from hungry_crab.attribution import (
    ATTRIBUTIONS_PATH,
    NOTICE_WIDTH,
    AttributionRecord,
    CarriedText,
    GitSourceReader,
    Obligation,
    SourceRef,
    TakenFile,
    add_attribution,
    base_receipts_kept,
    carried_texts,
    check_taken_licences,
    checked_attribution_file,
    clean_text,
    dump_attributions,
    load_attributions,
    load_materialization_receipt,
    obligation_for,
    parse_attributions,
    prepare_copy_pull_request,
    receipt_kind,
    render_notices,
    verify_sources,
)
from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.fetch.github import GitHubClient
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.nutrients import Candidate
from hungry_crab.pr_publication import (
    PreparedPullRequest,
    PullRequestPublication,
    nutrient_branch_name,
)
from hungry_crab.pr_serving import serve_cleanroom_pull_requests

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
SHA = "c490b45bc4d18f8968d527ec2d37b8af36eb4361"
OTHER_SHA = "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f"
PREY: dict[str, Any] = {
    "label": "pypa/pipx",
    "url": "https://github.com/pypa/pipx",
    "sha": SHA,
    "license": "MIT",
}
MENU: dict[str, Any] = {
    "prey": PREY,
    "verdict": {
        "mode": "COPY",
        "notice_required": False,
        "share_alike": False,
        "human_review": False,
        "reason": "permissive license: keep the copyright notice",
    },
}
WORKFLOW = "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"


def _receipt(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": 1,
        "kind": "materialization",
        "nutrient_id": "crab:ci:ci.cache",
        "source": dict(PREY),
        "taken": [
            {
                "maw_path": ".github/workflows/ci.yml",
                "prey_path": ".github/workflows/test.yml",
                "verbatim": False,
            }
        ],
        "summary": "carried the cache step over, adapted to uv",
        "checks": ["uv run pytest -q"],
    }
    base.update(overrides)
    return base


def _payload(**overrides: Any) -> str:
    return json.dumps(_receipt(**overrides))


def _card(mode: str = "COPY", key: str = "ci.cache") -> Candidate:
    return Candidate(
        "ci",
        key,
        "Cache the dependencies",
        "The prey caches.",
        origin="licensed",
        license_mode=mode,
        serve_as="pr",
    )


class FakePrey:
    """A source reader over an in-memory prey: (sha, path) -> content."""

    def __init__(
        self, files: dict[tuple[str, str], str], modes: dict[str, str] | None = None
    ) -> None:
        self.files = files
        self.modes = modes or {}

    def exists(self, sha: str, path: str) -> bool:
        return (sha, path) in self.files

    def read(self, sha: str, path: str) -> str | None:
        return self.files.get((sha, path))

    def entries(self, sha: str, directory: str = "") -> dict[str, str] | None:
        prefix = f"{directory}/" if directory else ""
        names = {
            path[len(prefix) :]: self.modes.get(path, "100644")
            for at, path in sorted(self.files)
            if at == sha and path.startswith(prefix) and "/" not in path[len(prefix) :]
        }
        folders = {path.rsplit("/", 1)[0] for at, path in self.files if at == sha and "/" in path}
        known = not directory or any(f == directory or f.startswith(prefix) for f in folders)
        return names if known else None


MIT_TEXT = """\
MIT License

Copyright (c) 2024 Prey Owner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software").

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
"""


def _prey() -> FakePrey:
    return FakePrey(
        {(SHA, ".github/workflows/test.yml"): "name: test\n", (SHA, "LICENSE"): MIT_TEXT}
    )


def _maw(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    for path, content in (files or {".github/workflows/ci.yml": WORKFLOW}).items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def _prepare(maw: Path, payload: str | None = None, **kwargs: Any) -> PreparedPullRequest:
    return prepare_copy_pull_request(
        _card(),
        MENU,
        payload or _payload(),
        maw,
        title="feat: cache the dependencies",
        body=kwargs.pop("body", "<!-- crab:ci:ci.cache -->\n\nCarry the cache step.\n"),
        attribution_file=kwargs.pop("attribution_file", "THIRD_PARTY_NOTICES.md"),
        source_reader=kwargs.pop("source_reader", _prey()),
        now=NOW,
        **kwargs,
    )


# --- the receipt -------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["COPY", "COPY_FILE"])
@pytest.mark.parametrize(
    "path", ["src/core.py", "tools/run.sh", "scripts/run.ps1", "tools/helper.lua"]
)
def test_strict_receipt_rechecks_taken_paths_before_reading_or_materializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, path: str
) -> None:
    monkeypatch.setattr(
        "hungry_crab.attribution.read_maw_text",
        lambda *_: pytest.fail("strict receipt read a maw file"),
    )
    payload = _payload(taken=[{"prey_path": path, "maw_path": path, "verbatim": True}])
    with pytest.raises(CrabError, match="strict maw policy"):
        prepare_copy_pull_request(
            _card(mode),
            {**MENU, "mode": "strict"},
            payload,
            tmp_path,
            title="feat: carry a configuration",
            body="Reviewed configuration",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=_prey(),
        )


@pytest.mark.parametrize("mode", ["COPY", "COPY_FILE"])
def test_strict_receipt_allows_verified_configuration_and_generated_notice(
    tmp_path: Path, mode: str
) -> None:
    _maw(tmp_path, {"ci/test.yml": "name: test\n"})
    payload = _payload(
        taken=[
            {"prey_path": ".github/workflows/test.yml", "maw_path": "ci/test.yml", "verbatim": True}
        ]
    )
    prepared = prepare_copy_pull_request(
        _card(mode),
        {**MENU, "mode": "strict"},
        payload,
        tmp_path,
        title="feat: carry a configuration",
        body="Reviewed configuration",
        attribution_file="THIRD_PARTY_NOTICES.md",
        source_reader=_prey(),
    )
    assert {file.path for file in prepared.files} == {
        "ci/test.yml",
        ATTRIBUTIONS_PATH,
        "THIRD_PARTY_NOTICES.md",
    }


def test_receipt_kind_tells_the_two_receipts_apart() -> None:
    assert receipt_kind(_payload()) == "materialization"
    assert receipt_kind('{"version": 1, "nutrient_id": "crab:ci:x"}') == "cleanroom"
    assert receipt_kind("not json") == "cleanroom"


def test_receipt_parses_strictly() -> None:
    receipt = load_materialization_receipt(_payload())
    assert receipt.nutrient_id == "crab:ci:ci.cache"
    assert receipt.source == SourceRef("pypa/pipx", "https://github.com/pypa/pipx", SHA, "MIT")
    assert receipt.taken == (
        TakenFile(".github/workflows/ci.yml", ".github/workflows/test.yml", False),
    )
    assert receipt.checks == ("uv run pytest -q",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"extra": 1},
        {"version": 2},
        {"kind": "cleanroom"},
        {"nutrient_id": "ci.cache"},
        {"source": {"label": "pypa/pipx", "sha": SHA}},
        {"source": {**PREY, "sha": "not-a-sha"}},
        {"source": {**PREY, "url": "http://github.com/pypa/pipx"}},
        {"taken": []},
        {"taken": [{"maw_path": "../ci.yml", "prey_path": "x", "verbatim": True}]},
        {"taken": [{"maw_path": "ci.yml", "prey_path": "/etc/passwd", "verbatim": True}]},
        {"taken": [{"maw_path": "ci.yml", "prey_path": "x", "verbatim": "yes"}]},
        {"taken": [{"maw_path": "ci.yml", "prey_path": "a\n\n# b.md", "verbatim": True}]},
        {"taken": [{"maw_path": "ci\tx.yml", "prey_path": "x", "verbatim": True}]},
        {
            "taken": [
                {"maw_path": "ci.yml", "prey_path": "x", "verbatim": True},
                {"maw_path": "ci.yml", "prey_path": "y", "verbatim": True},
            ]
        },
        {"summary": " "},
        {"checks": []},
    ],
)
def test_receipt_rejects_what_it_cannot_attribute(overrides: dict[str, Any]) -> None:
    with pytest.raises(CrabError, match="invalid materialization receipt"):
        load_materialization_receipt(_payload(**overrides))


def test_receipt_rejects_duplicate_members_and_non_objects() -> None:
    with pytest.raises(CrabError, match="invalid materialization receipt") as duplicate:
        load_materialization_receipt('{"version": 1, "version": 1}')
    assert duplicate.value.hint == "duplicate JSON object member: version"
    with pytest.raises(CrabError, match="invalid materialization receipt") as array:
        load_materialization_receipt("[]")
    assert array.value.hint == "receipt root must be an object"


# --- obligations -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spdx", "reason", "kind"),
    [
        ("MIT", "permissive license: keep the copyright notice", "copyright-notice"),
        ("Apache-2.0", "Apache-2.0: carry the NOTICE file over", "notice-file"),
        ("CC-BY-4.0", "CC-BY: attribution required", "attribution"),
        ("MPL-2.0", "file-level copyleft: whole files only", "file-license"),
        (None, "same owner: the maw's owner also owns the prey", "review"),
        ("GPL-3.0-only", "same owner, but the prey is gpl: check", "review"),
        ("MIT", "license checks bypassed by .crab.yml (trust.bypass_license)", "copyright-notice"),
        ("Foo-1.0", "unrecognised license Foo-1.0", "review"),
    ],
)
def test_obligation_is_structured_not_a_generic_copyright_line(
    spdx: str | None, reason: str, kind: str
) -> None:
    obligation = obligation_for(spdx, {"reason": reason})
    assert obligation.kind == kind
    assert obligation.text


# --- source verification -----------------------------------------------------------------


def test_sources_must_exist_in_the_prey_at_that_commit() -> None:
    receipt = load_materialization_receipt(_payload())
    with pytest.raises(CrabError, match="source path the prey does not have") as raised:
        verify_sources(receipt, FakePrey({}), {".github/workflows/ci.yml": WORKFLOW})
    assert raised.value.hint is not None and ".github/workflows/test.yml" in raised.value.hint


def test_a_verbatim_copy_must_match_its_source_byte_for_byte() -> None:
    receipt = load_materialization_receipt(
        _payload(taken=[{"maw_path": "LICENSE-pipx", "prey_path": "LICENSE", "verbatim": True}])
    )
    prey = FakePrey({(SHA, "LICENSE"): "MIT License\n\nCopyright (c) pipx\n"})
    verify_sources(receipt, prey, {"LICENSE-pipx": "MIT License\r\n\r\nCopyright (c) pipx\r\n"})
    with pytest.raises(CrabError, match="verbatim copy differs"):
        verify_sources(receipt, prey, {"LICENSE-pipx": "MIT License\n\nCopyright (c) someone\n"})


# --- the attributions file ---------------------------------------------------------------


def _record(
    nutrient_id: str = "crab:ci:ci.cache",
    sha: str = SHA,
    label: str = "pypa/pipx",
    taken: tuple[TakenFile, ...] | None = None,
    kind: str = "copyright-notice",
) -> AttributionRecord:
    return AttributionRecord(
        nutrient_id=nutrient_id,
        source=SourceRef(label, f"https://github.com/{label}", sha, "MIT"),
        taken=taken
        or (TakenFile(".github/workflows/ci.yml", ".github/workflows/test.yml", False),),
        mode="COPY",
        obligation=Obligation(kind, "keep the notice"),
        summary="carried over",
        branch=nutrient_branch_name(nutrient_id),
        recorded_at="2026-09-25T12:00:00+00:00",
    )


def test_the_same_receipt_is_recorded_once_and_a_second_source_is_a_second_record() -> None:
    first = _record()
    records = add_attribution([], first)
    assert add_attribution(records, first) == records, "a retry or a reconciliation adds nothing"
    later = _record(sha=OTHER_SHA, label="anthropics/skills")
    both = add_attribution(records, later)
    assert [r.source.label for r in both] == ["pypa/pipx", "anthropics/skills"]
    assert both[0] == first, "a later sighting from another prey does not rewrite history"
    ordered = parse_attributions(dump_attributions(both))
    assert [r.source.label for r in ordered] == ["anthropics/skills", "pypa/pipx"]
    assert ordered[1] == first


def test_the_same_identity_with_different_material_is_refused() -> None:
    records = add_attribution([], _record())
    changed = _record(taken=(TakenFile("ci.yml", ".github/workflows/test.yml", True),))
    with pytest.raises(CrabError, match="already exists with different material"):
        add_attribution(records, changed)


def test_attributions_file_round_trips_and_rejects_strangers(tmp_path: Path) -> None:
    assert load_attributions(tmp_path) == []
    path = tmp_path / ATTRIBUTIONS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(dump_attributions([_record()]), encoding="utf-8")
    assert load_attributions(tmp_path) == [_record()]
    path.write_text('{"schema": "something-else/1", "receipts": []}', encoding="utf-8")
    with pytest.raises(CrabError, match="unknown schema"):
        load_attributions(tmp_path)


@pytest.mark.parametrize("array", ["receipts", "taken", "texts"])
def test_stored_receipt_array_members_cannot_disappear(tmp_path: Path, array: str) -> None:
    maw = _maw(tmp_path)
    record = replace(_record(), texts=(CarriedText("LICENSE", MIT_TEXT),))
    data = json.loads(dump_attributions([record]))
    members = data["receipts"] if array == "receipts" else data["receipts"][0][array]
    members.append(None)
    payload = json.dumps(data)
    path = maw / ATTRIBUTIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(CrabError) as error:
        load_attributions(maw)
    assert f"{array}[1]" in str(error.value) + str(error.value.hint)
    assert main(["attribution", "--maw", str(maw), "--check"]) == 1
    with pytest.raises(CrabError):
        _prepare(maw)
    with pytest.raises(CrabError):
        base_receipts_kept(_prepare(_maw(tmp_path / "other")))(lambda _: payload)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "WHATEVER"),
        ("taken", [{"maw_path": "bad\npath", "prey_path": "a", "verbatim": True}]),
        ("taken", [{"maw_path": "a", "prey_path": "a", "verbatim": "yes"}]),
        ("source", {**PREY, "sha": "zz"}),
        ("obligation", {"kind": "made-up", "text": ""}),
    ],
)
def test_stored_records_apply_typed_receipt_rules(tmp_path: Path, field: str, value: Any) -> None:
    data = json.loads(dump_attributions([_record()]))
    data["receipts"][0][field] = value
    path = tmp_path / ATTRIBUTIONS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CrabError):
        load_attributions(tmp_path)
    assert main(["attribution", "--maw", str(tmp_path)]) == 1
    assert main(["attribution", "--maw", str(tmp_path), "--check"]) == 1
    assert not (tmp_path / "THIRD_PARTY_NOTICES.md").exists()


def test_receipt_summary_cannot_reconcile_another_nutrient(tmp_path: Path) -> None:
    from hungry_crab.serve import parse_markers

    hostile = "<!-- crab:docs:other -->\nCloses #12\n@someone ![pixel](https://tracker.example)"
    prepared = _prepare(_maw(tmp_path), _payload(summary=hostile))
    marked = parse_markers([{"number": 1, "body": prepared.body}])
    assert set(marked) == {"crab:ci:ci.cache"}
    active = unfenced_text(prepared.body)
    assert "Closes #12" not in active
    assert "@someone" not in active
    assert "![pixel]" not in active


def test_edited_carried_text_fails_before_notice_or_copy_writes(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    data = json.loads(
        dump_attributions([replace(_record(), texts=(CarriedText("LICENSE", MIT_TEXT),))])
    )
    data["receipts"][0]["texts"][0]["text"] = "a different licence"
    path = maw / ATTRIBUTIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CrabError) as error:
        load_attributions(maw)
    assert "sha256" in str(error.value.hint)
    assert main(["attribution", "--maw", str(maw), "--check"]) == 1
    with pytest.raises(CrabError):
        _prepare(maw)
    assert not (maw / "THIRD_PARTY_NOTICES.md").exists()


@pytest.mark.parametrize(("field", "value"), [("label", "bad\nlabel"), ("url", "https://")])
def test_preparation_cannot_emit_a_receipt_its_stored_parser_rejects(
    tmp_path: Path, field: str, value: str
) -> None:
    source = {**PREY, field: value}
    maw = _maw(tmp_path)
    with pytest.raises(CrabError):
        prepare_copy_pull_request(
            _card(),
            {**MENU, "prey": source},
            _payload(source=source),
            maw,
            title="safe",
            body="<!-- crab:ci:ci.cache -->",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=_prey(),
            now=NOW,
        )
    assert not (maw / ATTRIBUTIONS_PATH).exists()


def test_check_refuses_a_deleted_recorded_maw_file(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    path = maw / ATTRIBUTIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_attributions([_record()]), encoding="utf-8")
    assert main(["attribution", "--maw", str(maw)]) == 0
    (maw / ".github/workflows/ci.yml").unlink()
    notice = maw / "THIRD_PARTY_NOTICES.md"
    before = notice.read_bytes()
    assert main(["attribution", "--maw", str(maw), "--check"]) == 1
    assert main(["attribution", "--maw", str(maw)]) == 1
    assert notice.read_bytes() == before


@pytest.mark.parametrize("tracked", [False, True])
def test_missing_receipts_cannot_erase_the_notice(tmp_path: Path, tracked: bool) -> None:
    from fixture_builder import git

    maw = _maw(tmp_path)
    path = maw / ATTRIBUTIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_attributions([_record()]), encoding="utf-8")
    assert main(["attribution", "--maw", str(maw)]) == 0
    notice = maw / "THIRD_PARTY_NOTICES.md"
    if tracked:
        git(maw, "init", "--quiet")
        git(maw, "add", ".")
        git(
            maw,
            "commit",
            "--quiet",
            "-m",
            "test: attribution baseline",
            author="Fixture <fixture@example.invalid>",
        )
        notice.unlink()
    path.unlink()
    assert main(["attribution", "--maw", str(maw), "--check"]) == 1
    assert main(["attribution", "--maw", str(maw)]) == 1
    assert main(["attribution", "--maw", str(maw), "--check", "--allow-remove"]) == 1
    assert main(["attribution", "--maw", str(maw), "--allow-remove"]) == 0
    assert "Nothing has been carried" in notice.read_text(encoding="utf-8")


def test_github_source_case_does_not_create_duplicate_notice_sections() -> None:
    a = _record(label="Pypa/Pipx")
    b = _record(nutrient_id="crab:ci:ci.other", label="pypa/pipx")
    assert a.source == b.source
    assert render_notices([a, b]).count("## pypa/pipx @") == 1
    assert parse_attributions(dump_attributions([a, b]))[0].source.label == "pypa/pipx"


def test_source_license_reads_ignore_replacement_commits(tmp_path: Path) -> None:
    from fixture_builder import git

    prey = _maw(tmp_path, {"LICENSE": "Original licence\n"})
    git(prey, "init", "--quiet")
    git(prey, "add", ".")
    author = "Fixture <fixture@example.invalid>"
    git(prey, "commit", "--quiet", "-m", "test: original", author=author)
    original = git(prey, "rev-parse", "HEAD").strip()
    (prey / "LICENSE").write_text("Substitute licence\n", encoding="utf-8")
    git(prey, "commit", "--quiet", "-am", "test: substitute", author=author)
    substitute = git(prey, "rev-parse", "HEAD").strip()
    git(prey, "replace", original, substitute)
    reader = GitSourceReader(prey)
    assert (reader.read(original, "LICENSE") or "").strip() == "Original licence"
    assert carried_texts(original, reader)[0].text == "Original licence"


@pytest.mark.parametrize("value", [False, 42, "a", {}, None])
@pytest.mark.parametrize("array", ["receipts", "taken", "texts"])
def test_every_malformed_stored_array_shape_fails(array: str, value: Any) -> None:
    data = json.loads(dump_attributions([_record()]))
    if array == "receipts":
        data[array] = value
    else:
        data["receipts"][0][array] = value
    with pytest.raises(CrabError):
        parse_attributions(json.dumps(data))


# --- the notice file ---------------------------------------------------------------------


NOTICE = """\
# Third-party notices

Written by `crab attribution` from `.crab/attributions.json`, the receipts of
material Hungry Crab carried into this repository. Edit the receipts, not this
file; rerunning the command reproduces it byte for byte. It is headings and
paragraphs on purpose: a Markdown formatter has nothing to rewrite here.

## anthropics/skills @ 41bbe19 — MIT

Source:
<https://github.com/anthropics/skills/tree/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f>

Obligation (notice-file): keep the notice.

`.github/workflows/ci.yml`: adapted from `.github/workflows/test.yml`; COPY,
`crab:ci:ci.cache`.

## pypa/pipx @ c490b45 — MIT

Source:
<https://github.com/pypa/pipx/tree/c490b45bc4d18f8968d527ec2d37b8af36eb4361>

Obligation (copyright-notice): keep the notice.

`.github/workflows/ci.yml`: adapted from `.github/workflows/test.yml`; COPY,
`crab:ci:ci.cache`.

`.pre-commit-config.yaml`: copied unchanged from `.pre-commit-config.yaml`;
COPY, `crab:tooling:tooling.pre-commit`.
"""


def _three_records() -> list[AttributionRecord]:
    return [
        _record(sha=OTHER_SHA, label="anthropics/skills", kind="notice-file"),
        _record(),
        _record(
            nutrient_id="crab:tooling:tooling.pre-commit",
            taken=(TakenFile(".pre-commit-config.yaml", ".pre-commit-config.yaml", True),),
        ),
    ]


def test_notice_is_byte_stable_grouped_by_source_and_says_the_obligation() -> None:
    """The layout is a contract: the day these bytes change, every maw's notice is stale."""
    assert "Nothing has been carried over yet." in render_notices([])
    records = _three_records()
    text = render_notices(records)
    assert text == render_notices(list(reversed(records)))
    assert text == NOTICE
    assert text.count("## pypa/pipx") == 1, "two nutrients from one commit share a section"


def _assert_a_formatter_has_nothing_to_rewrite(text: str) -> None:
    lines = text.split("\n")
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert lines[0].startswith("# ")
    fence = ""
    for previous, line in pairwise(lines):
        assert line == line.rstrip(), "no trailing whitespace, so no hard line break either"
        if fence:
            # a formatter does not touch the inside of a fenced block: the carried licence text
            if line == fence:
                fence = ""
            continue
        opened = re.fullmatch(r"(`{3,})text", line)
        if opened:
            assert previous == "", "a fence opens its own block"
            fence = opened.group(1)
            continue
        assert not line.startswith("|"), "a table is realigned by every Markdown formatter"
        assert not (previous == "" and line == ""), "one blank line between blocks"
        if " " in line:
            assert len(line) <= NOTICE_WIDTH or line.startswith("`"), line
        if previous != "" and line != "" and not re.fullmatch(r"`{3,}", previous):
            assert re.match(r"[-+*>#=~]|\d+[.)]|_{3}", line) is None, (
                "a continuation line must not read as a list, a quote, a heading or a rule"
            )
    assert not fence, "every fence is closed"


def test_notice_is_headings_and_paragraphs_a_formatter_leaves_alone() -> None:
    """A maw's gate may be `prettier --check .`, and the table this file used to carry failed it.

    Prettier is not available to this suite, so the properties that make the file a fixed point
    of its defaults are asserted instead. The layout was run through Prettier 3 when it was
    chosen: the defaults, `--tab-width 4`, `--use-tabs` and `--prose-wrap always` leave it alone.
    """
    _assert_a_formatter_has_nothing_to_rewrite(render_notices([]))
    _assert_a_formatter_has_nothing_to_rewrite(render_notices(_three_records()))


def test_notice_survives_the_names_a_source_gives_its_files() -> None:
    """Prey names its own files, and a name is data: it must not become Markdown."""
    weird = AttributionRecord(
        nutrient_id="crab:docs:docs.weird",
        source=SourceRef("_x_/*y* [z](u) <b>", "https://example.com/a b", SHA, "MIT"),
        taken=(
            TakenFile("docs/My File.md", "docs/a`b``c.md", False),
            TakenFile("docs/pipe|name.md", "`lead.md", True),
            TakenFile("d/" + "long/" * 20 + "x.md", "- dash/# hash/> quote/1. one.md", False),
        ),
        mode="COPY",
        obligation=Obligation("review", "- a person decides  # and 1. nothing > else ---"),
        summary="carried over",
        branch="crab/x",
        recorded_at="2026-09-25T12:00:00+00:00",
    )
    own = AttributionRecord(
        nutrient_id="crab:docs:docs.own",
        source=weird.source,
        taken=(TakenFile("b.md", "b.md", True),),
        mode="COPY",
        obligation=Obligation("none", "same owner: no notice is owed."),
        summary="carried over",
        branch="crab/y",
        recorded_at="2026-09-25T12:00:00+00:00",
    )
    text = render_notices([weird, own])
    _assert_a_formatter_has_nothing_to_rewrite(text)
    assert "## `_x_/*y* [z](u) <b>` @ c490b45 — MIT" in text
    assert f"Source: `https://example.com/a b/tree/{SHA}`\n" in text
    assert "<https://" not in text, "what is not a link is not written as one"
    assert "`docs/My File.md`: adapted from ```docs/a`b``c.md```; COPY," in text
    assert "`docs/pipe|name.md`: copied unchanged from `` `lead.md ``; COPY," in text
    assert "adapted from `- dash/# hash/> quote/1. one.md`; COPY," in text
    assert "Obligation (none): same owner: no notice is owed.\n" in text
    assert (
        "Obligation (review): - a person decides &#35; and 1. nothing &gt; else ---."
        in " ".join(text.split())
    )
    assert text.index("Obligation (none)") < text.index("`b.md`: copied unchanged")
    assert text.index("`b.md`: copied") < text.index("Obligation (review)"), (
        "an obligation is said again where it changes inside one source"
    )


# --- preparing the pull request ----------------------------------------------------------


def test_prepare_carries_the_files_the_receipt_and_the_notice(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    prepared = _prepare(maw)

    paths = [generated.path for generated in prepared.files]
    assert paths == [".github/workflows/ci.yml", ATTRIBUTIONS_PATH, "THIRD_PARTY_NOTICES.md"]
    assert prepared.files[0].content == WORKFLOW
    receipts = json.loads(prepared.files[1].content)["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["nutrient_id"] == "crab:ci:ci.cache"
    assert receipts[0]["source"]["sha"] == SHA
    assert receipts[0]["branch"] == nutrient_branch_name("crab:ci:ci.cache")
    assert receipts[0]["recorded_at"] == "2026-09-25T12:00:00+00:00"
    assert receipts[0]["obligation"]["kind"] == "copyright-notice"
    assert receipts[0]["texts"] == [CarriedText("LICENSE", MIT_TEXT.rstrip("\n")).to_dict()]
    assert receipts[0]["checks"] == ["uv run pytest -q"]
    notice = prepared.files[2].content
    assert (
        "`.github/workflows/ci.yml`: adapted from `.github/workflows/test.yml`; COPY,\n"
        "`crab:ci:ci.cache`." in notice
    )
    # the obligation is discharged, not only named: the copyright and permission notice travel
    assert "`LICENSE` of the source at c490b45:\n\n```text\nMIT License\n" in notice
    assert "Copyright (c) 2024 Prey Owner" in notice
    assert "The above copyright notice and this permission notice shall be included" in notice
    _assert_a_formatter_has_nothing_to_rewrite(notice)
    assert "The notice reproduces the source's `LICENSE`" in prepared.body
    assert prepared.body.startswith("<!-- crab:ci:ci.cache -->")
    assert "## Attribution" in prepared.body
    assert "Taken from `pypa/pipx@c490b45` (MIT, mode COPY)" in prepared.body
    assert (
        "| `.github/workflows/ci.yml` | `.github/workflows/test.yml` | adapted |" in prepared.body
    )
    assert "carried the cache step over, adapted to uv" in prepared.body
    assert "no check that ran in the working tree has seen them" in prepared.body


def test_prepare_extends_the_maws_existing_receipts_and_reruns_are_stable(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    existing = maw / ATTRIBUTIONS_PATH
    existing.parent.mkdir(parents=True)
    existing.write_text(
        dump_attributions([_record(sha=OTHER_SHA, label="anthropics/skills")]), encoding="utf-8"
    )

    first = _prepare(maw)
    again = _prepare(maw)

    receipts = json.loads(first.files[1].content)["receipts"]
    assert [r["source"]["label"] for r in receipts] == ["anthropics/skills", "pypa/pipx"]
    assert first.files[1].content == again.files[1].content
    assert first.files[2].content == again.files[2].content


@pytest.mark.parametrize(
    ("card", "payload", "message"),
    [
        (_card(mode="REIMPLEMENT"), _payload(), "does not materialize"),
        (_card(key="other"), _payload(), "does not match the selected nutrient"),
        (_card(), _payload(source={**PREY, "sha": OTHER_SHA}), "different source than the meal"),
        (
            _card(),
            _payload(source={**PREY, "license": "Apache-2.0"}),
            "different source than the meal",
        ),
        (_card(mode="COPY_FILE"), _payload(), "whole files"),
        (
            _card(),
            _payload(taken=[{"maw_path": ATTRIBUTIONS_PATH, "prey_path": "x", "verbatim": False}]),
            "may not take over the attribution files",
        ),
    ],
)
def test_prepare_refuses_receipts_that_contradict_the_meal(
    tmp_path: Path, card: Candidate, payload: str, message: str
) -> None:
    maw = _maw(tmp_path)
    prey = FakePrey({(SHA, ".github/workflows/test.yml"): "t", (SHA, "x"): "x"})
    with pytest.raises(CrabError, match=message):
        prepare_copy_pull_request(
            card,
            MENU,
            payload,
            maw,
            title="t",
            body=f"<!-- {card.id} -->\n",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=prey,
            now=NOW,
        )


def test_an_apache_source_carries_its_licence_and_its_notice_file(tmp_path: Path) -> None:
    menu = {**MENU, "prey": {**PREY, "license": "Apache-2.0"}}
    prey = FakePrey(
        {
            (SHA, ".github/workflows/test.yml"): "name: test\n",
            (SHA, "LICENSE"): "Apache License\nVersion 2.0, January 2004\n",
            (SHA, "NOTICE"): "Widget\nCopyright 2020 The Widget Authors\n",
            (SHA, "README.md"): "# widget\n",
        }
    )
    prepared = prepare_copy_pull_request(
        _card(), menu, _payload(source={**PREY, "license": "Apache-2.0"}), _maw(tmp_path),
        title="t", body="<!-- crab:ci:ci.cache -->\n", attribution_file="THIRD_PARTY_NOTICES.md",
        source_reader=prey, now=NOW,
    )  # fmt: skip
    record = parse_attributions(prepared.files[1].content)[0]
    assert record.obligation.kind == "notice-file"
    assert [item.path for item in record.texts] == ["LICENSE", "NOTICE"]
    notice = prepared.files[2].content
    assert "Copyright 2020 The Widget Authors" in notice
    assert "# widget" not in notice, "only licence and NOTICE files travel"
    _assert_a_formatter_has_nothing_to_rewrite(notice)


def test_a_licence_that_asks_for_its_notice_and_a_prey_with_none_to_carry_is_refused(
    tmp_path: Path,
) -> None:
    prey = FakePrey({(SHA, ".github/workflows/test.yml"): "name: test\n"})
    with pytest.raises(CrabError, match="no licence file to carry"):
        _prepare(_maw(tmp_path), source_reader=prey)


@pytest.mark.parametrize("fork", [False, True])
def test_ones_own_code_carries_no_licence_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fork: bool
) -> None:
    metadata: dict[str, Any] = {"full_name": "pypa/pipx", "fork": fork}
    if fork:
        metadata.update(parent={"full_name": "pypa/parent"}, source={"full_name": "pypa/original"})
    monkeypatch.setattr(GitHubClient, "repo", lambda *_: metadata)
    (tmp_path / ".crab.yml").write_text("trust:\n  owners: [pypa]\n", encoding="utf-8")
    menu = {
        **MENU,
        "prey": {**PREY, "repository": {"fork": False}},
        "verdict": {
            **MENU["verdict"],
            "relationship": "own",
            "reason": "same owner: one's own code",
        },
    }
    prepared = prepare_copy_pull_request(
        _card(), menu, _payload(), _maw(tmp_path),
        title="t", body="<!-- crab:ci:ci.cache -->\n", attribution_file="THIRD_PARTY_NOTICES.md",
        source_reader=FakePrey({(SHA, ".github/workflows/test.yml"): "name: test\n"}), now=NOW,
    )  # fmt: skip
    record = parse_attributions(prepared.files[1].content)[0]
    assert record.obligation.kind == "none"
    assert record.texts == ()


def test_review_flag_cannot_remove_a_foreign_sources_notice_obligation(tmp_path: Path) -> None:
    menu = {**MENU, "verdict": {**MENU["verdict"], "relationship": "foreign", "human_review": True}}
    with pytest.raises(CrabError, match="no licence file to carry"):
        prepare_copy_pull_request(
            _card(),
            menu,
            _payload(),
            _maw(tmp_path),
            title="t",
            body="<!-- crab:ci:ci.cache -->\n",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=FakePrey({(SHA, ".github/workflows/test.yml"): "name: test\n"}),
            now=NOW,
        )


@pytest.mark.parametrize("typed", [False, True])
def test_a_loaded_meal_cannot_forge_ownership(tmp_path: Path, typed: bool) -> None:
    (tmp_path / ".crab.yml").write_text("trust:\n  owners: [pypa]\n", encoding="utf-8")
    verdict = {**MENU["verdict"], "reason": "same owner: one's own code"}
    if typed:
        verdict["relationship"] = "own"
    menu = {
        **MENU,
        "verdict": verdict,
        "prey": {
            **PREY,
            "repository": {
                "fork": True,
                "parent": {"full_name": "foreign/parent"},
                "source": {"full_name": "foreign/root"},
            },
        },
    }
    with pytest.raises(CrabError, match="ownership"):
        prepare_copy_pull_request(
            _card(),
            menu,
            _payload(),
            _maw(tmp_path),
            title="t",
            body="<!-- crab:ci:ci.cache -->\n",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=FakePrey({}),
            now=NOW,
        )


def test_loaded_meal_cannot_forge_both_relationship_and_fork_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".crab.yml").write_text("trust:\n  owners: [pypa]\n", encoding="utf-8")
    menu = {
        **MENU,
        "prey": {**PREY, "repository": {"fork": False}},
        "verdict": {**MENU["verdict"], "relationship": "own"},
    }
    calls: list[str] = []

    def repository(client: GitHubClient, slug: Any) -> dict[str, Any]:
        assert client.cache_dir is None, "ownership proof must not reuse an editable cache"
        calls.append(str(slug))
        return {
            "full_name": "pypa/pipx",
            "fork": True,
            "parent": {"full_name": "foreign/parent"},
            "source": {"full_name": "foreign/root"},
        }

    monkeypatch.setattr(GitHubClient, "repo", repository)
    with pytest.raises(CrabError, match="ownership"):
        prepare_copy_pull_request(
            _card(),
            menu,
            _payload(),
            _maw(tmp_path),
            title="t",
            body="<!-- crab:ci:ci.cache -->\n",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=FakePrey({(SHA, ".github/workflows/test.yml"): "name: test\n"}),
            now=NOW,
        )
    assert calls == ["pypa/pipx"]
    assert not (tmp_path / ATTRIBUTIONS_PATH).exists()


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"full_name": "another/repository", "fork": False},
        {"full_name": "pypa/pipx"},
        {"full_name": "pypa/pipx", "fork": True, "parent": {"full_name": "pypa/parent"}},
        None,
    ],
)
def test_ownership_refuses_unavailable_or_incomplete_provider_proof_before_maw_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any] | None
) -> None:
    (tmp_path / ".crab.yml").write_text("trust:\n  owners: [pypa]\n", encoding="utf-8")
    menu = {
        **MENU,
        "prey": {**PREY, "repository": {"fork": False}},
        "verdict": {**MENU["verdict"], "relationship": "own"},
    }

    def repository(*args: Any) -> dict[str, Any]:
        if metadata is None:
            raise CrabError("provider unavailable")
        return metadata

    monkeypatch.setattr(GitHubClient, "repo", repository)
    monkeypatch.setattr(
        "hungry_crab.attribution.read_maw_text", lambda *_: pytest.fail("maw file read")
    )
    with pytest.raises(CrabError, match="ownership"):
        prepare_copy_pull_request(
            _card(),
            menu,
            _payload(),
            _maw(tmp_path),
            title="t",
            body="<!-- crab:ci:ci.cache -->\n",
            attribution_file="THIRD_PARTY_NOTICES.md",
            source_reader=FakePrey({}),
            now=NOW,
        )
    assert not (tmp_path / ATTRIBUTIONS_PATH).exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_loaded_bypass_requires_current_explicit_maw_policy(tmp_path: Path, enabled: bool) -> None:
    if enabled:
        (tmp_path / ".crab.yml").write_text("trust:\n  bypass_license: true\n", encoding="utf-8")
    menu = {**MENU, "verdict": {**MENU["verdict"], "relationship": "bypass", "human_review": True}}
    kwargs = {
        "title": "t",
        "body": "<!-- crab:ci:ci.cache -->\n",
        "attribution_file": "THIRD_PARTY_NOTICES.md",
        "source_reader": _prey(),
        "now": NOW,
    }
    if not enabled:
        with pytest.raises(CrabError, match="ownership decision is not authorized"):
            prepare_copy_pull_request(_card(), menu, _payload(), _maw(tmp_path), **kwargs)
    else:
        prepared = prepare_copy_pull_request(_card(), menu, _payload(), _maw(tmp_path), **kwargs)
        assert parse_attributions(prepared.files[1].content)[0].obligation.kind == "review"


def test_a_carried_text_is_fenced_past_its_own_backticks_and_cleaned() -> None:
    text = "Line with ``` inside\r\n\r\nTabs\there   \r\n\x1bescape\x0c\n\n\n"
    record = replace(_record(), texts=(CarriedText("LICENSE.md", text),))
    notice = render_notices([record])
    assert "````text\nLine with ``` inside\n\nTabs\there\nescape\n````\n" in notice
    _assert_a_formatter_has_nothing_to_rewrite(notice)
    assert clean_text(text) == "Line with ``` inside\n\nTabs\there\nescape"


def test_a_record_without_texts_gains_them_and_nothing_else() -> None:
    old = _record()
    carried = replace(old, texts=(CarriedText("LICENSE", MIT_TEXT),))
    records = add_attribution([old], carried)
    assert records == [carried]
    assert add_attribution(records, old) == records, "a later sighting never removes them"


def test_the_git_reader_lists_the_files_at_the_root_of_the_prey(tmp_path: Path) -> None:
    repo = tmp_path / "mono"
    (repo / "packages" / "lib").mkdir(parents=True)
    (repo / "LICENSE").write_text("Apache License\n", encoding="utf-8")
    (repo / "packages" / "lib" / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (repo / "packages" / "lib" / "index.js").write_text("x\n", encoding="utf-8")
    run = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*run, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*run, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*run, "commit", "-qm", "init"], cwd=repo, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    root = GitSourceReader(repo)
    assert root.entries(sha) == {"LICENSE": "100644"}, "files only, not directories"
    lib = GitSourceReader(repo / "packages" / "lib")
    assert set(lib.entries(sha) or {}) == {"LICENSE", "index.js"}, "a subdirectory has its root"
    assert [t.text for t in carried_texts(sha, lib)] == ["MIT License"]
    assert root.entries("0" * 40) is None
    # a subdirectory prey's paths are its own, not the monorepo root's
    assert lib.exists(sha, "index.js") and not root.exists(sha, "index.js")
    assert lib.read(sha, "LICENSE") == "MIT License\n"


def test_a_symlinked_licence_is_refused_rather_than_carried_as_its_path(tmp_path: Path) -> None:
    """A link's blob is the path it points at: carried, the notice would reproduce a file name."""
    repo = tmp_path / "prey"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "LICENSE.txt").write_text(MIT_TEXT, encoding="utf-8")
    run = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*run, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*run, "add", "-A"], cwd=repo, check=True)
    # a symlink entry without a symlink on disk, which needs no privilege on Windows
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo, input="docs/LICENSE.txt", check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"120000,{blob},LICENSE"],
        cwd=repo, check=True,
    )  # fmt: skip
    subprocess.run([*run, "commit", "-qm", "init"], cwd=repo, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    assert GitSourceReader(repo).entries(sha) == {"LICENSE": "120000"}
    with pytest.raises(CrabError, match="symbolic link"):
        carried_texts(sha, GitSourceReader(repo))
    fake = FakePrey({(SHA, "LICENSE"): "docs/LICENSE.txt"}, modes={"LICENSE": "120000"})
    with pytest.raises(CrabError, match="symbolic link"):
        carried_texts(SHA, fake)


def test_prepare_refuses_a_missing_maw_file(tmp_path: Path) -> None:
    with pytest.raises(CrabError, match="cannot be published from the maw") as raised:
        _prepare(tmp_path)
    assert raised.value.hint == "declared file is missing: .github/workflows/ci.yml"


def test_a_copy_card_is_served_through_the_guarded_transaction(tmp_path: Path) -> None:
    """The policy layer plans a COPY card with a materialization receipt like any other."""
    maw = _maw(tmp_path)
    card = _card()
    config = MawConfig(root=maw)
    config.serve.prs = "auto"
    ledger = Ledger(tmp_path / "ledger.json", maw="maw")
    published: list[PreparedPullRequest] = []

    def preparer(card: Candidate, payload: str) -> PreparedPullRequest:
        return _prepare(maw, payload)

    def publisher(
        card: Candidate, prepared: PreparedPullRequest, allow_create: bool
    ) -> PullRequestPublication:
        published.append(prepared)
        return PullRequestPublication(
            branch=nutrient_branch_name(card.id), url="https://example.test/pr/12", created=True
        )

    report = serve_cleanroom_pull_requests(
        [card],
        {card.id: _payload()},
        config=config,
        ledger=ledger,
        explicit_selection=True,
        preparer=preparer,
        publisher=publisher,
        now=NOW,
    )

    assert [item["url"] for item in report.served] == ["https://example.test/pr/12"]
    assert [generated.path for generated in published[0].files][1:] == [
        ATTRIBUTIONS_PATH,
        "THIRD_PARTY_NOTICES.md",
    ]
    assert ledger.entries[card.id].status == "served"


# --- what a receipt may take, where the notice goes, what a branch may drop -------------


def _taking(prey_path: str) -> Any:
    return load_materialization_receipt(
        _payload(taken=[{"maw_path": "x.py", "prey_path": prey_path, "verbatim": False}])
    )


@pytest.mark.parametrize(
    ("files", "prey_path", "message"),
    [
        ({"vendor/gplthing/core.c": "int x;\n"}, "vendor/gplthing/core.c", "vendored code"),
        (
            {"lib/apache_part/util.py": "x = 1\n", "lib/apache_part/LICENSE": "Apache\n"},
            "lib/apache_part/util.py",
            "a licence of its own",
        ),
        (
            {"src/core.py": "# SPDX-License-Identifier: GPL-2.0-only\nx = 1\n"},
            "src/core.py",
            "declares another licence",
        ),
    ],
)
def test_a_taken_file_under_another_licence_is_refused(
    files: dict[str, str], prey_path: str, message: str
) -> None:
    """The repository's verdict does not answer for someone else's material inside the prey."""
    prey = FakePrey({(SHA, path): text for path, text in files.items()})
    with pytest.raises(CrabError, match=message):
        check_taken_licences(_taking(prey_path), prey)


@pytest.mark.parametrize(
    ("prey_path", "mode"),
    [("src/lib", None), ("src/link.py", "120000"), ("src/vendored", "160000")],
)
def test_a_taken_prey_path_must_be_a_regular_file(prey_path: str, mode: str | None) -> None:
    """A tree, a link and a submodule would each pass as the source of an adapted file."""
    files = {(SHA, "src/lib/x.py"): "x = 1\n", (SHA, "src/ok.py"): "y = 2\n"}
    modes = {}
    if mode is not None:
        files[(SHA, prey_path)] = "../elsewhere.py" if mode == "120000" else ""
        modes[prey_path] = mode
    with pytest.raises(CrabError, match="not a regular file"):
        check_taken_licences(_taking(prey_path), FakePrey(files, modes))
    check_taken_licences(_taking("src/ok.py"), FakePrey(files, modes))


def test_a_licence_file_that_is_a_link_still_marks_its_directory() -> None:
    files = {(SHA, "lib/part/util.py"): "x = 1\n", (SHA, "lib/part/LICENSE"): "../X"}
    with pytest.raises(CrabError, match="a licence of its own"):
        check_taken_licences(
            _taking("lib/part/util.py"), FakePrey(files, {"lib/part/LICENSE": "120000"})
        )


def test_the_git_reader_reports_links_and_submodules_by_mode(tmp_path: Path) -> None:
    repo = tmp_path / "prey"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    run = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*run, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*run, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*run, "commit", "-qm", "one"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo, input="a.py", check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    for entry in (f"120000,{blob},link.py", f"160000,{head},sub"):
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", entry], cwd=repo, check=True)
    subprocess.run([*run, "commit", "-qm", "two"], cwd=repo, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert GitSourceReader(repo).entries(sha) == {
        "a.py": "100644",
        "link.py": "120000",
        "sub": "160000",
    }
    for taken in ("link.py", "sub"):
        receipt = load_materialization_receipt(
            _payload(
                source={**PREY, "sha": sha},
                taken=[{"maw_path": "x.py", "prey_path": taken, "verbatim": False}],
            )
        )
        with pytest.raises(CrabError, match="not a regular file"):
            check_taken_licences(receipt, GitSourceReader(repo))


def test_a_taken_file_under_the_sources_own_licence_passes() -> None:
    prey = FakePrey(
        {
            (SHA, "src/core.py"): "# SPDX-License-Identifier: MIT\nx = 1\n",
            (SHA, "LICENSE"): MIT_TEXT,
            (SHA, "src/README.md"): "# src\n",
        }
    )
    check_taken_licences(_taking("src/core.py"), prey)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../outside.md",
        "/etc/notice.md",
        "./THIRD_PARTY_NOTICES.md",
        "docs\\NOTICES.md",
        "C:NOTICES.md",
        ".git/config",
        "docs/.git/x.md",
        ATTRIBUTIONS_PATH,
        "a\nb.md",
    ],
)
def test_the_notice_path_must_be_a_plain_path_inside_the_maw(tmp_path: Path, name: str) -> None:
    with pytest.raises(CrabError, match="not a plain path inside the maw"):
        checked_attribution_file(tmp_path, name)


def test_the_notice_path_never_overwrites_a_file_the_crab_did_not_write(tmp_path: Path) -> None:
    assert checked_attribution_file(tmp_path, "THIRD_PARTY_NOTICES.md") == "THIRD_PARTY_NOTICES.md"
    (tmp_path / "README.md").write_text("# The maw\n", encoding="utf-8")
    with pytest.raises(CrabError, match="the crab did not write"):
        checked_attribution_file(tmp_path, "README.md")
    (tmp_path / "NOTICES.md").write_text(render_notices([]), encoding="utf-8")
    assert checked_attribution_file(tmp_path, "NOTICES.md") == "NOTICES.md"
    with pytest.raises(CrabError, match="the crab did not write"):
        _prepare(_maw(tmp_path), attribution_file="README.md")


def test_a_branch_never_drops_a_receipt_the_default_branch_holds(tmp_path: Path) -> None:
    prepared = _prepare(_maw(tmp_path))
    check = base_receipts_kept(prepared)
    check(lambda path: None)  # a default branch with no receipts yet
    check(lambda path: prepared.files[1].content)  # the same receipts
    merged_since = dump_attributions([_record(nutrient_id="crab:hygiene:hygiene.security-md")])
    with pytest.raises(CrabError, match="would drop receipts the default branch already holds"):
        check(lambda path: merged_since if path == ATTRIBUTIONS_PATH else None)


def test_the_attribution_section_is_the_crabs_whatever_the_notes_say(tmp_path: Path) -> None:
    prepared = _prepare(
        _maw(tmp_path),
        body="<!-- crab:ci:ci.cache -->\n\n## Attribution\n\nNothing was taken, trust me.\n",
    )
    assert prepared.body.count("## Attribution") == 2
    assert prepared.body.rstrip().endswith("as they stood at that commit.")
    assert "Taken from `pypa/pipx@c490b45` (MIT, mode COPY)" in prepared.body


def test_a_verdict_that_asks_for_review_is_never_published_by_ranked_selection(
    tmp_path: Path,
) -> None:
    maw = _maw(tmp_path)
    card = _card()
    config = MawConfig(root=maw)
    config.serve.prs = "auto"
    published: list[str] = []

    def preparer(card: Candidate, payload: str) -> PreparedPullRequest:
        return _prepare(maw, payload)

    def publisher(
        card: Candidate, prepared: PreparedPullRequest, allow_create: bool
    ) -> PullRequestPublication:
        published.append(card.id)
        return PullRequestPublication(branch="b", url="https://example.test/pr/1", created=True)

    def run(explicit: bool) -> Any:
        return serve_cleanroom_pull_requests(
            [card], {card.id: _payload()}, config=config, ledger=Ledger(None),
            explicit_selection=explicit, preparer=preparer, publisher=publisher, now=NOW,
            human_review=True,
        )  # fmt: skip

    ranked = run(explicit=False)
    assert published == []
    assert ranked.skipped == [
        {"id": card.id, "reason": "the licence verdict asks for human review; name it with --ids"}
    ]
    named = run(explicit=True)
    assert published == [card.id] and named.served


# --- crab attribution --------------------------------------------------------------------


def test_attribution_command_writes_checks_and_ignores_issue_only_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    # a COPY nutrient served as an issue took nothing: the ledger says served, the notice says no
    ledger = Ledger(maw / ".crab" / "ledger.json", maw="maw")
    ledger.ensure(_card())
    ledger.mark("crab:ci:ci.cache", "served", url="https://example.test/issues/1")
    ledger.save()

    assert main(["attribution", "--maw", str(maw), "--check"]) == 0, "nothing owed, nothing stale"
    assert main(["attribution", "--maw", str(maw)]) == 0
    notice = maw / "THIRD_PARTY_NOTICES.md"
    assert "Nothing has been carried over yet." in notice.read_text(encoding="utf-8")

    receipts = maw / ATTRIBUTIONS_PATH
    receipts.parent.mkdir(parents=True, exist_ok=True)
    receipts.write_text(dump_attributions([_record()]), encoding="utf-8")
    _maw(maw)
    assert main(["attribution", "--maw", str(maw), "--check"]) == 1, "stale after a new receipt"
    assert "stale" in capsys.readouterr().err
    assert main(["attribution", "--maw", str(maw)]) == 0
    assert "wrote THIRD_PARTY_NOTICES.md from 1 receipt(s)" in capsys.readouterr().out
    assert "`crab:ci:ci.cache`." in notice.read_text(encoding="utf-8")
    assert main(["attribution", "--maw", str(maw)]) == 0
    assert "unchanged" in capsys.readouterr().out
    assert main(["attribution", "--maw", str(maw), "--check"]) == 0
