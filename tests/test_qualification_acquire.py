"""Shared setup exports inert objects, never hooks or executable donor worktrees."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from qualification_acquire import copy_regular_tree, export_objects
from test_discovery_inputs import repository

from hungry_crab.attribution import GitSourceReader
from hungry_crab.cache import Slug


def test_object_export_keeps_real_pin_and_excludes_hooks(tmp_path: Path) -> None:
    source = "raise RuntimeError('do not execute donor code')\n"
    repo = repository(tmp_path / "donor", {"app.py": source})
    (repo / ".git/hooks").mkdir(exist_ok=True)
    (repo / ".git/hooks/post-checkout").write_text("NEVER EXECUTE OR EXPORT")
    pin = git(repo, "rev-parse", "HEAD").strip()
    output = tmp_path / "objects"
    amount = export_objects(repo, output, Slug("owner", "tool"), 65_536)
    assert 0 < amount < 65_536
    assert not (output / "hooks").exists() and not (output / "app.py").exists()
    assert GitSourceReader(output).read(pin, "app.py") == source
    assert "https://github.com/owner/tool" in (output / "config").read_text()


def test_export_budget_is_enforced_before_overflow_writes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "large").write_bytes(b"x" * 100)
    destination = tmp_path / "output"
    with pytest.raises(ValueError, match="byte budget"):
        copy_regular_tree(source, destination, 10)
    assert (destination / "large").stat().st_size <= 10
    with pytest.raises(ValueError, match="invalid"):
        copy_regular_tree(source, tmp_path / "negative", -1)


def test_export_refuses_links_instead_of_reading_outside(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("PRIVATE RECEIVING EVIDENCE")
    try:
        (source / "link").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="linked or special"):
        copy_regular_tree(source, tmp_path / "output", 100)
