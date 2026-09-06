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

from pathlib import Path

import pytest

from hungry_crab.licensing import Mode, decide

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
