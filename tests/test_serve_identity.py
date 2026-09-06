"""Filing issues into a repository the crab is only a visitor to, under its own name.

Opening an issue on a public repository needs nothing but an account. Creating a label needs
write access. The crab asked for a label before every meal, so the first thing that failed on
somebody else's repository was the one thing it did not need.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from helpers import write_tree
from test_serve import MAW_SLUG, FakeIssues, _menu_dir

from hungry_crab.errors import CrabError
from hungry_crab.ledger import Ledger
from hungry_crab.maw import CONFIG_FILE, MawConfig
from hungry_crab.serve import GhIssueClient, ServeOptions, serve

NOW = datetime(2025, 6, 3, tzinfo=UTC)


def _serve(
    npm_app: Path, maw: Path, tmp_path: Path, client: FakeIssues, **kwargs: Any
) -> tuple[Any, list[str]]:
    prey_dir = _menu_dir(npm_app, maw, tmp_path / "cache")
    log: list[str] = []
    report = serve(
        prey_dir,
        maw,
        ServeOptions(ids=["crab:ci:ci.cache"], mode="issue"),
        config=MawConfig.load(maw),
        ledger=Ledger(tmp_path / "ledger.json", maw="h"),
        client=client,
        now=NOW,
        slug_lookup=lambda _: MAW_SLUG,
        log=log.append,
        **kwargs,
    )
    return report, log


def test_a_repository_we_cannot_label_still_gets_the_issue(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    client = FakeIssues()
    client.can_label = False
    report, log = _serve(npm_app, pyproject_cli, tmp_path, client)
    assert len(report.served) == 1, "the label is not what the issue is for"
    assert client.created[0]["labels"] == [], "gh rejects --label for a label that is not there"
    assert any("cannot create the 'hungry-crab' label" in line for line in log)
    assert any("crab:<id> marker" in line for line in log), "say why dedup still works"


def test_the_log_says_who_the_issues_are_filed_as(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path
) -> None:
    client = FakeIssues()
    client.who = "crab-bot[bot]"
    _, log = _serve(npm_app, pyproject_cli, tmp_path, client)
    assert any("as crab-bot[bot]" in line for line in log)


def test_token_env_names_the_identity(tmp_path: Path) -> None:
    maw = tmp_path / "maw"
    write_tree(maw, {CONFIG_FILE: "serve:\n  token_env: CRAB_BOT_TOKEN\n"})
    assert MawConfig.load(maw).serve.token_env == "CRAB_BOT_TOKEN"
    write_tree(maw, {CONFIG_FILE: "serve: {}\n"})
    assert MawConfig.load(maw).serve.token_env == "", "gh's own login by default"


class _Recorder(GhIssueClient):
    """A gh client that records the environment instead of running anything."""

    def __init__(self, token_env: str) -> None:
        self.gh = "gh"
        self.timeout = 1.0
        self.token_env = token_env
        self.env: dict[str, str] = {}
        self.fail = False

    def _run(self, *args: str) -> str:
        import os

        env = dict(os.environ)
        token = env.get(self.token_env, "").strip() if self.token_env else ""
        if token:
            env["GH_TOKEN"] = token
            env.pop("GITHUB_TOKEN", None)
        self.env = env
        if self.fail:
            raise CrabError("gh label create failed: HTTP 403")
        return json.dumps({"login": "crab-bot[bot]"}) if args[:2] == ("api", "user") else ""


def test_the_token_from_the_named_variable_reaches_gh(monkeypatch: Any) -> None:
    monkeypatch.setenv("CRAB_BOT_TOKEN", "ghs_installationtoken")
    monkeypatch.setenv("GITHUB_TOKEN", "the-wrong-one")
    client = _Recorder("CRAB_BOT_TOKEN")
    client.ensure_label(MAW_SLUG, "hungry-crab")
    assert client.env["GH_TOKEN"] == "ghs_installationtoken"
    assert "GITHUB_TOKEN" not in client.env, "two tokens is one too many"


def test_no_token_leaves_gh_to_its_own_login(monkeypatch: Any) -> None:
    monkeypatch.delenv("CRAB_BOT_TOKEN", raising=False)
    client = _Recorder("CRAB_BOT_TOKEN")
    client.ensure_label(MAW_SLUG, "hungry-crab")
    assert "GH_TOKEN" not in client.env


def test_a_forbidden_label_is_reported_not_raised() -> None:
    client = _Recorder("")
    client.fail = True
    assert client.ensure_label(MAW_SLUG, "hungry-crab") is False
