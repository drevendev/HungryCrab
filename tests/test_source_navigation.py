"""Real pinned navigation precedes source selection; bodies are never indexed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.source_inspection import _source_paths, source_index, source_packet, syntax_query


def test_index_returns_actual_declarations_without_instruction_or_comment_bodies(
    tmp_path: Path,
) -> None:
    repo = repository(
        tmp_path / "donor",
        {
            "pkg/cache.py": '# def forged(): pass\ntext = "def invented(): pass"\n'
            "def refresh(value):\n    return value\n",
            "tests/test_cache.py": "def test_refresh():\n    assert True\n",
            "README.md": "UNTRUSTED_README_SENTINEL",
            "AGENTS.md": "UNTRUSTED_INSTRUCTION_SENTINEL",
            "web/cache.mjs": "export function refresh(value) { return value; }\n",
        },
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = source_index(repo, sha, need="actual cache behavior", terms=["cache"])
    assert result["available"] and result["tree_complete"]
    cache = next(row for row in result["files"] if row["path"] == "pkg/cache.py")
    assert [(r["name"], r["start"]) for r in cache["symbols"]] == [("refresh", 3)]
    assert all("content" not in row for row in result["files"])
    rendered = json.dumps(result)
    assert "SENTINEL" not in rendered and "forged" not in rendered and "invented" not in rendered
    assert result["instruction_readme_paths_excluded"] == 2
    javascript = next(row for row in result["files"] if row["path"] == "web/cache.mjs")
    assert javascript["symbol_status"] == "unsupported-structural-language"
    assert result["semantic_coverage"] == result["test_coverage"] == "not-established"
    assert result["license_permission"] == "not-established" and not result["donor_executed"]


def test_index_classifies_root_level_test_files_without_coverage_claim(tmp_path: Path) -> None:
    expectations = {
        "test_cache.py": True,
        "cache_test.py": True,
        "spec_cache.py": True,
        "cache_spec.py": True,
        "tests/test_cache.py": True,
        "spec/cache.py": True,
        "src/contest.py": False,
        "src/testament.py": False,
        "src/test_cache.json": False,
    }
    repo = repository(
        tmp_path / "donor",
        {
            name: "{}\n" if name.endswith(".json") else "def example(): pass\n"
            for name in expectations
        },
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = source_index(
        repo, sha, need="test evidence navigation", terms=[], limit=24, symbol_files=0
    )
    flags = {row["path"]: row["test_path_heuristic"] for row in result["files"]}
    assert result["available"] and result["tree_complete"]
    assert flags == expectations
    assert result["test_coverage"] == "not-established"
    assert result["license_permission"] == "not-established"
    assert not result["donor_executed"]


def test_navigation_limits_retain_unreturned_and_uninspected_counts(tmp_path: Path) -> None:
    repo = repository(
        tmp_path / "donor",
        {f"src/module{i}.py": f"def function{i}():\n    return {i}\n" for i in range(4)},
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = source_index(repo, sha, need="source selection", terms=[], limit=2, symbol_files=1)
    assert len(result["files"]) == 2 and result["eligible_paths_not_returned"] == 2
    assert result["files"][1]["symbol_status"] == "symbol-file-budget-deferred"
    scoped = source_index(repo, sha, need="source selection", terms=[], prefix="src", limit=1)
    assert scoped["available"] and len(scoped["files"]) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": True},
        {"limit": 65},
        {"symbol_files": 17},
        {"terms": ["x" * 101]},
        {"prefix": "../elsewhere"},
    ],
)
def test_navigation_rejects_unbounded_or_unsafe_plans(
    tmp_path: Path, kwargs: dict[str, object]
) -> None:
    with pytest.raises(CrabError):
        source_index(tmp_path, "a" * 40, need="bounded", **{"terms": [], **kwargs})  # type: ignore[arg-type]


def test_navigation_never_promotes_missing_commit_to_complete(tmp_path: Path) -> None:
    repo = repository(tmp_path / "donor", {"app.py": "def run(): pass\n"})
    result = source_index(repo, "0" * 40, need="pinned source", terms=[])
    assert not result["available"] and not result["tree_complete"]
    assert result["tree_omissions"] and not result["files"]
    with pytest.raises(CrabError, match="immutable"):
        source_index(repo, "HEAD", need="pinned source", terms=[])


def test_public_navigation_command_uses_real_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repository(tmp_path / "donor", {"actual.py": "def entry(): return 1\n"})
    sha = git(repo, "rev-parse", "HEAD").strip()
    plan = tmp_path / "index.json"
    plan.write_text(json.dumps({"repo": str(repo), "sha": sha, "need": "entry point", "terms": []}))
    assert main(["discover", "index", "--input", str(plan)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["files"][0]["path"] == "actual.py"
    assert result["files"][0]["symbols"][0]["name"] == "entry"


def test_index_does_not_execute_source_or_parse_oversized_files(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    repo = repository(
        tmp_path / "donor",
        {
            "danger.py": f"from pathlib import Path\nPath({str(marker)!r}).write_text('BAD')\n"
            "def declaration_only(): return 1\n",
            "large.py": "#" + "x" * 65536 + "\ndef hidden(): pass\n",
        },
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = source_index(repo, sha, need="bounded navigation", terms=[])
    assert not marker.exists()
    large = next(row for row in result["files"] if row["path"] == "large.py")
    assert large["symbol_status"] == "unavailable-or-file-byte-limit" and not large["symbols"]


def test_index_never_follows_git_link_modes(tmp_path: Path) -> None:
    repo = repository(tmp_path / "donor", {"target.py": "def actual(): pass\n"})
    blob = git(repo, "rev-parse", "HEAD:target.py").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},alias.py")
    git(repo, "commit", "-m", "fixture: inert link object")
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = source_index(repo, sha, need="no link traversal", terms=[])
    assert all(row["path"] != "alias.py" for row in result["files"])
    assert any(
        row["reason"] == "non-regular-material-not-followed" for row in result["tree_omissions"]
    )


def test_directory_queue_omissions_cannot_be_promoted_to_complete() -> None:
    class Reader:
        def expired(self) -> bool:
            return False

        def tree_entries(self, sha: str, directory: str) -> dict[str, str]:
            return {f"dir{i:03}": "040000" for i in range(200)} if not directory else {}

    paths, omissions, available = _source_paths(Reader(), "a" * 40)  # type: ignore[arg-type]
    assert available and not paths
    assert any(row["reason"] == "directory-queue-limit" for row in omissions)


def test_expired_navigation_does_not_start_another_tree_read() -> None:
    class Reader:
        def expired(self) -> bool:
            return True

        def tree_entries(self, sha: str, directory: str) -> dict[str, str]:
            raise AssertionError("an expired source budget must not start another read")

    paths, omissions, available = _source_paths(Reader(), "a" * 40)  # type: ignore[arg-type]
    assert not available and not paths and omissions


def test_navigation_finds_late_scoped_methods_before_boilerplate(tmp_path: Path) -> None:
    source = "def unrelated(): pass\n" * 30
    source += "class Cache:\n    def validate(self):\n        return True\n"
    repo = repository(tmp_path / "donor", {"module.py": source})
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = source_index(repo, sha, need="cache validation", terms=["cache", "valid"])
    symbol = result["files"][0]["symbols"][0]
    assert symbol["qualified_name"] == "Cache.validate" and symbol["start"] == 32
    selected = syntax_query("\ufeff" + source, [{"kind": "function", "name": "Cache.validate"}])
    assert len(selected["matches"]) == 1 and selected["matches"][0]["start"] == 32


def test_navigation_rejects_tree_objects_and_noncanonical_hash_lengths(tmp_path: Path) -> None:
    repo = repository(tmp_path / "donor", {"app.py": "def run(): pass\n"})
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    result = source_index(repo, tree, need="commit pin", terms=[])
    assert not result["available"] and not result["files"]
    with pytest.raises(CrabError, match="immutable"):
        source_index(repo, "a" * 41, need="full hash", terms=[])


def test_packets_prioritize_requested_queries_and_preserve_deferred_matches(tmp_path: Path) -> None:
    source = "".join(f"def helper{i}(): return {i}\n" for i in range(12))
    source += "class Cache:\n    def validate(self):\n        return True\n"
    repo = repository(tmp_path / "donor", {"module.py": source})
    sha = git(repo, "rev-parse", "HEAD").strip()
    packet = source_packet(
        repo,
        sha,
        ["module.py"],
        [
            {"kind": "function", "name": "Cache.validate"},
            {"kind": "function"},
        ],
        need="cache validation",
        related=False,
    )
    row = packet["files"][0]
    assert row["ranges"][0]["qualified_name"] == "Cache.validate"
    assert row["ranges"][0]["query_index"] == 0
    assert len(row["ranges"]) == 8
    assert len(row["structural_result"]["matches"]) == 14
    assert row["duplicate_structural_ranges"] == 1
    assert row["structural_ranges_deferred"] == 5
    assert packet["semantic_coverage"] == "not-established" and not packet["reviewed"]


def test_packets_preserve_worker_limits_and_actual_byte_omissions(tmp_path: Path) -> None:
    source = "".join(f"def helper{i}():\n    return {('x' * 200)!r}\n" for i in range(101))
    repo = repository(tmp_path / "donor", {"module.py": source})
    sha = git(repo, "rev-parse", "HEAD").strip()
    packet = source_packet(
        repo,
        sha,
        ["module.py"],
        [{"kind": "function"}],
        need="bounded source",
        byte_budget=1024,
        related=False,
    )
    row = packet["files"][0]
    assert row["structural_result"]["status"] == "budget-deferred"
    assert row["structural_result"]["reason"] == "match-limit"
    assert len(row["structural_result"]["matches"]) == 100
    assert row["structural_ranges_deferred"] == 92
    assert len(row["ranges"]) < 8 and packet["bytes"] <= 1024
    assert any(item["reason"] == "whole-packet-byte-limit" for item in packet["omissions"])
