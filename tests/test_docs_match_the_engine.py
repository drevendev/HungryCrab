"""Prose rots against code silently, and no other test here would notice.

`README.md`, the design documents and the skill references all describe the licence engine, and
one of them described it wrongly for a day: every one of them said an unrecognised licence ends
as `IDEAS_ONLY` with a human flag, which stopped being true the moment `HUMAN` started being
returned. The instance that mattered was not the README — it was
`skills/license/references/matrix.md`, the table an agent reads while deciding whether code may
be copied.

These tests guard the retired claim by name. They are narrow on purpose: a test that tried to
parse every table in the repository would be a second implementation of the matrix, and it would
rot too.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from hungry_crab.licensing import Mode, decide
from hungry_crab.maw import DEFAULT_CONFIG_TEXT, MawConfig

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = sorted(
    [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "skills").rglob("*.md"),
        *(ROOT / "agents").glob("*.md"),
        *(ROOT / "commands").glob("*.md"),
    ]
)
# The exact pairing that was true until `HUMAN` became reachable. CHANGELOG entries describe the
# past and are allowed to keep it.
RETIRED_CLAIM = "`IDEAS_ONLY` + `HUMAN`"


def test_the_engine_still_answers_these_three_cases_differently() -> None:
    """If this fails, the documents below are right and the tests are what needs updating."""
    assert decide("BUSL-1.1", "MIT").mode is Mode.IDEAS_ONLY
    assert not decide("BUSL-1.1", "MIT").human_review
    assert decide(None, "MIT").mode is Mode.IDEAS_ONLY
    assert decide(None, "MIT").human_review
    assert decide("Weird-License-9", "MIT").mode is Mode.HUMAN


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_document_still_pairs_the_two_modes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert RETIRED_CLAIM not in text, (
        f"{path.relative_to(ROOT)} still says {RETIRED_CLAIM}. An unrecognised licence is "
        "`HUMAN`; a missing one is `IDEAS_ONLY` flagged for review. They are different answers."
    )


def test_the_reference_an_agent_decides_from_names_both_outcomes() -> None:
    """The skill hands this file to a model at the moment it decides. It is the one that counts."""
    text = (ROOT / "skills" / "license" / "references" / "matrix.md").read_text(encoding="utf-8")
    assert "| License read and not classified | `HUMAN` |" in text
    assert "| No license found | `IDEAS_ONLY`" in text
    assert "`own`" in text and "`bypass`" in text, (
        "the relationship short-circuits the matrix, so the matrix reference has to mention it"
    )


def test_security_does_not_promise_a_bound_acquisition_does_not_have() -> None:
    """The threat model may only claim what `catch` enforces.

    The caps the threat model points at — `MAX_FILES`, `MAX_TEXT_SIZE`, `MAX_VENDORED_PER_DIR`,
    `MAX_COMMITS` — all live in the miners, which run after the clone has landed on disk. While
    `CatchOptions()` fetches everything by default, the unconditional claim is false, and the
    person reading it is deciding whether to point the crab at a stranger's repository.

    When acquisition gains a real bound, the first assertion here is what says the prose may go
    back to the shorter promise.
    """
    from hungry_crab.fetch.catch import CatchOptions

    unbounded = CatchOptions().shallow is False and CatchOptions().since is None
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if unbounded:
        assert "exhaust the machine **while it is being digested**" in text, (
            "`CatchOptions()` still clones everything, so SECURITY.md has to say that the caps "
            "bound digestion rather than acquisition"
        )
        assert "### What is not bounded: acquisition" in text


def test_one_milestone_owns_npx_skills_add() -> None:
    """`02-mvp.md` put `npx skills add` in 0.3 while the roadmap, the authority, put it in 0.6.

    A reader planning 0.3 from the MVP document scoped in work three milestones away — the same
    way the two documents once disagreed about digest coverage. The MVP document now defers to
    the roadmap explicitly; this pins the one item that was found drifting.
    """
    roadmap = (ROOT / "docs" / "design" / "03-roadmap.md").read_text(encoding="utf-8")
    owner = next(line for line in roadmap.splitlines() if "`npx skills add`" in line)
    assert owner.startswith("| 0.6 |"), "the roadmap row that names it is milestone 0.6"

    mvp = (ROOT / "docs" / "design" / "02-mvp.md").read_text(encoding="utf-8")
    milestone_03 = mvp.split('### 0.3 "Serve"', 1)[1].split("## 9.", 1)[0]
    for line in milestone_03.splitlines():
        if "`npx skills add`" in line:
            assert "0.6" in line, f"0.3 in 02-mvp.md claims `npx skills add` again: {line!r}"
    assert "the roadmap wins" in mvp, "02-mvp.md must defer to the roadmap explicitly"


def test_the_decision_time_skill_separates_review_from_the_mode() -> None:
    """`RETIRED_CLAIM` is an exact phrase, and rule 5 said the same thing in other words.

    `skills/license/SKILL.md` is the protocol an agent follows at the moment it decides, and it
    said a conflict or a missing licence "is `HUMAN`". The engine keeps the two apart:
    `human_review` is a flag that rides alongside a mode, and `decide(None, ...)` is `IDEAS_ONLY`
    with that flag, not `HUMAN`. Reading them as one mode stops a meal the engine allows —
    over-cautious rather than over-permissive, but wrong either way, and invisible to a sweep for
    one exact string.
    """
    assert decide(None, "MIT").mode is Mode.IDEAS_ONLY
    assert decide(None, "MIT").human_review

    text = (ROOT / "skills" / "license" / "SKILL.md").read_text(encoding="utf-8")
    assert "no license at all, is `HUMAN`" not in text, (
        "an absent licence is `IDEAS_ONLY` with human review; `HUMAN` is for a licence that was "
        "read and could not be classified"
    )
    assert "No license at all is `IDEAS_ONLY` with human review" in text
    assert "Review is a flag, not a mode" in text


def test_readme_configuration_tracks_maw_config_surface() -> None:
    """README configuration bullets must name every user-facing top-level maw key."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Configuration", 1)[1].split("\n## ", 1)[0]
    documented = {
        line.split("`", 2)[1] for line in section.splitlines() if line.startswith("- `")
    }

    defaults = set(yaml.safe_load(DEFAULT_CONFIG_TEXT))
    model = {item.name for item in fields(MawConfig)} - {"root", "exists", "raw"}
    assert documented == defaults == model
