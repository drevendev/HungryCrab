"""COPY pull requests: the materialization receipt, the attributions file and the notice.

The boundary under test is the one #70's thread settled on: a notice row exists only for
material the crab actually carried into the maw, recorded when the pull request was prepared,
never reconstructed from a ledger status or from whichever prey proposed the nutrient first.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.attribution import (
    ATTRIBUTIONS_PATH,
    AttributionRecord,
    Obligation,
    SourceRef,
    TakenFile,
    add_attribution,
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

    def __init__(self, files: dict[tuple[str, str], str]) -> None:
        self.files = files

    def exists(self, sha: str, path: str) -> bool:
        return (sha, path) in self.files

    def read(self, sha: str, path: str) -> str | None:
        return self.files.get((sha, path))


def _prey() -> FakePrey:
    return FakePrey({(SHA, ".github/workflows/test.yml"): "name: test\n"})


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
        body="<!-- crab:ci:ci.cache -->\n\nCarry the cache step.\n",
        attribution_file="THIRD_PARTY_NOTICES.md",
        source_reader=kwargs.pop("source_reader", _prey()),
        now=NOW,
        **kwargs,
    )


# --- the receipt -------------------------------------------------------------------------


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
        (None, "same owner: the maw's owner also owns the prey", "none"),
        ("GPL-3.0-only", "same owner, but the prey is gpl: check", "review"),
        ("MIT", "license checks bypassed by .crab.yml (trust.bypass_license)", "review"),
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


# --- the notice file ---------------------------------------------------------------------


def test_notice_is_byte_stable_grouped_by_source_and_says_the_obligation() -> None:
    assert "Nothing has been carried over yet." in render_notices([])
    records = [
        _record(sha=OTHER_SHA, label="anthropics/skills", kind="notice-file"),
        _record(),
        _record(
            nutrient_id="crab:tooling:tooling.pre-commit",
            taken=(TakenFile(".pre-commit-config.yaml", ".pre-commit-config.yaml", True),),
        ),
    ]
    text = render_notices(records)
    assert text == render_notices(list(reversed(records)))
    skills = text.index("## anthropics/skills @ 41bbe19")
    pipx = text.index("## pypa/pipx @ c490b45 — MIT")
    assert skills < pipx
    assert "Obligation (notice-file): keep the notice" in text
    cache_row = (
        "| `crab:ci:ci.cache` | `.github/workflows/ci.yml` "
        "| `.github/workflows/test.yml` (adapted) | COPY |"
    )
    precommit_row = (
        "| `crab:tooling:tooling.pre-commit` | `.pre-commit-config.yaml` "
        "| `.pre-commit-config.yaml` | COPY |"
    )
    assert cache_row in text
    assert precommit_row in text
    assert text.count("## pypa/pipx") == 1, "two nutrients from one commit share a section"
    assert f"<https://github.com/pypa/pipx/tree/{SHA}>" in text


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
    assert "| `crab:ci:ci.cache` | `.github/workflows/ci.yml` |" in prepared.files[2].content
    assert prepared.body.startswith("<!-- crab:ci:ci.cache -->")
    assert "## Attribution" in prepared.body
    assert "Taken from `pypa/pipx@c490b45` (MIT, mode COPY)" in prepared.body
    assert (
        "| `.github/workflows/ci.yml` | `.github/workflows/test.yml` | adapted |" in prepared.body
    )
    assert "carried the cache step over, adapted to uv" in prepared.body


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
    assert main(["attribution", "--maw", str(maw), "--check"]) == 1, "stale after a new receipt"
    assert "stale" in capsys.readouterr().err
    assert main(["attribution", "--maw", str(maw)]) == 0
    assert "wrote THIRD_PARTY_NOTICES.md from 1 receipt(s)" in capsys.readouterr().out
    assert "| `crab:ci:ci.cache` |" in notice.read_text(encoding="utf-8")
    assert main(["attribution", "--maw", str(maw)]) == 0
    assert "unchanged" in capsys.readouterr().out
    assert main(["attribution", "--maw", str(maw), "--check"]) == 0
