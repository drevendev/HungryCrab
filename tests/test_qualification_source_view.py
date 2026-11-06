"""A CI bare export must become readable through published 0.6.8 relative paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixture_builder import git
from qualification_source_view import source_view
from test_discovery_inputs import repository


def test_inert_view_supports_old_reader_syntax_without_running_hooks(tmp_path: Path) -> None:
    source = repository(
        tmp_path / "source",
        {
            "unindexed.yaml": "owned: declaration\n",
            ".gitattributes": "unindexed.yaml export-ignore\n",
            "program.py": "raise AssertionError('DONOR MUST NEVER EXECUTE')\n",
        },
    )
    git(source, "remote", "add", "origin", "https://github.com/owned/fixture")
    sha = git(source, "rev-parse", "HEAD").strip()
    bare = tmp_path / "bare"
    import shutil

    shutil.copytree(source / ".git", bare)
    git(bare, "config", "core.bare", "true")
    import subprocess

    unsupported = subprocess.run(
        ["git", "-C", str(bare), "cat-file", "-s", sha + ":./unindexed.yaml"], capture_output=True
    )
    assert unsupported.returncode != 0
    view = tmp_path / "view"
    record = source_view(bare, sha, view)
    assert git(view, "show", sha + ":./unindexed.yaml") == "owned: declaration\n"
    assert (view / "unindexed.yaml").read_text() == "owned: declaration\n"
    assert record["donor_executed"] is False
    assert record["tree"] == git(source, "rev-parse", "HEAD^{tree}").strip()
    with pytest.raises(FileExistsError):
        source_view(bare, sha, view)
