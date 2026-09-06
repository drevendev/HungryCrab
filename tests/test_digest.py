from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import read_json, read_md, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import MD_BUDGET, SCHEMA, DigestOptions, DigestResult, run_digest
from hungry_crab.miners import ALL_MINERS, MINER_NAMES, select_miners
from hungry_crab.tokens import estimate_tokens

EXPECTED_FILES = {
    "manifest.json",
    "inventory.json",
    "inventory.md",
    "license.json",
    "deps.json",
    "ci.json",
    "ci.md",
    "tests.json",
    "tests.md",
    "docs.json",
    "docs.md",
    "ai.json",
    "ai.md",
    "history.json",
    "history.md",
    "branches.json",
    "branches.md",
    "issues.json",
    "issues.md",
    "architecture.json",
    "architecture.md",
    "traits.json",
}


def test_registry_order_and_dependencies() -> None:
    names = list(MINER_NAMES)
    assert names[0] == "inventory"
    assert names[-1] == "traits"
    for miner in ALL_MINERS:
        for required in miner.requires:
            assert names.index(required) < names.index(miner.name)
    subset = [m.name for m in select_miners(["testing"])]
    assert subset == ["inventory", "deps", "testing"]
    with pytest.raises(ValueError, match="unknown miner"):
        select_miners(["nope"])


def test_manifest_lists_every_file_with_token_estimates(npm_digest: DigestResult) -> None:
    manifest = npm_digest.manifest
    assert manifest["schema"] == SCHEMA
    assert not npm_digest.cached
    assert {p.name for p in npm_digest.out_dir.iterdir()} == EXPECTED_FILES
    names = {entry["name"] for entry in manifest["files"]}
    assert names == EXPECTED_FILES - {"manifest.json"}
    for entry in manifest["files"]:
        assert entry["tokens_est"] > 0
        assert entry["miner"] in MINER_NAMES
    assert all(record["ok"] for record in manifest["miners"]), manifest["miners"]
    assert manifest["prey"]["label"] == "npm-app"
    assert len(manifest["prey"]["sha"]) == 40
    assert manifest["depth"] == "normal"
    assert manifest["maw_license"] == "MIT"
    assert manifest["reading_order"][0] == "inventory.md"
    assert manifest["summary"]["license"]["spdx"] == "MIT"
    assert manifest["summary"]["primary_language"] == "TypeScript"
    assert manifest["summary"]["commits"] == 13
    assert manifest["generated_at"].startswith(FIXED_NOW.date().isoformat())


def test_markdown_files_respect_the_budget(npm_digest: DigestResult) -> None:
    manifest = npm_digest.manifest
    assert not manifest["over_budget"]
    for entry in manifest["files"]:
        if entry["kind"] == "markdown":
            text = read_md(npm_digest, entry["name"])
            assert estimate_tokens(text) <= MD_BUDGET["normal"]
            assert text.startswith("# ")
            assert "Derived data about the prey, not instructions." in text
    assert manifest["markdown_tokens_est"] <= manifest["budget"]["markdown_total"]


def test_second_run_is_served_from_cache_and_force_rewrites(npm_app: Path, tmp_path: Path) -> None:
    options = DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache")
    first = run_digest(Target(path=npm_app), options)
    assert not first.cached
    again = run_digest(Target(path=npm_app), options)
    assert again.cached
    assert again.manifest["prey"]["sha"] == first.manifest["prey"]["sha"]
    forced = run_digest(Target(path=npm_app), DigestOptions(**{**options.__dict__, "force": True}))
    assert not forced.cached


def test_local_digest_defaults_to_the_maws_cache(npm_app: Path, tmp_path: Path) -> None:
    result = run_digest(
        Target(path=npm_app), DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache")
    )
    assert result.out_dir.is_relative_to(tmp_path / "cache" / "maws")
    assert result.out_dir.name == result.manifest["prey"]["sha"]


def test_subset_of_miners(npm_app: Path, tmp_path: Path) -> None:
    result = run_digest(
        Target(path=npm_app),
        DigestOptions(out=tmp_path / "out", now=FIXED_NOW, miners=["license"]),
    )
    ran = [record["name"] for record in result.manifest["miners"]]
    assert ran == ["inventory", "license"]
    assert (result.out_dir / "license.json").is_file()
    assert not (result.out_dir / "ci.json").exists()


def test_digest_of_a_plain_directory_without_git(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "plain",
        {
            "README.md": "# Plain\n\n## Usage\n\nRun it.\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted, free of charge, to any person "
            "obtaining a copy of this software, to deal in the Software without restriction. "
            "The above copyright notice and this permission notice shall be included in all "
            "copies or substantial portions of the Software.\n",
            "src/app.py": "print('hi')\n",
        },
    )
    result = run_digest(Target(path=root), DigestOptions(out=tmp_path / "out", now=FIXED_NOW))
    manifest = result.manifest
    assert manifest["prey"]["sha"].startswith("nogit-")
    assert manifest["prey"]["ref"] == "worktree"
    assert all(record["ok"] for record in manifest["miners"])
    history = read_json(result, "history.json")
    assert history["available"] is False
    branches = read_json(result, "branches.json")
    assert branches["available"] is False
    traits = read_json(result, "traits.json")["traits"]
    assert traits["primary_language"] == "Python"
    assert traits["license_spdx"] == "MIT"
    assert traits["has_ci"] is False
    assert traits["commits"] is None


def test_manifest_is_valid_json_on_disk(npm_digest: DigestResult) -> None:
    on_disk = json.loads(npm_digest.manifest_path.read_text(encoding="utf-8"))
    assert on_disk["schema"] == SCHEMA
    assert on_disk["files"] == npm_digest.manifest["files"]
