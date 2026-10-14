"""Independent wiki evidence: page names, headings and counts, never body prose."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..fetch.git import GitRunner
from ..mdutil import MdDoc
from ..safety import is_suspicious
from .base import MineContext, MinerResult

PAGE_EXTENSIONS = {".md", ".markdown", ".rst", ".textile", ".asciidoc", ".org", ".mediawiki"}
MAX_PAGES = {"normal": 2_000, "deep": 10_000}
MAX_PAGE_BYTES = 2 * 1024 * 1024


def markdown_headings(text: str) -> list[dict[str, object]]:
    """ATX and setext headings outside fenced/indented code and HTML comments."""
    text = re.sub(r"\A---\s*\n.*?\n(?:---|\.\.\.)\s*(?:\n|\Z)", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.DOTALL)
    headings: list[dict[str, object]] = []
    fence = ""
    fence_length = 0
    previous = ""
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if not fence:
                fence, fence_length = value[0], len(value)
            elif (
                value[0] == fence
                and len(value) >= fence_length
                and not line[marker.end() :].strip()
            ):
                fence = ""
            previous = ""
            continue
        if fence or line.startswith(("    ", "\t")):
            previous = ""
            continue
        match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            title = match.group(2)
            headings.append(
                {
                    "level": len(match.group(1)),
                    "text": "[heading omitted: instruction-like]"
                    if is_suspicious(title)
                    else title,
                }
            )
        elif previous and re.fullmatch(r" {0,3}(?:=+|-+)\s*", line):
            headings.append(
                {
                    "level": 1 if "=" in line else 2,
                    "text": "[heading omitted: instruction-like]"
                    if is_suspicious(previous)
                    else previous,
                }
            )
        previous = line.strip() if not match else ""
    return headings


class WikiMiner:
    name = "wiki"
    requires: tuple[str, ...] = ()
    json_file = "wiki.json"
    md_file = "wiki.md"

    def run(self, ctx: MineContext) -> MinerResult:
        data: dict[str, Any] = {
            **ctx.wiki_info,
            "pages": [],
            "page_count": 0,
            "bytes": 0,
            "available": False,
            "coverage": {
                "healthy": True,
                "truncated": False,
                "oversized_pages": [],
                "unreadable_pages": [],
            },
        }
        pages: list[dict[str, Any]] = []
        root = ctx.wiki_root
        if root is not None:
            git = GitRunner(root)
            for entry in git.run("ls-tree", "-r", "-z", "HEAD").split("\0"):
                metadata, separator, name = entry.partition("\t")
                if not separator or metadata.split()[0] not in ("100644", "100755"):
                    continue
                path = root / name
                if path.suffix.lower() not in PAGE_EXTENSIONS or path.is_symlink():
                    continue
                if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                    continue
                if len(pages) >= MAX_PAGES[ctx.depth]:
                    data["coverage"].update(healthy=False, truncated=True)
                    break
                try:
                    with path.open("rb") as handle:
                        raw = handle.read(MAX_PAGE_BYTES + 1)
                except OSError:
                    data["coverage"]["healthy"] = False
                    data["coverage"]["unreadable_pages"].append(name)
                    raw = b""
                if len(raw) > MAX_PAGE_BYTES:
                    data["coverage"]["healthy"] = False
                    data["coverage"]["oversized_pages"].append(name)
                text = raw[:MAX_PAGE_BYTES].decode("utf-8-sig", errors="replace")
                headings = (
                    markdown_headings(text) if path.suffix.lower() in (".md", ".markdown") else []
                )
                pages.append(
                    {
                        "path": name,
                        "name": Path(name).stem,
                        "bytes": path.stat().st_size,
                        "headings": headings,
                        "suspicious": is_suspicious(name)
                        or any(is_suspicious(str(h["text"])) for h in headings),
                    }
                )
            pages.sort(key=lambda p: str(p["path"]))
            data.update(
                available=True,
                pages=pages,
                page_count=len(pages),
                bytes=sum(p["bytes"] for p in pages),
            )
        doc = MdDoc(f"Wiki · {ctx.label}", source=ctx.source_line())
        doc.section("Snapshot", priority=0).table(
            ["Metric", "Value"],
            [
                ["Status", data.get("status", "not_requested")],
                ["Wiki commit", data.get("sha") or "—"],
                ["Pages", len(pages)],
                ["Bytes", data["bytes"]],
            ],
        )
        doc.section("Pages", priority=1).table(
            ["Page", "Bytes", "Headings", "Flag"],
            [
                [p["path"], p["bytes"], len(p["headings"]), "suspicious" if p["suspicious"] else ""]
                for p in pages
            ],
        )
        for page in pages:
            doc.section(str(page["path"]), priority=2).bullets(
                f"H{heading['level']}: {heading['text']}" for heading in page["headings"]
            )
        warnings = (
            []
            if data["coverage"]["healthy"]
            else ["wiki visibility loss: inspect wiki.json coverage"]
        )
        return MinerResult(self.name, data, doc, warnings=warnings)
