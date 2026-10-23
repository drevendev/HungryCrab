from __future__ import annotations

import base64
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from hungry_crab.cli import main
from hungry_crab.errors import CrabError, UsageError
from hungry_crab.report import STYLE, render_report, write_report


class Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def menu_file(tmp_path: Path, *, cards: int = 1) -> Path:
    data = {
        "schema": "hungry-crab.menu/1",
        "prey": {"label": "example/prey", "sha": "a" * 40},
        "maw": {"label": "example/maw", "sha": "b" * 40},
        "candidates": [
            {
                "id": f"crab:ci:cache-{i}",
                "title": "<script>alert(1)</script>",
                "category": "ci",
                "license_mode": "COPY",
                "what": "Use a cache & verify it",
                "why": "<img src=x onerror=alert(2)>",
                "score": 0.5,
                "evidence": [
                    {
                        "path": "safe source",
                        "url": "https://github.com/example/prey/blob/" + "a" * 40 + "/ci.yml",
                    },
                    {"path": "unsafe link", "url": "javascript:alert(1)"},
                ],
            }
            for i in range(cards)
        ],
        "hidden": [{"id": "crab:ci:old", "reason": "ledger: rejected"}],
    }
    path = tmp_path / "menu.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_report_is_inert_self_contained_and_does_not_drop_cards(tmp_path: Path) -> None:
    path = menu_file(tmp_path, cards=45)
    rendered = render_report(path)
    parsed = Elements()
    parsed.feed(rendered)
    assert "&lt;script&gt;" in rendered and "&lt;img" in rendered
    assert "cache-44" in rendered and "ledger: rejected" in rendered
    assert not any(
        tag in {"script", "img", "iframe", "object", "base", "form"} for tag, _ in parsed.tags
    )
    links = [
        attrs
        for tag, attrs in parsed.tags
        if tag == "a" and not str(attrs.get("href", "")).startswith("#")
    ]
    assert links and all(str(row["href"]).startswith("https://") for row in links)
    assert all(row["rel"] == "noopener noreferrer" for row in links)
    assert not any(key.startswith("on") for _, attrs in parsed.tags for key in attrs)
    expected = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
    assert f"sha256-{expected}" in rendered
    assert "Unknown" in rendered and "45" in rendered


@pytest.mark.parametrize(
    "url",
    [
        "data:text/html,x",
        "file:///secret",
        "//example.com",
        "java\nscript:evil",
        "https://user:password@example.com",
    ],
)
def test_unsafe_evidence_is_text_only(tmp_path: Path, url: str) -> None:
    path = menu_file(tmp_path)
    data = json.loads(path.read_text())
    data["candidates"][0]["evidence"] = [{"path": "plain evidence", "url": url}]
    path.write_text(json.dumps(data), encoding="utf-8")
    parsed = Elements()
    parsed.feed(render_report(path))
    assert not [
        attrs
        for tag, attrs in parsed.tags
        if tag == "a" and not str(attrs.get("href", "")).startswith("#")
    ]


def test_empty_menu_and_bad_or_duplicate_json(tmp_path: Path) -> None:
    path = menu_file(tmp_path, cards=0)
    assert "No new candidates" in render_report(path)
    for raw in ('{"schema": 1, "schema": 2}', "[]", "{", '{"schema":"future"}'):
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(CrabError):
            render_report(path)


def test_report_reads_existing_digest_without_network_or_subprocess(
    npm_digest: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    import subprocess

    monkeypatch.setattr(
        socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("network")
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("subprocess"))
    source = npm_digest.out_dir
    before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    result = write_report(source, tmp_path / "digest.html")
    assert result.is_file() and "Miner health" in result.read_text(encoding="utf-8")
    assert {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()} == before


def test_cli_refuses_overwrite_then_allows_explicit_regular_html_replacement(
    tmp_path: Path,
) -> None:
    source = menu_file(tmp_path)
    output = tmp_path / "report.html"
    assert main(["report", str(source), "--out", str(output)]) == 0
    before = output.read_bytes()
    assert main(["report", str(source), "--out", str(output)]) == 2
    assert output.read_bytes() == before
    assert main(["report", str(source), "--out", str(output), "--force"]) == 0
    assert not list(tmp_path.glob(".*.tmp"))
    with pytest.raises(UsageError, match=r"\.html"):
        write_report(source, tmp_path / "manifest.json", force=True)


def test_digest_page_link_cannot_read_outside_the_digest(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("NEVER_EMBED_THIS", encoding="utf-8")
    digest = tmp_path / "digest"
    digest.mkdir()
    manifest = {
        "schema": "hungry-crab.digest/1",
        "files": [{"name": "../outside.md", "miner": "inventory"}],
        "miners": [],
    }
    path = digest / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CrabError, match="invalid artifact"):
        render_report(digest)


@pytest.mark.parametrize("oversized", [False, True])
def test_digest_report_stops_reading_pages_when_aggregate_budget_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, oversized: bool
) -> None:
    from hungry_crab import report

    pages = ["inventory.md", *(f"inventory.{i}.md" for i in range(2, 10))]
    for name in pages:
        (tmp_path / name).write_bytes(b"x" * (8193 if oversized else 8192))
    manifest = {
        "schema": "hungry-crab.digest/1",
        "files": [{"name": name, "miner": "inventory"} for name in pages],
        "miners": [],
        "coverage": {"healthy": True},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(report, "MAX_REPORT_BYTES", 65536)
    monkeypatch.setattr(report, "MAX_PAGE_BYTES", 8192)
    reads: list[str] = []
    real_read = report.read_bytes

    def counted_read(path: Path, limit: int) -> bytes:
        reads.append(path.name)
        return real_read(path, limit)

    monkeypatch.setattr(report, "read_bytes", counted_read)
    rendered = render_report(tmp_path)
    assert reads == pages[:4]
    assert rendered.count("Report read budget exhausted; omitted pages:") == 1
    assert ", ".join(pages[4:]) in rendered
