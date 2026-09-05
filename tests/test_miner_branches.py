from __future__ import annotations

from helpers import read_json, read_md

from hungry_crab.digest import DigestResult


def test_npm_branches(npm_digest: DigestResult) -> None:
    data = read_json(npm_digest, "branches.json")
    assert data["available"] is True
    assert data["default_branch"] == "main"
    assert data["default_last_commit"] == "2025-04-10"
    assert data["total"] == 2 and data["analyzed"] == 2
    assert data["merged"] == 0
    assert data["stale"] == 1
    assert data["active_unmerged"] == 1
    by_name = {b["name"]: b for b in data["branches"]}
    dark = by_name["feature/dark-mode"]
    assert (dark["ahead"], dark["behind"]) == (2, 4)
    assert dark["stale"] is True and dark["merged"] is False
    assert dark["subjects"] == ["wip dark mode styles", "feat(theme): add dark mode toggle"]
    deps = by_name["chore/deps"]
    assert (deps["ahead"], deps["behind"]) == (1, 1)
    assert deps["stale"] is False
    assert data["branches"][0]["name"] == "chore/deps", "freshest branch first"
    text = read_md(npm_digest, "branches.md")
    assert "feature/dark-mode" in text
    assert "## What unmerged branches are about" in text


def test_other_fixtures_have_one_stale_branch_each(
    py_digest: DigestResult, dotnet_digest: DigestResult
) -> None:
    py = read_json(py_digest, "branches.json")
    assert [b["name"] for b in py["branches"]] == ["wip/plugins"]
    assert py["branches"][0]["stale"] is True
    dotnet = read_json(dotnet_digest, "branches.json")
    assert [b["name"] for b in dotnet["branches"]] == ["experiment/span-api"]
    assert dotnet["branches"][0]["ahead"] == 1
