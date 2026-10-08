from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import read_json, read_md, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import MD_BUDGET, SCHEMA, DigestOptions, DigestResult, run_digest
from hungry_crab.errors import CrabError
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
    "wiki.json",
    "wiki.md",
    "symbols.json",
    "symbols.md",
    "signals.json",
    "signals.md",
}


def test_registry_order_and_dependencies() -> None:
    names = list(MINER_NAMES)
    assert names[0] == "inventory"
    assert names[-1] == "traits"
    for miner in ALL_MINERS:
        for required in miner.requires:
            assert names.index(required) < names.index(miner.name)
    subset = [m.name for m in select_miners(["testing"])]
    assert subset == ["inventory", "deps", "ci", "testing"], "coverage in CI is a testing fact"
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
    # the inventory's coverage block is lifted to the manifest so a gate can read it (#76)
    coverage = manifest["coverage"]
    assert coverage["healthy"] is True
    assert coverage["files_seen"] == manifest["summary"]["files"]
    assert "examples" in coverage["excluded"]


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


def test_a_cached_digest_is_only_reused_for_the_same_question(
    npm_app: Path, tmp_path: Path
) -> None:
    """The commit is not the only input, and the `eat` protocol depends on this.

    Step 4 of the skill tells an agent that reads the maw as the wrong stack to add the offending
    paths to `ignore` and rerun. The commit does not move when `.crab.yml` changes, so the rerun
    used to return the cached answer and the remedy did nothing at all.
    """
    options = DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache")
    first = run_digest(Target(path=npm_app), options)
    assert not first.cached

    same = run_digest(Target(path=npm_app), DigestOptions(**options.__dict__))
    assert same.cached, "nothing changed, so nothing should be recomputed"

    ignored = run_digest(
        Target(path=npm_app), DigestOptions(**{**options.__dict__, "ignore": ["src/**"]})
    )
    assert not ignored.cached, "a different `ignore` is a different digest"
    assert ignored.manifest["ignore"] == ["src/**"]

    licensed = run_digest(
        Target(path=npm_app), DigestOptions(**{**options.__dict__, "maw_license": "GPL-3.0-only"})
    )
    assert not licensed.cached, "the license verdict inside the digest is maw-dependent"


def test_a_digest_from_an_older_crab_is_not_reused(npm_app: Path, tmp_path: Path) -> None:
    """Upgrading the crab has to change its answers, or `crab update` buys nothing."""
    options = DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache")
    run_digest(Target(path=npm_app), options)
    manifest_path = tmp_path / "out" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["crab_version"] = "0.0.1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert not run_digest(Target(path=npm_app), DigestOptions(**options.__dict__)).cached


def test_a_digest_without_a_coverage_record_is_not_reused(npm_app: Path, tmp_path: Path) -> None:
    """Builds share a development version; the missing coverage block dates a digest."""
    options = DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache")
    run_digest(Target(path=npm_app), options)
    manifest_path = tmp_path / "out" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["coverage"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    again = run_digest(Target(path=npm_app), DigestOptions(**options.__dict__))
    assert not again.cached
    assert isinstance(again.manifest["coverage"], dict)


def test_local_digest_defaults_to_the_maws_cache(npm_app: Path, tmp_path: Path) -> None:
    result = run_digest(
        Target(path=npm_app), DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache")
    )
    assert result.out_dir.is_relative_to(tmp_path / "cache" / "maws")
    assert result.out_dir.parent.name == result.manifest["prey"]["sha"]
    assert result.out_dir.parent.parent.name == ".generations"


def test_subset_of_miners(npm_app: Path, tmp_path: Path) -> None:
    result = run_digest(
        Target(path=npm_app),
        DigestOptions(out=tmp_path / "out", now=FIXED_NOW, miners=["license"]),
    )
    ran = [record["name"] for record in result.manifest["miners"]]
    assert ran == ["inventory", "license"]
    assert (result.out_dir / "license.json").is_file()
    assert not (result.out_dir / "ci.json").exists()


def test_explicit_output_preserves_unowned_registered_name_on_selective_run(
    npm_app: Path, tmp_path: Path
) -> None:
    out = tmp_path / "docs"
    out.mkdir()
    caller = out / "architecture.md"
    original = b"# Human architecture\n\nDo not replace me.\n"
    caller.write_bytes(original)

    result = run_digest(
        Target(path=npm_app),
        DigestOptions(out=out, now=FIXED_NOW, miners=["license"]),
    )

    assert result.out_dir == out
    assert caller.read_bytes() == original
    assert (out / "license.json").is_file()
    assert (out / "manifest.json").is_file()


def test_explicit_output_preserves_year_suffixed_caller_file(npm_app: Path, tmp_path: Path) -> None:
    out = tmp_path / "docs"
    out.mkdir()
    caller = out / "history.2024.md"
    original = b"# 2024 notes\n"
    caller.write_bytes(original)

    run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW))

    assert caller.read_bytes() == original


def test_explicit_output_refuses_to_replace_unowned_manifest(npm_app: Path, tmp_path: Path) -> None:
    out = tmp_path / "public"
    out.mkdir()
    manifest = out / "manifest.json"
    original = b'{"name":"web-app"}\n'
    manifest.write_bytes(original)

    with pytest.raises(CrabError, match=r"caller-owned file 'manifest\.json'"):
        run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW))

    assert manifest.read_bytes() == original


@pytest.mark.skipif(
    os.path.normcase("A") != os.path.normcase("a"), reason="case-sensitive platform"
)
def test_explicit_output_refuses_case_insensitive_collision(npm_app: Path, tmp_path: Path) -> None:
    out = tmp_path / "docs"
    out.mkdir()
    caller = out / "HISTORY.md"
    original = b"# Human history\n"
    caller.write_bytes(original)

    with pytest.raises(CrabError, match=r"HISTORY\.md"):
        run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW))

    assert caller.read_bytes() == original


def test_explicit_output_rerun_replaces_only_previous_manifest_owned_files(
    npm_app: Path, tmp_path: Path
) -> None:
    out = tmp_path / "docs"
    run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW))
    assert (out / "architecture.md").is_file()
    stranger = out / "notes.md"
    stranger.write_bytes(b"caller data\n")

    second = run_digest(
        Target(path=npm_app),
        DigestOptions(out=out, now=FIXED_NOW, miners=["license"]),
    )

    assert second.out_dir == out
    assert not (out / "architecture.md").exists()
    assert stranger.read_bytes() == b"caller data\n"
    assert {record["name"] for record in second.manifest["miners"]} == {"inventory", "license"}


@pytest.mark.parametrize("claimed_files", [None, "inventory.json", 7])
def test_explicit_output_refuses_malformed_ownership_without_mutation(
    npm_app: Path, tmp_path: Path, claimed_files: object
) -> None:
    out = tmp_path / "out"
    first = run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW))
    first.manifest["miners"][0]["files"] = claimed_files
    first.manifest_path.write_text(json.dumps(first.manifest), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in out.iterdir()}

    with pytest.raises(CrabError, match="caller-owned file"):
        run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW, force=True))

    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
    assert not list(tmp_path.glob(".out.crab-*"))


def test_explicit_output_collision_after_selective_digest_preserves_every_file(
    npm_app: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW, miners=["license"]))
    (out / "architecture.md").write_bytes(b"# Caller architecture\n")
    before = {path.name: path.read_bytes() for path in out.iterdir()}

    with pytest.raises(CrabError, match=r"caller-owned file 'architecture\.md'"):
        run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW, force=True))

    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
    assert not list(tmp_path.glob(".out.crab-*"))


def test_explicit_output_rerun_preserves_unlisted_page_family_members(
    npm_app: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW))
    caller = out / "history.2024.md"
    caller.write_bytes(b"# Caller history\n")

    result = run_digest(
        Target(path=npm_app),
        DigestOptions(out=out, now=FIXED_NOW, force=True, budget_policy="enforce", total_budget=0),
    )

    assert caller.read_bytes() == b"# Caller history\n"
    assert result.manifest["markdown_tokens_est"] == 0
    assert "history.2024.md" not in {entry["name"] for entry in result.manifest["files"]}


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
