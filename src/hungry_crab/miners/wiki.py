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

# A setext underline turns the paragraph line above it into a heading, and only a paragraph
# line (CommonMark). A list item, a quote, a table row or an HTML block above `---` or `===`
# is body text, and so is every line that continues one of them lazily until the next blank
# line: underlined, they used to reach the outline verbatim.
_BLOCK_START_RE = re.compile(r" {0,3}(?:[-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$)|[>|<])")
_THEMATIC_BREAK_RE = re.compile(r" {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})")
_SETEXT_UNDERLINE_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*")
# Only the opening run is a regex, and a bounded one; the rest of an ATX line is cut with
# str.strip. `(.+?)\s*#*\s*$` was super-linear in the spaces inside a heading (31.7 s on one
# 3,000-space line) and turned `# C#` into `C`.
_ATX_OPEN_RE = re.compile(r" {0,3}(#{1,6})(?=[ \t]|$)")


def atx_heading(line: str) -> tuple[int, str] | None:
    """(level, text) of an ATX heading line, as CommonMark reads it, or None.

    A closing run of `#` is dropped only when a space or tab precedes it (or it is all there
    is), so `# C#` keeps its `#`.
    """
    opening = _ATX_OPEN_RE.match(line)
    if opening is None:
        return None
    content = line[opening.end() :].strip(" \t")
    unclosed = content.rstrip("#")
    if not unclosed:
        content = ""
    elif unclosed != content and unclosed[-1] in " \t":
        content = unclosed.rstrip(" \t")
    return len(opening.group(1)), content


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(YAML front matter, the text after it), or (None, text) when there is none.

    Found line by line: the first line is `---`, and the block ends at the first later line
    that is `---` or `...` (trailing whitespace allowed). The regexes this replaces could end
    their opening `\\s*\\n` at every leading newline and rescan the rest of the text from each,
    which is quadratic: 100,000 newlines after `---` took 48.7 s. This is one pass.
    """
    first = text.find("\n")
    if first < 0 or text[:first].rstrip(" \t\r") != "---":
        return None, text
    start = position = first + 1
    while position < len(text):
        end = text.find("\n", position)
        stop = len(text) if end < 0 else end
        if text[position:stop].rstrip(" \t\r") in ("---", "..."):
            return text[start:position], "" if end < 0 else text[end + 1 :]
        if end < 0:
            break
        position = end + 1
    return None, text


def markdown_headings(text: str) -> list[dict[str, object]]:
    """ATX and setext headings outside fenced/indented code and HTML comments."""
    text = split_frontmatter(text)[1]
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.DOTALL)
    headings: list[dict[str, object]] = []
    fence = ""
    fence_length = 0
    previous = ""
    # Inside a list item, quote, table or HTML block until a blank line: no paragraph starts.
    in_block = False
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
            in_block = False
            continue
        if fence or line.startswith(("    ", "\t")):
            previous = ""
            continue
        if not line.strip():
            previous = ""
            in_block = False
            continue
        atx = atx_heading(line)
        if atx is not None:
            level, title = atx
            if title:
                headings.append(
                    {
                        "level": level,
                        "text": "[heading omitted: instruction-like]"
                        if is_suspicious(title)
                        else title,
                    }
                )
            previous = ""
            in_block = False
        elif previous and _SETEXT_UNDERLINE_RE.fullmatch(line):
            headings.append(
                {
                    "level": 1 if "=" in line else 2,
                    "text": "[heading omitted: instruction-like]"
                    if is_suspicious(previous)
                    else previous,
                }
            )
            previous = ""
        elif _THEMATIC_BREAK_RE.fullmatch(line):
            previous = ""
            in_block = False
        elif _BLOCK_START_RE.match(line) or "|" in line:
            previous = ""
            in_block = True
        else:
            previous = "" if in_block else line.strip()
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
