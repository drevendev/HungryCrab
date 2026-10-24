"""Exercise fetched ownership evidence through sniff, compare and COPY preparation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import FIXED_NOW
from fixture_builder import git
from helpers import copy_repo, write_tree

import hungry_crab.digest as digest_module
from hungry_crab.attribution import GitSourceReader, prepare_copy_pull_request
from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.compare import compare_for_maw
from hungry_crab.digest import DigestOptions
from hungry_crab.fetch.github import GitHubClient
from hungry_crab.fetch.providers import RepositoryClient
from hungry_crab.licensing import Relationship
from hungry_crab.maw import MawConfig, TrustSettings
from hungry_crab.miners import MineContext
from hungry_crab.sniff import sniff


class MetadataClient:
    transport = "synthetic-api"

    def __init__(self, repository: dict[str, Any]) -> None:
        self.repository = repository

    def repo(self, slug: Slug) -> dict[str, Any]:
        return self.repository

    def languages(self, slug: Slug) -> dict[str, int]:
        return {"Python": 100}


def test_sniff_rechecks_owner_after_fetching_fork_metadata(tmp_path: Path) -> None:
    config = MawConfig(root=tmp_path, trust=TrustSettings(same_owner=False, owners=["owner"]))
    metadata = {"fork": True, "parent": {"full_name": "foreign/upstream"}, "license": None}
    report = sniff(
        Slug("owner", "fork"),
        client=cast(RepositoryClient, MetadataClient(metadata)),
        cache_root=tmp_path / "cache",
        maw_license="MIT",
        relationship=Relationship.OWN,
        maw_config=config,
        now=FIXED_NOW,
    )
    assert report.relationship == "foreign"
    assert report.mode == "IDEAS_ONLY"
    stored = json.loads(
        (prey_paths(Slug("owner", "fork"), tmp_path / "cache").api / "sniff.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["relationship"] == "foreign"


@pytest.mark.parametrize("fork", [False, True])
def test_fetched_fork_reaches_compare_and_copy_notice(
    tmp_path: Path,
    pyproject_cli: Path,
    npm_app: Path,
    monkeypatch: pytest.MonkeyPatch,
    fork: bool,
) -> None:
    maw = copy_repo(npm_app, tmp_path / "maw", with_git=True)
    git(maw, "remote", "add", "origin", "https://github.com/owner/maw")
    slug = Slug("owner", "prey")
    metadata: dict[str, Any] = {
        "full_name": "owner/prey",
        "fork": fork,
        "license": {"spdx_id": "Apache-2.0"},
    }
    if fork:
        metadata["parent"] = {"full_name": "foreign/upstream"}
        metadata["source"] = {"full_name": "foreign/original"}
    original = digest_module.prepare_context

    def local_api(
        target: Target,
        options: DigestOptions,
        *,
        log: Callable[[str], None],
    ) -> tuple[MineContext, Path]:
        # Replace only acquisition; the real miners, manifest and compare remain in the path.
        if target.slug == slug:
            ctx, out = original(Target(path=pyproject_cli), options, log=log)
            ctx.label, ctx.url = str(slug), slug.url
            ctx.api["repo"] = metadata
            return ctx, out
        return original(target, options, log=log)

    monkeypatch.setattr(digest_module, "prepare_context", local_api)
    monkeypatch.setattr(GitHubClient, "repo", lambda *_: metadata)
    result, prey_digest, _, _ = compare_for_maw(
        Target(slug=slug),
        maw,
        digest_options=DigestOptions(cache_root=tmp_path / "cache", now=FIXED_NOW),
        record_ledger=False,
    )
    expected = "foreign" if fork else "own"
    assert result.verdict["relationship"] == expected
    assert result.verdict["notice_required"] is fork
    assert prey_digest.manifest["prey"]["repository"] == {
        key: value for key, value in metadata.items() if key in {"fork", "parent", "source"}
    }
    assert all(card.trace["license_relationship"] == expected for card in result.candidates)
    card = next(c for c in result.candidates if c.license_mode == "COPY" and c.serve_as == "pr")
    text = (pyproject_cli / "README.md").read_text(encoding="utf-8")
    write_tree(maw, {"adopted/README.md": text})
    source = result.menu["prey"]
    receipt = json.dumps(
        {
            "version": 1,
            "kind": "materialization",
            "nutrient_id": card.id,
            "source": {key: source[key] for key in ("label", "url", "sha", "license")},
            "taken": [
                {"prey_path": "README.md", "maw_path": "adopted/README.md", "verbatim": True}
            ],
            "summary": "Carry the documented example.",
            "checks": ["review the rendered document"],
        }
    )
    prepared = prepare_copy_pull_request(
        card,
        result.menu,
        maw_root=maw,
        receipt_payload=receipt,
        title=card.title,
        body=f"<!-- {card.id} -->\nCarry the example.\n",
        attribution_file="THIRD_PARTY_NOTICES.md",
        source_reader=GitSourceReader(pyproject_cli),
        now=FIXED_NOW,
    )
    records = json.loads(
        next(f.content for f in prepared.files if f.path == ".crab/attributions.json")
    )
    assert records["receipts"][0]["obligation"]["kind"] == ("notice-file" if fork else "none")
