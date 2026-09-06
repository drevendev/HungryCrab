from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXED_NOW
from helpers import write_tree

from hungry_crab.cache import Slug, Target
from hungry_crab.compare import CompareOptions, compare_for_maw, run_compare
from hungry_crab.digest import DigestOptions
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.ledger import Ledger
from hungry_crab.maw import CONFIG_FILE, MawConfig
from hungry_crab.nutrients import Candidate, Evidence
from hungry_crab.serve import ServeOptions, parse_markers, render_issue, serve

NOW = datetime(2025, 6, 3, tzinfo=UTC)
MAW_SLUG = Slug("example", "maw")


class FakeIssues:
    def __init__(self, existing: dict[str, dict[str, Any]] | None = None) -> None:
        self.existing = existing or {}
        self.created: list[dict[str, Any]] = []
        self.labels: list[str] = []
        self.fail_list = False
        self.can_label = True
        self.who = "crab-bot[bot]"

    def list_marked(self, slug: Slug, label: str) -> dict[str, dict[str, Any]]:
        if self.fail_list:
            raise CrabError("boom")
        return self.existing

    def ensure_label(self, slug: Slug, label: str) -> bool:
        self.labels.append(label)
        return self.can_label

    def identity(self) -> str:
        return self.who

    def create(
        self, slug: Slug, title: str, body: str, labels: list[str], assignees: list[str]
    ) -> str:
        number = len(self.created) + 10
        self.created.append(
            {"title": title, "body": body, "labels": labels, "assignees": assignees}
        )
        return f"https://github.com/{slug}/issues/{number}"


def _menu_dir(npm_app: Path, pyproject_cli: Path, cache: Path) -> Path:
    result, _, _ = run_compare(
        Target(path=npm_app),
        pyproject_cli,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=cache),
        options=CompareOptions(now=FIXED_NOW),
    )
    assert result.meal_dir is not None
    return result.meal_dir


def test_parse_markers_and_render_issue() -> None:
    issues = [
        {
            "number": 1,
            "url": "u1",
            "state": "OPEN",
            "title": "t",
            "body": "text <!-- crab:ci:ci.cache --> more",
        },
        {
            "number": 2,
            "url": "u2",
            "state": "CLOSED",
            "title": "t2",
            "body": "<!--crab:docs:docs.site-->",
        },
        {"number": 3, "url": "u3", "state": "OPEN", "title": "t3", "body": None},
    ]
    found = parse_markers(issues)
    assert found == {
        "crab:ci:ci.cache": {"number": 1, "url": "u1", "state": "open", "title": "t"},
        "crab:docs:docs.site": {"number": 2, "url": "u2", "state": "closed", "title": "t2"},
    }
    card = Candidate(
        "ci", "ci.cache", "Cache dependencies in CI", "npm-app caches dependencies",
        maw_state="no", serve_as="pr", effort="S", risk="low",
        evidence=[Evidence(".github/workflows/ci.yml", "https://x/ci.yml")],
        license_mode="COPY", score=0.81,
    )  # fmt: skip
    title, body = render_issue(
        card,
        {
            "prey": {
                "label": "example/prey",
                "sha": "a" * 40,
                "url": "https://github.com/example/prey",
                "license": "MIT",
            }
        },
    )
    assert title == "Cache dependencies in CI"
    assert body.startswith("<!-- crab:ci:ci.cache -->\n")
    assert "[.github/workflows/ci.yml](https://x/ci.yml)" in body
    assert "`example/prey@aaaaaaa`" in body
    assert "mode COPY" in body and "not legal advice" in body
    assert "Not judged yet" in body
    # maw_state is a rendered trait value, and a bare "no" reads as an unfinished sentence
    assert "## What this repository has\n\nnothing comparable\n" in body
    card.maw_state = "ruff 0.4, no cache"
    assert "## What this repository has\n\nruff 0.4, no cache\n" in render_issue(card, {})[1]
    card.why = "Our CI is slow."
    card.how = "Use setup-uv cache."
    _, body = render_issue(card, {"prey": {"label": "p"}})
    assert "Our CI is slow." in body and "Use setup-uv cache." in body


def test_dry_run_previews_and_skips(npm_app: Path, pyproject_cli: Path, tmp_path: Path) -> None:
    prey_dir = _menu_dir(npm_app, pyproject_cli, tmp_path / "cache")
    config = MawConfig.load(pyproject_cli)
    ledger = Ledger(tmp_path / "ledger.json", maw="pyproject-cli")
    client = FakeIssues(
        {"crab:tooling:tooling.dependabot": {"number": 7, "url": "u", "state": "open"}}
    )
    options = ServeOptions(
        ids=["crab:ci:ci.cache", "crab:tooling:tooling.dependabot", "crab:nope:x"]
    )
    report = serve(
        prey_dir, pyproject_cli, options, config=config, ledger=ledger, client=client, now=NOW,
        slug_lookup=lambda _: MAW_SLUG,
    )  # fmt: skip
    assert report.mode == "dry-run"
    assert [p["id"] for p in report.previews] == ["crab:ci:ci.cache"]
    assert report.previews[0]["body"].startswith("<!-- crab:ci:ci.cache -->")
    reasons = {s["id"]: s["reason"] for s in report.skipped}
    assert reasons["crab:nope:x"] == "not in the menu"
    assert reasons["crab:tooling:tooling.dependabot"] == "issue #7 exists (open)"
    assert client.created == []
    assert ledger.entries["crab:tooling:tooling.dependabot"].status == "served"
    assert (tmp_path / "ledger.json").is_file(), "learning about an existing issue is persisted"


def test_issue_mode_creates_issues_and_updates_the_ledger(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    prey_dir = _menu_dir(npm_app, pyproject_cli, tmp_path / "cache")
    notes = tmp_path / "notes.json"
    notes.write_text(
        json.dumps([{"id": "crab:ci:ci.cache", "why": "CI takes 9 minutes.", "how": "Cache uv."}]),
        encoding="utf-8",
    )
    config = MawConfig.load(pyproject_cli)
    ledger = Ledger(tmp_path / "ledger.json", maw="pyproject-cli")
    ledger.record_meal(
        {"prey": {"label": "npm-app"}, "verdict": {"mode": "COPY"}},
        [
            Candidate("ci", "ci.concurrency", "Concurrency", "x", trace={"prey": "npm-app"}),
        ],
        now=NOW,
    )
    ledger.mark("crab:ci:ci.concurrency", "rejected", reason="no", now=NOW)
    client = FakeIssues()
    options = ServeOptions(
        ids=["crab:ci:ci.cache", "crab:ci:ci.concurrency"], mode="issue", notes=notes
    )
    report = serve(
        prey_dir, pyproject_cli, options, config=config, ledger=ledger, client=client, now=NOW,
        slug_lookup=lambda _: MAW_SLUG,
    )  # fmt: skip
    assert [s["id"] for s in report.served] == ["crab:ci:ci.cache"]
    assert report.served[0]["url"] == "https://github.com/example/maw/issues/10"
    assert report.skipped == [{"id": "crab:ci:ci.concurrency", "reason": "ledger: rejected"}]
    assert client.labels == ["hungry-crab"]
    created = client.created[0]
    assert created["labels"] == ["hungry-crab"]
    assert "CI takes 9 minutes." in created["body"] and "Cache uv." in created["body"]
    entry = ledger.entries["crab:ci:ci.cache"]
    assert entry.status == "served" and entry.url == report.served[0]["url"]
    saved = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert {e["id"]: e["status"] for e in saved["entries"]}["crab:ci:ci.cache"] == "served"
    # second serve of the same id is a no-op
    again = serve(
        prey_dir, pyproject_cli, ServeOptions(ids=["crab:ci:ci.cache"], mode="issue"),
        config=config, ledger=ledger, client=client, now=NOW, slug_lookup=lambda _: MAW_SLUG,
    )  # fmt: skip
    assert again.served == [] and len(client.created) == 1
    assert again.skipped[0]["reason"].startswith("ledger: served")


def test_serve_guards(npm_app: Path, pyproject_cli: Path, tmp_path: Path) -> None:
    prey_dir = _menu_dir(npm_app, pyproject_cli, tmp_path / "cache")
    config = MawConfig.load(pyproject_cli)
    ledger = Ledger(None)
    with pytest.raises(CrabError, match=r"0\.3"):
        serve(
            prey_dir,
            pyproject_cli,
            ServeOptions(ids=["x"], mode="pr-branch"),
            config=config,
            ledger=ledger,
        )
    with pytest.raises(UsageError, match="nothing selected"):
        serve(prey_dir, pyproject_cli, ServeOptions(), config=config, ledger=ledger)
    with pytest.raises(CrabError, match="no GitHub origin"):
        serve(
            prey_dir,
            pyproject_cli,
            ServeOptions(top=1, mode="issue"),
            config=config,
            ledger=ledger,
            client=FakeIssues(),
        )
    off = tmp_path / "off"
    write_tree(off, {CONFIG_FILE: "serve:\n  issues: off\n"})
    with pytest.raises(CrabError, match=r"serve\.issues is off"):
        serve(
            prey_dir,
            off,
            ServeOptions(top=1, mode="issue"),
            config=MawConfig.load(off),
            ledger=ledger,
            client=FakeIssues(),
            slug_lookup=lambda _: MAW_SLUG,
        )
    with pytest.raises(CrabError, match="no menu"):
        serve(tmp_path / "empty", pyproject_cli, ServeOptions(top=1), config=config, ledger=ledger)
    broken = FakeIssues()
    broken.fail_list = True
    logged: list[str] = []
    report = serve(
        prey_dir, pyproject_cli, ServeOptions(top=2), config=config, ledger=ledger, client=broken,
        slug_lookup=lambda _: MAW_SLUG, log=logged.append,
    )  # fmt: skip
    assert len(report.previews) == 2
    assert any("could not list existing issues" in line for line in logged)


def test_compare_for_maw_uses_config_ledger_and_issues(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    maw = tmp_path / "maw"
    maw.mkdir()
    # a copy of the fixture tree is enough: compare reads the digest, not git history
    for path in pyproject_cli.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        target = maw / path.relative_to(pyproject_cli)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    write_tree(
        maw,
        {
            CONFIG_FILE: (
                "hunger:\n  tests: false\nledger: repo\nscoring:\n  categories:\n    tooling: 1.0\n"
            )
        },
    )
    cache = tmp_path / "cache"
    lookups: list[tuple[str, str]] = []

    def lookup(slug: Slug, label: str) -> dict[str, dict[str, Any]]:
        lookups.append((str(slug), label))
        return {"crab:ci:ci.cache": {"number": 3, "state": "open", "url": "u"}}

    result, _, ledger, config = compare_for_maw(
        Target(path=npm_app), maw,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=cache),
        issue_lookup=lookup, now=NOW,
    )  # fmt: skip
    assert lookups == [], "no origin remote, so no issue lookup"
    ids = {c.id for c in result.candidates}
    assert not any(i.startswith("crab:tests:") for i in ids)
    assert result.candidates[0].category == "tooling", "tooling weight raised to 1.0"
    assert config.exists and ledger.path == maw / ".crab" / "ledger.json"
    assert ledger.path is not None and ledger.path.is_file()
    assert all(e.status == "proposed" for e in ledger.entries.values())
    assert ledger.meals[0].prey == "npm-app"

    ledger.mark("crab:ci:ci.cache", "rejected", reason="no", now=NOW)
    ledger.save()
    second, _, ledger2, _ = compare_for_maw(
        Target(path=npm_app), maw,
        digest_options=DigestOptions(now=FIXED_NOW, cache_root=cache),
        now=NOW,
    )  # fmt: skip
    assert "crab:ci:ci.cache" not in {c.id for c in second.candidates}
    assert {"id": "crab:ci:ci.cache", "reason": "ledger: rejected (no)"} in second.hidden
    assert len(ledger2.meals) == 2 and ledger2.meals[1].new == 0
