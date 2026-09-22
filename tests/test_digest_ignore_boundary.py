"""A local path is not the maw merely because it is local (#128).

`.crab.yml` is control-plane configuration. It reaches a digest only through an explicit
`--maw`, and its `ignore` list applies only when the target is that maw; a local prey's own
`.crab.yml` is data, never read, so it can neither hide files nor abort the digest.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import copy_repo, write_tree

from hungry_crab.cache import Target
from hungry_crab.cli import main
from hungry_crab.compare import compare_for_maw
from hungry_crab.digest import DigestOptions
from hungry_crab.maw import CONFIG_FILE

HIDING_CONFIG = "ignore:\n  - src/**\n"


def _prey_with_config(source: Path, where: Path, config: str) -> Path:
    shutil.copytree(source, where)
    (where / CONFIG_FILE).write_text(config, encoding="utf-8", newline="\n")
    return where


def _digest_manifest(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    code = main(["-q", *argv, "--json"])
    assert code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert isinstance(manifest, dict)
    return manifest


def _top_level(out: Path) -> set[str]:
    inventory = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
    return {str(row["path"]) for row in inventory["top_level"]}


def test_local_prey_cannot_hide_its_files_through_its_own_config(
    npm_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prey = _prey_with_config(npm_app, tmp_path / "prey", HIDING_CONFIG)
    out = tmp_path / "digest"

    manifest = _digest_manifest(
        ["--cache-dir", str(tmp_path / "cache"), "digest", str(prey), "--out", str(out)], capsys
    )

    assert manifest["ignore"] == []
    assert "src" in _top_level(out)


def test_the_maw_itself_keeps_its_ignore_list(
    npm_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = _prey_with_config(npm_app, tmp_path / "maw", HIDING_CONFIG)
    out = tmp_path / "digest"

    manifest = _digest_manifest(
        [
            "--cache-dir",
            str(tmp_path / "cache"),
            "digest",
            str(maw),
            "--maw",
            str(maw),
            "--out",
            str(out),
        ],
        capsys,
    )

    assert manifest["ignore"] == ["src/**"]
    assert "src" not in _top_level(out)


def test_the_maw_ignore_list_does_not_apply_to_a_foreign_local_prey(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = _prey_with_config(pyproject_cli, tmp_path / "maw", HIDING_CONFIG)
    prey = tmp_path / "prey"
    shutil.copytree(npm_app, prey)
    out = tmp_path / "digest"

    manifest = _digest_manifest(
        [
            "--cache-dir",
            str(tmp_path / "cache"),
            "digest",
            str(prey),
            "--maw",
            str(maw),
            "--out",
            str(out),
        ],
        capsys,
    )

    assert manifest["ignore"] == []
    assert "src" in _top_level(out)


@pytest.mark.parametrize(
    "config",
    [
        "mode: bogus\n",
        "hunger:\n  ci: maybe\n",
        "budget:\n  policy: sometimes\n",
        "ledger: nowhere\n",
        "appetite: {}\n",
        "ignore: [\n",
    ],
    ids=["mode", "hunger", "budget", "ledger", "appetite", "yaml"],
)
def test_a_broken_config_in_a_local_prey_does_not_abort_its_digest(
    npm_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], config: str
) -> None:
    prey = _prey_with_config(npm_app, tmp_path / "prey", config)

    manifest = _digest_manifest(
        [
            "--cache-dir",
            str(tmp_path / "cache"),
            "digest",
            str(prey),
            "--out",
            str(tmp_path / "digest"),
        ],
        capsys,
    )

    assert manifest["ignore"] == []


def test_compare_reads_only_the_maw_config(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    """The prey side of `crab compare` used to load the local prey's `.crab.yml` as well."""
    prey = _prey_with_config(npm_app, tmp_path / "prey", HIDING_CONFIG)
    maw = copy_repo(pyproject_cli, tmp_path / "maw")
    write_tree(maw, {CONFIG_FILE: "ignore:\n  - tests/fixtures/**\nledger: none\n"})

    result, prey_digest, _, config = compare_for_maw(
        Target(path=prey),
        maw,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache"),
        now=FIXED_NOW,
    )

    assert config.ignore == ["tests/fixtures/**"]
    assert prey_digest.manifest["ignore"] == [], "the prey keeps its whole tree"
    assert result.prey.trait("ecosystems") == ["npm"]
