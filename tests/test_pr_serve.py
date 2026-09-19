from __future__ import annotations

import json
from pathlib import Path

import pytest

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.pr_publication import GeneratedFile
from hungry_crab.pr_serve import prepare_cleanroom_pull_request, publish_cleanroom_git_pull_request


TRACE = "implemented from a specification, without access to the prey source"


def _receipt(*, nutrient_id: str = "crab:ci:cache", paths: list[str] | None = None) -> str:
    return json.dumps(
        {
            "version": 1,
            "nutrient_id": nutrient_id,
            "changed_paths": paths or ["generated/cache.yml"],
            "summary": TRACE,
            "checks": ["pytest -q"],
        }
    )


def _body(nutrient_id: str = "crab:ci:cache") -> str:
    return f"<!-- {nutrient_id} -->\n\nCarries one clean-room nutrient.\n"


def test_prepare_cleanroom_pull_request_freezes_only_receipt_files(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "cache.yml").write_text("cache: true\n", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("dirty maintainer edit\n", encoding="utf-8")

    prepared = prepare_cleanroom_pull_request(
        "crab:ci:cache",
        "feat: carry cache setup",
        _body(),
        _receipt(),
        tmp_path,
    )

    assert prepared.files == (GeneratedFile("generated/cache.yml", "cache: true\n"),)
    assert "unrelated.txt" not in {generated.path for generated in prepared.files}
    assert prepared.body.startswith("<!-- crab:ci:cache -->")
    assert TRACE in prepared.body


def test_prepare_cleanroom_pull_request_rejects_nutrient_mismatch_before_file_read(
    tmp_path: Path,
) -> None:
    with pytest.raises(CrabError, match="receipt nutrient does not match") as raised:
        prepare_cleanroom_pull_request(
            "crab:ci:cache",
            "feat: carry cache setup",
            _body(),
            _receipt(nutrient_id="crab:ci:other"),
            tmp_path,
        )

    assert raised.value.hint == "expected crab:ci:cache, got crab:ci:other"


def test_publish_reconciles_existing_pr_without_git_or_gh_effects(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "cache.yml").write_text("cache: true\n", encoding="utf-8")
    gh_calls: list[tuple[str, ...]] = []

    result = publish_cleanroom_git_pull_request(
        "crab:ci:cache",
        "feat: carry cache setup",
        _body(),
        _receipt(),
        tmp_path,
        Slug("example", "maw"),
        list_marked_prs=lambda: {
            "crab:ci:cache": {"url": "https://github.com/example/maw/pull/7"}
        },
        run_gh=lambda *args: gh_calls.append(args) or "",
    )

    assert result.url == "https://github.com/example/maw/pull/7"
    assert result.created is False
    assert gh_calls == []


def test_secret_blocks_before_provider_reconciliation_or_git_effects(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    generated.mkdir()
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    (generated / "cache.yml").write_text(f"token: {secret}\n", encoding="utf-8")
    provider_reads: list[str] = []
    gh_calls: list[tuple[str, ...]] = []

    with pytest.raises(CrabError, match="possible secret") as raised:
        publish_cleanroom_git_pull_request(
            "crab:ci:cache",
            "feat: carry cache setup",
            _body(),
            _receipt(),
            tmp_path,
            Slug("example", "maw"),
            list_marked_prs=lambda: provider_reads.append("read") or {},
            run_gh=lambda *args: gh_calls.append(args) or "",
        )

    assert provider_reads == []
    assert gh_calls == []
    assert secret not in str(raised.value)
    assert secret not in str(raised.value.hint)
