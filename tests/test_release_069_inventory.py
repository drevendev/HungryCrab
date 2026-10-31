"""Literal source classification/dependency facts and explicit unsupported coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hungry_crab.errors import CrabError
from hungry_crab.miners.attributes import classify_attributes
from hungry_crab.miners.inventory import describe_file
from hungry_crab.miners.ruby_lock import ruby_lock
from hungry_crab.publication_policy import parse_publication_allow


@pytest.mark.parametrize(
    ("header", "language"),
    [
        ("#!/usr/bin/env python3", "Python"),
        ("#!/bin/sh", "Shell"),
        ("#!/usr/bin/env -S python3 -u", "Python"),
        ("#!python", None),
        ("#!/bin/sh; echo unsafe", None),
        ("a" * 300, None),
    ],
)
def test_bounded_shebang_reads_without_execution(
    tmp_path: Path, header: str, language: str | None
) -> None:
    path = tmp_path / "script"
    path.write_text(header + "\nraise RuntimeError('must never run')\n", encoding="utf-8")
    fact = describe_file(
        str(path), "script", "script", 0, path.stat().st_size, vendored=False, build_output=False
    )
    assert fact.language == language


def test_attribute_precedence_and_unsupported_patterns(tmp_path: Path) -> None:
    path = tmp_path / "owned.py"
    path.write_text("# inert", encoding="utf-8")
    fact = describe_file(
        str(path), "owned.py", "owned.py", 0, 7, vendored=False, build_output=False
    )
    omissions = classify_attributes(
        "*.py linguist-generated\nowned.py -linguist-generated linguist-vendored\n"
        "[x].py linguist-generated\n",
        [fact],
    )
    assert not fact.generated and fact.vendored
    assert fact.classification_origin["vendored"] == ".gitattributes:2"
    assert omissions == [{"line": 3, "reason": "unsupported-pattern"}]


def test_attribute_globs_reset_and_declarations_never_waive_safety_floors(tmp_path: Path) -> None:
    facts = []
    for relative, vendored in (
        ("src/a.py", False),
        ("src/nested/b.py", False),
        ("vendor/c.py", True),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# inert", encoding="utf-8")
        facts.append(
            describe_file(
                str(path), relative, path.name, 0, 7, vendored=vendored, build_output=False
            )
        )
    classify_attributes("src/*.py linguist-generated\nvendor/** -linguist-vendored\n", facts)
    assert facts[0].generated and not facts[1].generated
    assert facts[2].vendored and facts[2].classification_declared["vendored"] is False
    classify_attributes("*.py linguist-generated\nsrc/** !linguist-generated\n", facts[1:2])
    assert not facts[1].generated and facts[1].classification_declared["generated"] is None


def test_literal_lock_versions_directness_and_unsupported_sources() -> None:
    rows, meta = ruby_lock(
        "GEM\n  remote: https://rubygems.org/\n  specs:\n    direct (1.2.3)\n"
        "      transit (>= 1)\n    transit (2.0.0)\n\nDEPENDENCIES\n  direct (~> 1.2)\n"
        "GIT\n  remote: never-executed\n",
        "Gemfile.lock",
    )
    assert [(r["name"], r["spec"], r["kind"]) for r in rows] == [
        ("direct", "1.2.3", "runtime"),
        ("transit", "2.0.0", "indirect"),
    ]
    assert meta["unsupported"] == ["git-source"]


def test_exact_exception_expiry_and_invalid_date() -> None:
    entry = {
        "finding": "a" * 64,
        "reason": "owned false-positive review",
        "expires_at": "2026-11-01T12:00:00Z",
    }
    assert parse_publication_allow([entry], now=datetime(2026, 11, 1, 11, tzinfo=UTC)) == (
        "a" * 64,
    )
    assert parse_publication_allow([entry], now=datetime(2026, 11, 1, 12, tzinfo=UTC)) == ()
    with pytest.raises(CrabError, match="expires_at"):
        parse_publication_allow([{**entry, "expires_at": "tomorrow"}])
