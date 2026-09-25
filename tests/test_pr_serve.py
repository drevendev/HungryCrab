from __future__ import annotations

import json
from pathlib import Path

import pytest

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.pr_publication import GeneratedFile, nutrient_branch_name, nutrient_spec_path
from hungry_crab.pr_serve import (
    prepare_cleanroom_pull_request,
    publish_cleanroom_git_pull_request,
)

TRACE = "implemented from a specification, without access to the prey source"
SPEC = "# Behaviour\n\nCache the dependencies between runs; a cold cache must not fail the job.\n"


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


def _write_spec(maw: Path, nutrient_id: str = "crab:ci:cache", text: str = SPEC) -> str:
    path = nutrient_spec_path(nutrient_id)
    target = maw / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return path


def _maw(tmp_path: Path) -> Path:
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "cache.yml").write_text("cache: true\n", encoding="utf-8")
    _write_spec(tmp_path)
    return tmp_path


def test_prepare_freezes_the_receipt_files_and_the_specification(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    (maw / "unrelated.txt").write_text("dirty maintainer edit\n", encoding="utf-8")
    spec = nutrient_spec_path("crab:ci:cache")

    prepared = prepare_cleanroom_pull_request(
        "crab:ci:cache",
        "feat: carry cache setup",
        _body(),
        _receipt(),
        maw,
    )

    assert prepared.files == (
        GeneratedFile("generated/cache.yml", "cache: true\n"),
        GeneratedFile(spec, SPEC),
    )
    assert "unrelated.txt" not in {generated.path for generated in prepared.files}
    assert prepared.body.startswith("<!-- crab:ci:cache -->")
    assert TRACE in prepared.body
    # without a slug the link is the maw-relative path itself
    assert f"Specification: [`{spec}`]({spec}), carried in this pull request." in prepared.body


def test_prepare_links_the_specification_on_the_nutrient_branch(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    spec = nutrient_spec_path("crab:ci:cache")
    branch = nutrient_branch_name("crab:ci:cache")

    prepared = prepare_cleanroom_pull_request(
        "crab:ci:cache",
        "feat: carry cache setup",
        _body(),
        _receipt(),
        maw,
        slug=Slug("example", "maw"),
    )

    link = f"https://github.com/example/maw/blob/{branch}/{spec}"
    assert f"[`{spec}`]({link})" in prepared.body
    assert prepared.body.endswith("carried in this pull request.\n")


def test_prepare_refuses_without_a_specification(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "cache.yml").write_text("cache: true\n", encoding="utf-8")

    with pytest.raises(CrabError, match="specification missing") as raised:
        prepare_cleanroom_pull_request(
            "crab:ci:cache",
            "feat: carry cache setup",
            _body(),
            _receipt(),
            tmp_path,
        )

    assert raised.value.hint is not None
    assert nutrient_spec_path("crab:ci:cache") in raised.value.hint
    assert "crab spec crab:ci:cache" in raised.value.hint


def test_a_specification_the_receipt_lists_is_carried_once(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    spec = nutrient_spec_path("crab:ci:cache")

    prepared = prepare_cleanroom_pull_request(
        "crab:ci:cache",
        "feat: carry cache setup",
        _body(),
        _receipt(paths=["generated/cache.yml", spec]),
        maw,
    )

    assert [generated.path for generated in prepared.files] == ["generated/cache.yml", spec]


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
    maw = _maw(tmp_path)
    gh_calls: list[tuple[str, ...]] = []

    result = publish_cleanroom_git_pull_request(
        "crab:ci:cache",
        "feat: carry cache setup",
        _body(),
        _receipt(),
        maw,
        Slug("example", "maw"),
        list_marked_prs=lambda: {"crab:ci:cache": {"url": "https://github.com/example/maw/pull/7"}},
        run_gh=lambda *args: gh_calls.append(args) or "",
    )

    assert result.url == "https://github.com/example/maw/pull/7"
    assert result.created is False
    assert gh_calls == []


def test_secret_blocks_before_provider_reconciliation_or_git_effects(tmp_path: Path) -> None:
    maw = _maw(tmp_path)
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    (maw / "generated" / "cache.yml").write_text(f"token: {secret}\n", encoding="utf-8")
    provider_reads: list[str] = []
    gh_calls: list[tuple[str, ...]] = []

    with pytest.raises(CrabError, match="possible secret") as raised:
        publish_cleanroom_git_pull_request(
            "crab:ci:cache",
            "feat: carry cache setup",
            _body(),
            _receipt(),
            maw,
            Slug("example", "maw"),
            list_marked_prs=lambda: provider_reads.append("read") or {},
            run_gh=lambda *args: gh_calls.append(args) or "",
        )

    assert provider_reads == []
    assert gh_calls == []
    assert secret not in str(raised.value)
    assert secret not in str(raised.value.hint)


def test_a_secret_in_the_specification_blocks_publication_too(tmp_path: Path) -> None:
    """The specification is published text: it is scanned like every other payload file."""
    maw = _maw(tmp_path)
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    _write_spec(maw, text=f"# Behaviour\n\nUse token {secret} to fetch.\n")
    gh_calls: list[tuple[str, ...]] = []

    with pytest.raises(CrabError, match="possible secret") as raised:
        publish_cleanroom_git_pull_request(
            "crab:ci:cache",
            "feat: carry cache setup",
            _body(),
            _receipt(),
            maw,
            Slug("example", "maw"),
            list_marked_prs=dict,
            run_gh=lambda *args: gh_calls.append(args) or "",
        )

    assert gh_calls == []
    assert secret not in str(raised.value) and secret not in str(raised.value.hint)
