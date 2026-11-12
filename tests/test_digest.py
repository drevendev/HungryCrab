from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from fixture_builder import git as fixture_git
from helpers import read_json, read_md, write_tree

from hungry_crab import digest as digest_module
from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.digest import (
    MD_BUDGET,
    SCHEMA,
    DigestOptions,
    DigestResult,
    prepare_context,
    run_digest,
)
from hungry_crab.errors import CrabError, ExternalCommandError
from hungry_crab.fetch.git import GitRunner
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
    "ci_relations.json",
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


def test_same_version_pre_review_licence_policy_cache_is_remined(
    npm_app: Path, tmp_path: Path
) -> None:
    options = DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache")
    first = run_digest(Target(path=npm_app), options)
    manifest_path = first.out_dir / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    old["analysis_identity"]["license_provenance"] = 2
    manifest_path.write_text(json.dumps(old), encoding="utf-8")
    refreshed = run_digest(Target(path=npm_app), DigestOptions(**options.__dict__))
    assert not refreshed.cached
    assert refreshed.manifest["crab_version"] == old["crab_version"]
    assert refreshed.manifest["prey"]["sha"] == old["prey"]["sha"]
    assert run_digest(Target(path=npm_app), DigestOptions(**options.__dict__)).cached


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


def test_cached_prey_without_a_head_refuses_non_git_digest(tmp_path: Path) -> None:
    slug = Slug("example", "incomplete-prey")
    options = DigestOptions(cache_root=tmp_path / "cache", now=FIXED_NOW)
    paths = prey_paths(slug, options.cache_root)
    paths.repo.mkdir(parents=True)
    fixture_git(paths.repo, "init", "-b", "main")
    assert (paths.repo / ".git").is_dir()

    with pytest.raises(CrabError, match="no valid Git HEAD") as exc:
        run_digest(Target(slug=slug), options)
    assert exc.value.hint is not None
    assert f"crab catch {slug} --force" in exc.value.hint
    assert not paths.digests.exists()


@pytest.mark.parametrize("has_git_marker", [False, True])
def test_git_spawn_failure_is_not_misclassified_as_plain_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_git_marker: bool
) -> None:
    root = write_tree(tmp_path / "prey", {"README.md": "# Prey\\n"})
    if has_git_marker:
        (root / ".git").mkdir()

    def fail_git_launch(_self: GitRunner, *_args: str, **_kwargs: object) -> str:
        raise ExternalCommandError("failed to run git: [WinError 267]") from OSError(
            "synthetic process creation failure"
        )

    monkeypatch.setattr(GitRunner, "run", fail_git_launch)
    requested = tmp_path / "digest-output"
    with pytest.raises(CrabError, match="cannot launch Git") as caught:
        run_digest(Target(path=root), DigestOptions(out=requested, now=FIXED_NOW))

    assert "[WinError 267]" in str(caught.value)
    assert caught.value.hint is not None
    assert "shorten long Windows paths" in caught.value.hint
    assert isinstance(caught.value.__cause__, ExternalCommandError)
    assert isinstance(caught.value.__cause__.__cause__, OSError)
    assert not requested.exists()


def test_git_head_spawn_failure_is_not_treated_as_non_git(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first Git probe succeeds; a later failed process start must not be
    # interpreted as a repository without commits.
    original_run = GitRunner.run

    def fail_only_head_probe(
        self: GitRunner,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
        cwd: Path | None = None,
        stdout_limit: int | None = None,
    ) -> str:
        if args == ("rev-parse", "--verify", "-q", "HEAD"):
            raise ExternalCommandError("failed to run git: [WinError 267]") from OSError(
                "synthetic HEAD process creation failure"
            )
        return original_run(
            self, *args, check=check, timeout=timeout, cwd=cwd, stdout_limit=stdout_limit
        )

    monkeypatch.setattr(GitRunner, "run", fail_only_head_probe)
    requested = tmp_path / "digest-output"
    with pytest.raises(CrabError, match="cannot launch Git while verifying HEAD") as caught:
        run_digest(Target(path=npm_app), DigestOptions(out=requested, now=FIXED_NOW))

    assert "[WinError 267]" in str(caught.value)
    assert isinstance(caught.value.__cause__, ExternalCommandError)
    assert isinstance(caught.value.__cause__.__cause__, OSError)
    assert not requested.exists()


@pytest.mark.parametrize(
    ("os_error", "expected_hint"),
    [
        (errno.EACCES, "permissions"),
        (errno.ENAMETOOLONG, "LongPathsEnabled"),
    ],
)
def test_digest_write_oserror_reports_failed_artifact_path(
    npm_app: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    os_error: int,
    expected_hint: str,
) -> None:
    original_write_json = digest_module._write_json
    failed_path: str | None = None

    def fail_manifest_write(path: Path, value: dict[str, object]) -> None:
        nonlocal failed_path
        if path.name == "manifest.json":
            failed_path = str(path)
            raise OSError(os_error, "synthetic artifact write failure", failed_path)
        original_write_json(path, value)

    monkeypatch.setattr(digest_module, "_write_json", fail_manifest_write)
    requested = tmp_path / "owned-output"
    with pytest.raises(CrabError, match="cannot write digest output") as caught:
        run_digest(Target(path=npm_app), DigestOptions(out=requested, now=FIXED_NOW))

    assert failed_path is not None
    assert failed_path in str(caught.value)
    assert caught.value.hint is not None
    assert expected_hint in caught.value.hint
    if os_error == errno.ENAMETOOLONG:
        assert "CRAB_CACHE_DIR" in caught.value.hint
    assert isinstance(caught.value.__cause__, OSError)
    assert not requested.exists()


def test_digest_of_a_plain_directory_without_git(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "plain",
        {
            "README.md": "# Plain\n\n## Usage\n\nRun it.\n",
            "LICENSE": (Path(__file__).parents[1] / "LICENSE").read_text(encoding="utf-8"),
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


@pytest.mark.parametrize("name", ["architecture.md", "ARCHITECTURE.md"])
def test_orphan_manifest_entry_never_grants_output_ownership(
    npm_app: Path, tmp_path: Path, name: str
) -> None:
    out = tmp_path / "out"
    run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW, miners=["license"]))
    caller = out / name
    original = b"# Caller architecture\n"
    caller.write_bytes(original)
    path = out / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {"name": "architecture.md", "miner": "architecture", "bytes": len(original)}
    )
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(CrabError, match="caller-owned"):
        run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW, force=True))
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert caller.read_bytes() == original


def test_explicit_output_rerun_refuses_case_variant_stale_artifact(
    npm_app: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run_digest(Target(path=npm_app), DigestOptions(out=out, now=FIXED_NOW))
    (out / "history.md").unlink()
    caller = out / "HISTORY.md"
    caller.write_bytes(b"# Human history\n")
    before = {path.name: path.read_bytes() for path in out.iterdir()}

    with pytest.raises(CrabError, match=r"caller-owned file 'HISTORY\.md'"):
        run_digest(
            Target(path=npm_app),
            DigestOptions(out=out, now=FIXED_NOW, miners=["license"], force=True),
        )

    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
    assert caller.read_bytes() == b"# Human history\n"
    assert not list(tmp_path.glob(".out.crab-*"))


def test_in_tree_output_does_not_expose_staging_to_inventory(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "prey", {"app.py": "VALUE = 1\n", "README.md": "# App\n"})
    baseline = run_digest(
        Target(path=root),
        DigestOptions(out=tmp_path / "outside", now=FIXED_NOW, miners=["inventory"]),
    )
    nested = run_digest(
        Target(path=root),
        DigestOptions(out=root / "new-parent" / "report", now=FIXED_NOW, miners=["inventory"]),
    )

    assert (
        read_json(nested, "inventory.json")["top_level"]
        == read_json(baseline, "inventory.json")["top_level"]
    )
    assert not list(root.rglob("*.crab-*"))


@pytest.mark.parametrize("bad_entry", ["refs-file", "ref-directory"])
def test_force_rejects_unsafe_ref_before_mining(
    npm_app: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_entry: str,
) -> None:
    options = DigestOptions(force=True, cache_root=tmp_path / "cache")
    ctx, requested_out_dir = prepare_context(Target(path=npm_app), options)
    digests_dir = requested_out_dir.parent
    refs = digests_dir / ".refs"
    if bad_entry == "refs-file":
        refs.parent.mkdir(parents=True, exist_ok=True)
        refs.write_text("unowned root sentinel\n", encoding="utf-8")
        sentinel = refs
    else:
        refs.mkdir(parents=True)
        ref_entry = refs / f"{ctx.sha}.json"
        ref_entry.mkdir()
        sentinel = ref_entry / "keep-me.txt"
        sentinel.write_text("unowned ref sentinel\n", encoding="utf-8")

    def forbidden_mining(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unsafe ref preflight must reject before running miners")

    monkeypatch.setattr(digest_module, "run_miners", forbidden_mining)
    with pytest.raises(CrabError, match="unsafe digest ref") as exc:
        run_digest(Target(path=npm_app), options)
    assert exc.value.hint is not None and "manually" in exc.value.hint
    assert sentinel.read_text(encoding="utf-8").startswith("unowned")
    assert not (digests_dir / ".generations").exists()
