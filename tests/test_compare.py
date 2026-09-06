from __future__ import annotations

import json
from pathlib import Path

from conftest import FIXED_NOW
from helpers import read_json

from hungry_crab.cache import Target
from hungry_crab.compare import (
    CompareOptions,
    apply_hunger,
    compare_digests,
    load_menu,
    menu_candidates,
    run_compare,
    write_compare,
)
from hungry_crab.compare.candidates import Side
from hungry_crab.digest import DigestOptions, DigestResult
from hungry_crab.nutrients import Candidate, Evidence
from hungry_crab.tokens import estimate_tokens


def _ids(candidates: list[Candidate]) -> set[str]:
    return {c.id for c in candidates}


def test_side_loads_a_digest(npm_digest: DigestResult, npm_app: Path) -> None:
    side = Side.load(npm_digest.out_dir, root=npm_app)
    assert side.label == "npm-app"
    assert side.spdx == "MIT"
    assert side.ecosystems == {"npm"}
    assert side.trait("has_dependabot") is True
    assert side.find_files(".github/workflows/*") == [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
    ]
    assert side.blob_url("README.md") is None  # local prey has no URL


def test_python_maw_eats_npm_prey(
    npm_digest: DigestResult, py_digest: DigestResult, npm_app: Path, pyproject_cli: Path
) -> None:
    result = compare_digests(
        npm_digest.out_dir,
        py_digest.out_dir,
        prey_root=npm_app,
        maw_root=pyproject_cli,
        options=CompareOptions(now=FIXED_NOW),
    )
    ids = _ids(result.candidates)
    expected = {
        "crab:ci:ci.cache",
        "crab:ci:ci.concurrency",
        "crab:ci:ci.timeouts",
        "crab:tooling:tooling.dependabot",
        "crab:tooling:tooling.editorconfig",
        "crab:ai-config:ai-config.claude-md",
        "crab:ai-config:ai-config.skills",
        "crab:ai-config:ai-config.claude-settings",
        "crab:hygiene:hygiene.contributing",
        "crab:tests:tests.e2e",
    }
    assert expected <= ids
    # different ecosystems: no tool or dependency candidates, e2e is only transferable
    assert not any(i.startswith("crab:deps:") for i in ids)
    assert not any(i.startswith("crab:tooling:tooling.linter") for i in ids)
    e2e = next(c for c in result.candidates if c.id == "crab:tests:tests.e2e")
    assert e2e.applicability == 0.6
    assert "Playwright" in e2e.what
    architecture = next(c for c in result.candidates if c.category == "architecture")
    assert architecture.id == "crab:architecture:architecture.npm-app.raw"
    assert "src/lib/store.ts" in architecture.what and architecture.artifact == "idea"
    # both sides are permissive: COPY, and scores are ranked
    assert result.verdict["mode"] == "COPY"
    assert all(c.license_mode == "COPY" for c in result.candidates)
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
    assert result.candidates[0].score >= 0.7
    cache = next(c for c in result.candidates if c.id == "crab:ci:ci.cache")
    assert [e.path for e in cache.evidence] == [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
    ]
    dependabot = next(c for c in result.candidates if c.id == "crab:tooling:tooling.dependabot")
    assert dependabot.evidence[0].path == ".github/dependabot.yml"
    assert "github-actions, npm" in dependabot.what
    assert dependabot.provenance["prey"] == "npm-app"
    assert dependabot.provenance["maw"] == "pyproject-cli"


def test_npm_maw_eats_python_prey(
    npm_digest: DigestResult, py_digest: DigestResult, npm_app: Path, pyproject_cli: Path
) -> None:
    result = compare_digests(
        py_digest.out_dir, npm_digest.out_dir, prey_root=pyproject_cli, maw_root=npm_app
    )
    ids = _ids(result.candidates)
    expected = {
        "crab:tooling:tooling.pre-commit",
        "crab:hygiene:hygiene.security-md",
        "crab:hygiene:hygiene.codeowners",
        "crab:hygiene:hygiene.issue-templates",
        "crab:hygiene:hygiene.pr-template",
        "crab:docs:docs.site",
        "crab:docs:docs.adr",
        "crab:docs:docs.directory",
        "crab:ai-config:ai-config.agents-md",
        "crab:ai-config:ai-config.cursor-rules",
        "crab:tests:tests.coverage-threshold",
        "crab:ci:ci.macos-runner",
    }
    assert expected <= ids
    assert "crab:tooling:tooling.python-version-file" not in ids, "npm maw cannot use it"
    assert "crab:hygiene:hygiene.contributing" not in ids, "the maw already has one"
    threshold = next(c for c in result.candidates if c.id == "crab:tests:tests.coverage-threshold")
    assert "80%" in threshold.title
    assert result.verdict["mode"] == "COPY"
    assert result.verdict["notice_required"] is True


def test_gpl_prey_lowers_every_score(
    npm_digest: DigestResult, dotnet_digest: DigestResult, npm_app: Path, dotnet_lib: Path
) -> None:
    result = compare_digests(
        dotnet_digest.out_dir, npm_digest.out_dir, prey_root=dotnet_lib, maw_root=npm_app
    )
    assert result.verdict["mode"] == "REIMPLEMENT"
    ids = _ids(result.candidates)
    assert {"crab:hygiene:hygiene.code-of-conduct", "crab:tests:tests.bench"} <= ids
    bench = next(c for c in result.candidates if c.id == "crab:tests:tests.bench")
    assert bench.applicability == 0.6 and bench.license_mode == "REIMPLEMENT"
    assert max(c.score for c in result.candidates) < 0.5


def test_hunger_hides_and_downgrades() -> None:
    cards = [
        Candidate("ci", "ci.cache", "Cache", "caches", artifact="pr"),
        Candidate("deps", "deps.npm.zod", "zod", "uses zod", artifact="issue"),
        Candidate("docs", "docs.site", "Docs", "docs", artifact="issue"),
    ]
    kept, hidden = apply_hunger(cards, {"deps": False, "ci": "issues-only", "docs": "ideas-only"})
    assert [c.id for c in kept] == ["crab:ci:ci.cache", "crab:docs:docs.site"]
    assert kept[0].artifact == "issue" and kept[1].artifact == "idea"
    assert hidden == [{"id": "crab:deps:deps.npm.zod", "reason": "hunger: deps is off"}]


def test_hidden_ids_and_scoring_overrides(
    npm_digest: DigestResult, py_digest: DigestResult
) -> None:
    options = CompareOptions(
        hidden_ids={"crab:ci:ci.cache": "ledger: rejected"},
        scoring={"categories": {"ci": 0.1}},
        top=5,
        now=FIXED_NOW,
    )
    result = compare_digests(npm_digest.out_dir, py_digest.out_dir, options=options)
    assert "crab:ci:ci.cache" not in _ids(result.candidates)
    assert {"id": "crab:ci:ci.cache", "reason": "ledger: rejected"} in result.hidden
    assert result.menu["scoring"]["categories"]["ci"] == 0.1
    assert not any(c.category == "ci" for c in result.candidates[:3])
    assert result.menu["counts"]["top"] == 5
    assert len(result.shown) == 5
    assert result.menu["generated_at"].startswith("2025-06-01")


def test_markdown_outputs_and_manifest_refresh(
    npm_digest: DigestResult, py_digest: DigestResult, npm_app: Path, tmp_path: Path
) -> None:
    result = compare_digests(npm_digest.out_dir, py_digest.out_dir, prey_root=npm_app)
    assert result.gap_md.startswith("# Gap: pyproject-cli vs npm-app@")
    assert "## Prey has, maw lacks" in result.gap_md
    assert "| has_dependabot | no | yes |" in result.gap_md
    assert result.menu_md.startswith("# Menu: npm-app@")
    assert "| 1 | " in result.menu_md
    assert "crab:ci:ci.cache" in result.menu_md
    assert estimate_tokens(result.menu_md) <= 3500
    out = tmp_path / "digest"
    out.mkdir()
    (out / "manifest.json").write_text(
        json.dumps(
            {"schema": "hungry-crab.digest/1", "files": [], "budget": {"markdown_total": 30000}}
        ),
        encoding="utf-8",
    )
    names = write_compare(result, out)
    assert names == ["gap.md", "menu.md", "menu.json", "compare.json"]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [f["name"] for f in manifest["files"]] == [
        "compare.json",
        "gap.md",
        "menu.json",
        "menu.md",
    ]
    assert all(f["miner"] == "compare" for f in manifest["files"])
    assert manifest["reading_order"][:2] == ["menu.md", "gap.md"]
    # a digest taken without a maw has no verdict; the comparison resolves it
    assert manifest["summary"]["license"]["verdict"]["mode"] == "COPY"
    assert manifest["summary"]["license"]["maw_license"] == "Apache-2.0"
    menu = load_menu(out)
    assert menu is not None and menu["schema"] == "hungry-crab.menu/1"
    cards = menu_candidates(menu)
    assert cards[0].id == result.candidates[0].id
    assert cards[0].evidence == result.candidates[0].evidence


def test_run_compare_end_to_end(npm_app: Path, pyproject_cli: Path, tmp_path: Path) -> None:
    result, prey_digest, maw_digest = run_compare(
        Target(path=npm_app),
        pyproject_cli,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache"),
        options=CompareOptions(now=FIXED_NOW),
    )
    assert (prey_digest.out_dir / "menu.json").is_file()
    assert (prey_digest.out_dir / "gap.md").is_file()
    manifest = read_json(prey_digest, "manifest.json")
    assert "menu.md" in [f["name"] for f in manifest["files"]]
    assert manifest["summary"]["license"]["verdict"]["mode"] == "COPY"
    assert manifest["summary"]["license"]["spdx"] == "MIT", "the merge keeps what the miner found"
    assert manifest["summary"]["primary_language"]
    assert maw_digest.out_dir.is_relative_to(tmp_path / "cache" / "maws")
    assert result.menu["maw"]["license"] == "Apache-2.0"
    compare_info = read_json(prey_digest, "compare.json")
    assert compare_info["maw"]["label"] == "pyproject-cli"
    # cached digests on the second run, fresh comparison
    again, _, _ = run_compare(
        Target(path=npm_app),
        pyproject_cli,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache"),
        options=CompareOptions(now=FIXED_NOW),
    )
    assert _ids(again.candidates) == _ids(result.candidates)


def test_candidate_roundtrip() -> None:
    card = Candidate(
        "ci", "ci.cache", "Cache", "caches", evidence=[Evidence("a.yml", "https://x/a.yml")]
    )
    card.score = 0.5
    data = card.to_dict()
    assert data["id"] == "crab:ci:ci.cache"
    restored = Candidate.from_dict({**data, "unknown_field": 1})
    assert restored == card
