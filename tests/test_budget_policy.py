from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXED_NOW

from hungry_crab.budget import apply_markdown_policy
from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.errors import CrabError
from hungry_crab.tokens import estimate_tokens


def test_enforce_drops_low_priority_pages_and_repairs_next_link(tmp_path: Path) -> None:
    out = tmp_path / "digest"
    out.mkdir()
    first = "# Docs\n\nimportant\n\n> Next: `docs.2.md`\n"
    low = "# Docs\n\nlow priority " + "x" * 100 + "\n"
    medium = "# CI\n\nmedium\n"
    (out / "docs.md").write_text(first, encoding="utf-8")
    (out / "docs.2.md").write_text(low, encoding="utf-8")
    (out / "ci.md").write_text(medium, encoding="utf-8")
    records = [
        {
            "name": "docs",
            "files": ["docs.md", "docs.2.md"],
            "page_priorities": {"docs.md": 1, "docs.2.md": 9},
        },
        {"name": "ci", "files": ["ci.md"], "page_priorities": {"ci.md": 5}},
    ]
    total = sum(estimate_tokens(text) for text in (first, low, medium))
    result = apply_markdown_policy(
        records,
        out,
        total_budget=total - estimate_tokens(low),
        policy="enforce",
    )

    assert [page["name"] for page in result.dropped_pages] == ["docs.2.md"]
    assert not (out / "docs.2.md").exists()
    assert "> Next:" not in (out / "docs.md").read_text(encoding="utf-8")
    assert records[0]["files"] == ["docs.md"]
    assert records[0]["page_priorities"] == {"docs.md": 1}
    assert result.after_tokens <= total - estimate_tokens(low)


def test_warn_and_off_keep_complete_digest(tmp_path: Path) -> None:
    out = tmp_path / "digest"
    out.mkdir()
    text = "# Docs\n\n" + "x" * 100 + "\n"
    (out / "docs.md").write_text(text, encoding="utf-8")
    records = [
        {"name": "docs", "files": ["docs.md"], "page_priorities": {"docs.md": 5}}
    ]

    warned = apply_markdown_policy(records, out, total_budget=1, policy="warn")
    assert warned.dropped_pages == []
    assert warned.over_by_tokens > 0
    assert (out / "docs.md").is_file()

    unlimited = apply_markdown_policy(records, out, total_budget=1, policy="off")
    assert unlimited.dropped_pages == []
    assert unlimited.over_by_tokens == 0
    assert (out / "docs.md").is_file()


def test_budget_policy_is_cache_identity(npm_app: Path, tmp_path: Path) -> None:
    options = DigestOptions(
        out=tmp_path / "out",
        now=FIXED_NOW,
        cache_root=tmp_path / "cache",
        total_budget=1,
        budget_policy="warn",
    )
    first = run_digest(Target(path=npm_app), options)
    assert not first.cached
    assert first.manifest["over_budget"] is True
    assert first.manifest["dropped_pages"] == []
    assert first.manifest["budget"]["over_by_tokens_est"] > 0

    same = run_digest(Target(path=npm_app), DigestOptions(**options.__dict__))
    assert same.cached

    enforced = run_digest(
        Target(path=npm_app),
        DigestOptions(**{**options.__dict__, "budget_policy": "enforce"}),
    )
    assert not enforced.cached
    assert enforced.manifest["budget"]["policy"] == "enforce"
    assert enforced.manifest["over_budget"] is False
    assert enforced.manifest["markdown_tokens_est"] <= 1
    assert enforced.manifest["dropped_pages"]
    dropped = {page["name"] for page in enforced.manifest["dropped_pages"]}
    assert dropped.isdisjoint({entry["name"] for entry in enforced.manifest["files"]})
    assert all(
        dropped.isdisjoint(set(record["files"])) for record in enforced.manifest["miners"]
    )


def test_unknown_budget_policy_is_rejected(npm_app: Path) -> None:
    with pytest.raises(CrabError, match="unknown budget policy"):
        run_digest(Target(path=npm_app), DigestOptions(budget_policy="maybe"))
