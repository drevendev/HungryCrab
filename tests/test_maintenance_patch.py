"""Regression controls for the bounded 0.6.10 maintenance contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from helpers import write_tree

from hungry_crab import cli, digest
from hungry_crab.cache import Slug, Target, resolve_target
from hungry_crab.compare import compare_for_maw, load_menu, menu_candidates
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.git import GitRunner
from hungry_crab.miners import inventory
from hungry_crab.miners.inventory import coverage_block, describe_coverage, walk_tree


def test_vendor_omission_is_a_lower_bound_without_walking_pruned_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inventory, "MAX_VENDORED_PER_DIR", 3)
    root = write_tree(
        tmp_path / "tree",
        {
            **{f"node_modules/a/a{n}.js": "x\n" for n in range(3)},
            "node_modules/b/index.js": "x\n",
            **{f"node_modules/b/lib/b{n}.js": "x\n" for n in range(10)},
        },
    )
    files, stats = walk_tree(root, max_files=100)
    coverage = coverage_block(files, stats, 0)
    assert coverage["loss"]["vendored_capped_files"] == 1
    assert coverage["loss"]["vendored_capped_files_at_least"] == 1
    assert coverage["loss"]["vendored_capped_total_known"] is False
    assert "at least 1" in describe_coverage(coverage)
    assert "total unknown" in describe_coverage(coverage)
    assert coverage["healthy"] is True


def test_build_outputs_share_a_reason_and_generated_files_keep_theirs(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "tree",
        {
            "Cargo.toml": '[package]\nname="app"\n',
            "dist/a.js": "x\n",
            "target/a.rs": "x\n",
            "src/a.min.js": "x\n",
        },
    )
    files, stats = walk_tree(root, max_files=100)
    inventory.mark_build_outputs(files)
    coverage = coverage_block(files, stats, 0)
    assert coverage["excluded"] == {"build-output": 2, "generated": 1}


@pytest.mark.parametrize(
    "options",
    [
        DigestOptions(miners=["not-a-miner"]),
        DigestOptions(md_budget=0),
        DigestOptions(total_budget=-1),
        DigestOptions(depth="unknown"),
        DigestOptions(catch_options=CatchOptions(since="invalid")),
    ],
)
def test_invalid_digest_request_never_prepares_a_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: DigestOptions
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid options reached context preparation")

    monkeypatch.setattr(digest, "prepare_context", forbidden)
    with pytest.raises(UsageError):
        run_digest(Target(slug=Slug("example", "prey")), options)
    assert not list(tmp_path.iterdir())


def test_invalid_since_is_refused_before_maw_issue_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid history window reached config or provider")

    monkeypatch.setattr(cli.MawConfig, "load", forbidden)
    with pytest.raises(UsageError):
        compare_for_maw(
            Target(slug=Slug("example", "prey")),
            tmp_path,
            digest_options=DigestOptions(catch_options=CatchOptions(since="bad")),
            issue_lookup=forbidden,
        )


def test_cached_history_options_are_explained_without_refreshing_history(
    npm_app: Path, tmp_path: Path
) -> None:
    slug = Slug("example", "cached-history")
    cache = tmp_path / "cache"
    acquired = catch(slug, cache_root=cache, source_url=npm_app.as_uri())
    logs: list[str] = []
    result = run_digest(
        Target(slug=slug),
        DigestOptions(
            out=tmp_path / "digest",
            cache_root=cache,
            miners=["inventory"],
            catch_options=CatchOptions(shallow=True, since="90d"),
        ),
        log=logs.append,
    )
    assert any("already cached" in line and "crab catch" in line for line in logs)
    assert result.manifest["prey"]["sha"] == acquired.sha
    assert not GitRunner(Path(acquired.repo_dir)).is_shallow()


def test_compare_destination_is_printed_once_and_library_progress_survives(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "--cache-dir",
                str(tmp_path / "cache"),
                "compare",
                str(npm_app),
                "--maw",
                str(pyproject_cli),
                "--no-issues",
                "--no-ledger",
                "--top",
                "1",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count("meal written to") == 1
    assert "meal written to" in captured.out
    assert "compared " in captured.err


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {"category": "ci", "key": "k", "title": "secret"},
        {"category": "ci", "key": "k", "title": "secret", "what": "w", "trace": []},
        {"category": "ci", "key": "k", "title": "secret", "what": "w", "score": "secret"},
        {"category": "ci", "key": "k", "title": "secret", "what": "w", "evidence": [False]},
    ],
)
def test_damaged_menu_is_an_error_without_private_candidate_contents(
    tmp_path: Path, row: object
) -> None:
    path = tmp_path / "menu.json"
    path.write_text(json.dumps({"candidates": [row]}), encoding="utf-8")
    with pytest.raises(CrabError) as error:
        load_menu(tmp_path)
    assert str(path) in str(error.value)
    assert "secret" not in str(error.value)


def test_absent_truncated_and_valid_legacy_menu_are_distinct(tmp_path: Path) -> None:
    assert load_menu(tmp_path) is None
    path = tmp_path / "menu.json"
    path.write_text('{"candidates": [', encoding="utf-8")
    with pytest.raises(CrabError, match="cannot read saved menu"):
        load_menu(tmp_path)
    path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "category": "ci",
                        "key": "ci.cache",
                        "title": "Cache",
                        "what": "cache",
                        "future": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    menu = load_menu(tmp_path)
    assert menu is not None
    assert menu_candidates(menu)[0].id == "crab:ci:ci.cache"


@pytest.mark.parametrize("action", ["show", "mark"])
def test_ledger_maw_placement_and_conflicts(
    tmp_path: Path, action: str, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    suffix = ["crab:ci:ci.cache", "rejected"] if action == "mark" else []
    codes = []
    for args in (
        ["ledger", "--maw", str(maw), action, *suffix],
        ["ledger", action, *suffix, "--maw", str(maw)],
    ):
        codes.append(cli.main(args))
    assert codes[0] == codes[1] == (1 if action == "mark" else 0)
    capsys.readouterr()
    assert cli.main(["ledger", "--maw", str(maw), action, *suffix, "--maw", str(tmp_path)]) == 2
    assert "conflicting --maw" in capsys.readouterr().err


def test_disabled_ledger_mark_diagnoses_policy_without_creating_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".crab.yml").write_text("ledger: none\n", encoding="utf-8")
    assert cli.main(["ledger", "mark", "crab:ci:ci.cache", "rejected", "--maw", str(tmp_path)]) == 2
    assert "persistence is disabled" in capsys.readouterr().err
    assert not (tmp_path / ".crab").exists()


def test_init_directory_and_digest_file_outputs_preserve_existing_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".crab.yml"
    config.mkdir()
    sentinel = config / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    assert cli.main(["init", "--force", "--maw", str(tmp_path)]) == 1
    assert "cannot write configuration" in capsys.readouterr().err
    assert sentinel.read_text() == "keep"
    output = tmp_path / "notes.txt"
    output.write_text("keep", encoding="utf-8")
    assert cli.main(["digest", str(tmp_path), "--out", str(output)]) == 2
    assert "--out must name a directory" in capsys.readouterr().err
    assert output.read_text() == "keep"


@pytest.mark.skipif(os.name == "nt", reason="Windows aliases trailing-space directory names")
def test_original_local_path_whitespace_wins_over_normalization(tmp_path: Path) -> None:
    ordinary = tmp_path / "project"
    spaced = tmp_path / "project "
    ordinary.mkdir()
    with pytest.raises(UsageError):
        resolve_target(str(spaced))
    spaced.mkdir()
    assert resolve_target(str(spaced)).path == spaced.resolve()
    assert resolve_target(str(ordinary)).path == ordinary.resolve()
    assert resolve_target(" example/prey ").slug == Slug("example", "prey")
    with pytest.raises(UsageError):
        resolve_target("   ")


def test_output_permission_failures_are_bounded_and_preserve_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hungry_crab import maw

    config = tmp_path / ".crab.yml"
    config.write_text("ledger: none\n", encoding="utf-8")

    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError("PRIVATE_SYNTHETIC_CONTENT")

    monkeypatch.setattr(maw, "atomic_text", denied)
    assert cli.main(["init", "--force", "--maw", str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert "cannot write configuration" in error
    assert "PRIVATE_SYNTHETIC_CONTENT" not in error and "Traceback" not in error
    assert config.read_text(encoding="utf-8") == "ledger: none\n"

    monkeypatch.setattr(digest.tempfile, "mkdtemp", denied)
    assert cli.main(["digest", str(tmp_path), "--out", str(tmp_path / "output")]) == 1
    error = capsys.readouterr().err
    assert "cannot prepare digest output" in error and "Traceback" not in error
