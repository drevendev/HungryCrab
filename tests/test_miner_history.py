from __future__ import annotations

from collections import Counter

from helpers import read_json, read_md

from hungry_crab.digest import DigestResult
from hungry_crab.miners.history import bus_factor, is_conventional, is_fix, parse_log


def test_helpers() -> None:
    assert is_conventional("feat(store): add thing")
    assert is_conventional("fix: it")
    assert not is_conventional("Add thing")
    assert not is_conventional("wip: thing")
    assert is_fix("Fix crash on empty input")
    assert is_fix("Bugfix: handle unicode")
    assert not is_fix("feat: add fixtures for the fixer")  # 'fixtures' is not 'fix'
    assert bus_factor(Counter({"a": 6, "b": 5, "c": 2})) == 2
    assert bus_factor(Counter({"a": 10})) == 1
    assert bus_factor(Counter()) == 0


def test_parse_log_handles_bodies_and_binary_numstat() -> None:
    record = (
        "\x1eabc123\x1fAlice\x1fALICE@example.com\x1f2024-03-01T10:00:00+00:00"
        '\x1f2024-03-01T10:00:00+00:00\x1fparent1\x1fRevert "thing"\x1fThis reverts commit x.\x1f\n'
        "3\t1\tsrc/a.ts\n-\t-\timg/logo.png\n"
    )
    commits = parse_log(record)
    assert len(commits) == 1
    commit = commits[0]
    assert commit.email == "alice@example.com"
    assert commit.is_revert
    assert commit.files == [("src/a.ts", 3, 1), ("img/logo.png", None, None)]
    assert commit.lines == 4
    assert not commit.is_merge


def test_npm_history_metrics(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "history.json")
    assert data["available"] is True
    assert data["commits"] == 13
    assert data["merges"] == 0
    assert data["authors"] == 3
    assert data["bus_factor"] == 2
    assert data["first_commit"].startswith("2024-03-01")
    assert data["last_commit"].startswith("2025-04-10")
    assert data["commits_last_90d"] == 1
    assert data["conventional_commits_ratio"] == 0.85
    assert data["fix_ratio"] == 0.31
    assert data["pr_style_ratio"] == 0.23
    assert data["revert_count"] == 1
    assert data["reverts"][0]["subject"].startswith("Revert")
    hotspot = data["hotspots"][0]
    assert hotspot["path"] == "src/lib/store.ts"
    assert hotspot["commits"] == 6
    assert hotspot["fixes"] == 4
    assert hotspot["fix_ratio"] == 0.67
    assert data["fix_prone"][0]["path"] == "src/lib/store.ts"
    pairs = {(c["a"], c["b"]): c["count"] for c in data["coupling"]}
    assert pairs[("src/lib/store.test.ts", "src/lib/store.ts")] == 2
    top = data["top_authors"][0]
    assert top["name"] == "Alice Crab" and top["commits"] == 6
    assert "email" not in top


def test_npm_tags(npm_digest: DigestResult) -> None:
    tags = read_json(npm_digest, "history.json")["tags"]
    assert tags["count"] == 4
    assert tags["semver_count"] == 4
    assert tags["latest"] == "v1.0.1"
    assert tags["release_cadence_days"] == 122
    assert tags["releases_last_year"] == 2
    assert tags["annotated_ratio"] == 1.0
    text = read_md(npm_digest, "history.md")
    assert "## Hotspots" in text
    assert "src/lib/store.ts" in text
    assert "v1.0.1" in text


def test_python_and_dotnet_history(py_digest: DigestResult, dotnet_digest: DigestResult) -> None:
    py = read_json(py_digest, "history.json")
    assert py["commits"] == 10
    assert py["conventional_commits_ratio"] == 0.6
    assert py["fix_ratio"] == 0.2
    assert py["tags"]["latest"] == "v0.2.0"
    assert py["commits_last_90d"] == 1
    dotnet = read_json(dotnet_digest, "history.json")
    assert dotnet["commits"] == 8
    assert dotnet["conventional_commits_ratio"] == 0.0
    assert dotnet["revert_count"] == 1
    assert dotnet["tags"]["latest"] == "1.1.0"
    assert dotnet["tags"]["semver_count"] == 2
    assert dotnet["commits_last_90d"] == 0
