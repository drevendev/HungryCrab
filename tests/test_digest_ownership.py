"""A digest directory is only partly the crab's.

``--out`` can name a directory the caller already uses, or the repository itself. Rerun
cleanup, the aggregate budget and ``manifest.json`` may touch only files a registered miner
declares; everything else survives untouched and unlisted (#126).
"""

from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW
from helpers import copy_repo

from hungry_crab.budget import apply_markdown_policy, artifact_owner, is_digest_artifact
from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.tokens import estimate_tokens

# Files the crab did not write and must never touch. Two of them wear digest-like names.
NEIGHBOURS: dict[str, str] = {
    "notes.txt": "caller owned\n",
    "caller-owned.md": "# Not a digest page\n\n" + "prose " * 600 + "\n",
    "caller-owned.json": '{"owner": "caller"}\n',
    "history.notes.md": "# Looks like a page of history.md and is not one\n",
}


def _plant(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in NEIGHBOURS.items():
        (out_dir / name).write_text(text, encoding="utf-8", newline="\n")


def _assert_untouched(out_dir: Path) -> None:
    for name, text in NEIGHBOURS.items():
        assert (out_dir / name).read_text(encoding="utf-8") == text, name


def test_ownership_comes_from_the_registry_not_the_extension() -> None:
    assert artifact_owner("deps.json") == "deps"
    assert artifact_owner("history.md") == "history"
    assert artifact_owner("history.2.md") == "history"
    assert artifact_owner("tests.md") == "testing"
    assert artifact_owner("history.notes.md") is None
    assert artifact_owner("caller-owned.md") is None
    assert artifact_owner("menu.json") is None
    assert artifact_owner("manifest.json") is None
    assert is_digest_artifact("manifest.json")
    assert is_digest_artifact("ai.2.md")
    assert not is_digest_artifact("notes.txt")


def test_policy_reconciles_and_budgets_only_the_crabs_own_files(tmp_path: Path) -> None:
    out = tmp_path / "digest"
    out.mkdir()
    docs = "# Docs\n\n" + "x" * 100 + "\n"
    (out / "docs.md").write_text(docs, encoding="utf-8", newline="\n")
    (out / "deps.json").write_text("{}\n", encoding="utf-8", newline="\n")
    _plant(out)
    records = [{"name": "docs", "files": ["docs.md"], "page_priorities": {"docs.md": 5}}]

    result = apply_markdown_policy(
        records, out, total_budget=estimate_tokens(docs), policy="enforce"
    )

    assert not (out / "deps.json").exists(), "a stale artifact of a registered miner is removed"
    assert (out / "docs.md").is_file()
    assert result.before_tokens == estimate_tokens(docs)
    assert result.dropped_pages == []
    _assert_untouched(out)


def test_selective_rerun_removes_stale_artifacts_and_keeps_the_neighbours(
    npm_app: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    target = Target(path=npm_app)
    run_digest(target, DigestOptions(out=out_dir, now=FIXED_NOW))
    assert (out_dir / "deps.json").is_file()
    _plant(out_dir)

    result = run_digest(target, DigestOptions(out=out_dir, now=FIXED_NOW, miners=["license"]))

    assert not (out_dir / "deps.json").exists()
    _assert_untouched(out_dir)
    files = result.manifest["files"]
    assert {entry["name"] for entry in files}.isdisjoint(NEIGHBOURS)
    assert all(entry["miner"] for entry in files)
    listed_markdown = sum(e["tokens_est"] for e in files if e["kind"] == "markdown")
    assert result.manifest["markdown_tokens_before_policy_est"] == listed_markdown


def test_enforce_drops_pages_the_crab_wrote_and_nothing_else(npm_app: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    _plant(out_dir)

    result = run_digest(
        Target(path=npm_app),
        DigestOptions(out=out_dir, now=FIXED_NOW, total_budget=1, budget_policy="enforce"),
    )

    dropped = {page["name"] for page in result.manifest["dropped_pages"]}
    assert dropped
    assert dropped.isdisjoint(NEIGHBOURS)
    assert all(artifact_owner(name) for name in dropped)
    assert result.manifest["markdown_tokens_est"] <= 1
    assert {entry["name"] for entry in result.manifest["files"]}.isdisjoint(NEIGHBOURS)
    _assert_untouched(out_dir)


def test_out_dir_may_be_the_repository_itself(npm_app: Path, tmp_path: Path) -> None:
    repo = copy_repo(npm_app, tmp_path / "repo", with_git=True)
    before = {path.name for path in repo.iterdir() if path.is_file()}
    assert "package.json" in before

    run_digest(Target(path=repo), DigestOptions(out=repo, now=FIXED_NOW))

    after = {path.name for path in repo.iterdir() if path.is_file()}
    assert before <= after
    assert (repo / "package.json").read_text(encoding="utf-8") == (
        npm_app / "package.json"
    ).read_text(encoding="utf-8")
