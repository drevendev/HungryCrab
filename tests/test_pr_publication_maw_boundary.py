from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.errors import CrabError
from hungry_crab.pr_publication import (
    CleanroomImplementationReceipt,
    publication_handoff_from_receipt,
)


def _receipt(path: str = "generated/cache.yml") -> CleanroomImplementationReceipt:
    return CleanroomImplementationReceipt(
        nutrient_id="crab:ci:cache",
        changed_paths=(path,),
        summary="implemented from a specification, without access to the prey source",
        checks=("pytest -q",),
    )


def test_receipt_filesystem_boundary_reads_only_resolved_in_maw_file(tmp_path: Path) -> None:
    maw = tmp_path / "maw"
    generated = maw / "generated"
    generated.mkdir(parents=True)
    target = generated / "cache.yml"
    target.write_text("cache: true\n", encoding="utf-8")

    handoff = publication_handoff_from_receipt(_receipt(), maw)

    assert [declared.path for declared in handoff.files] == ["generated/cache.yml"]


def test_receipt_filesystem_boundary_rejects_symlink_escape(tmp_path: Path) -> None:
    maw = tmp_path / "maw"
    generated = maw / "generated"
    generated.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("must not become publication bytes\n", encoding="utf-8")
    alias = generated / "cache.yml"
    try:
        alias.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation unavailable on this runner: {exc}")

    with pytest.raises(CrabError, match="invalid clean-room implementation receipt") as raised:
        publication_handoff_from_receipt(_receipt(), maw)

    assert raised.value.hint == "declared changed file resolves outside the maw: generated/cache.yml"


def test_receipt_filesystem_boundary_rejects_unproven_reader(tmp_path: Path) -> None:
    maw = tmp_path / "maw"
    generated = maw / "generated"
    generated.mkdir(parents=True)
    target = generated / "cache.yml"
    target.write_text("cache: true\n", encoding="utf-8")

    with pytest.raises(CrabError, match="invalid clean-room implementation receipt") as raised:
        publication_handoff_from_receipt(_receipt(), lambda path: (maw / path).read_text())  # type: ignore[arg-type]

    assert raised.value.hint == (
        "maw source must be a root Path so resolved containment can be proven before reading"
    )
