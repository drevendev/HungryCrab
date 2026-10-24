"""Independent wiki evidence: page names, headings and counts, never body prose."""

from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath
from typing import Any

from ..errors import ExternalCommandError
from ..fetch.bounded import MAX_STDERR_BYTES, ResponseTooLargeError, run_bounded
from ..fetch.git import SAFE_CONFIG, GitRunner, git_env
from ..mdutil import MdDoc
from ..safety import is_suspicious, safe_name
from .base import MineContext, MinerResult

MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mkdn", ".mkd", ".mdown"}
PAGE_EXTENSIONS = MARKDOWN_EXTENSIONS | {
    ".rst",
    ".rest",
    ".textile",
    ".asciidoc",
    ".adoc",
    ".asc",
    ".org",
    ".mediawiki",
    ".wiki",
    ".creole",
    ".rdoc",
    ".pod",
}
MAX_PAGES = {"normal": 2_000, "deep": 10_000}
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_TREE_BYTES = 8 * 1024 * 1024
# A heading is structure only while it is bounded: one `# ...` line can hold a page of prose.
# The same caps as the README outline in docs.py; wiki.json records the true count.
HEADING_CHARS = 80
MAX_HEADINGS = 60
# wiki.md shows a bounded slice, largest pages first; wiki.json has every page.
MD_PAGES = 100
MD_OUTLINES = 25
MD_HEADINGS = 10

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
# A heading reaches the summary as text, never as live Markdown: an image (a tracking pixel),
# a link target or raw HTML is removed. The inner classes exclude their own opening bracket,
# so each pattern is linear in the heading.
_HEADING_IMAGE_RE = re.compile(r"!\[[^\[\]]*\](?:\([^()]*\)|\[[^\[\]]*\])")
_HEADING_LINK_RE = re.compile(r"\[([^\[\]]*)\](?:\([^()]*\)|\[[^\[\]]*\])")
# A tag, a comment, a declaration or an autolink starts with a letter, `/`, `!` or `?` after
# `<`; `a <= b` is text.
_HEADING_TAG_RE = re.compile(r"<[A-Za-z/!?][^<>]*>")
_HEADING_DEFINITION_RE = re.compile(r"\A\s*\[([^\[\]]*)\]:")
_BACKTICKS_RE = re.compile(r"`+")
_OPENING_ANGLE_RE = re.compile(r"<(?=\S)")
HEADING_OMITTED = "[heading omitted: instruction-like]"


def _code_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each code span: a backtick run closed by the next run of equal length.

    Each length keeps a cursor into its own runs that only moves forward, so the scan is
    linear however many unmatched runs the heading has. A backtick escaped by a backslash is
    text, not part of a run (CommonMark), so `` \\`<img …>` `` opens no span; inside a span
    that rule errs towards closing it later, which only leaves more of the heading as text.
    """
    runs: list[tuple[int, int]] = []
    for match in _BACKTICKS_RE.finditer(text):
        start, end = match.start(), match.end()
        backslashes = 0
        while start - backslashes > 0 and text[start - backslashes - 1] == "\\":
            backslashes += 1
        if backslashes % 2:
            start += 1
        if start < end:
            runs.append((start, end))
    by_length: dict[int, list[int]] = {}
    for index, (start, end) in enumerate(runs):
        by_length.setdefault(end - start, []).append(index)
    cursor = dict.fromkeys(by_length, 0)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(runs):
        start, end = runs[index]
        same = by_length[end - start]
        position = cursor[end - start]
        while position < len(same) and same[position] <= index:
            position += 1
        cursor[end - start] = position
        if position == len(same):
            index += 1
            continue
        closing = same[position]
        spans.append((start, runs[closing][1]))
        index = closing + 1
    return spans


def _generic_type_spans(text: str) -> list[tuple[int, int]]:
    """Find comma-separated, capitalized type arguments in one forward scan.

    These are text rather than HTML (for example, the `<K, V>` in `HashMap<K, V>`).
    Restricting the candidate alphabet and requiring a comma avoids protecting tag
    attributes; advancing past each scanned candidate keeps malformed input linear.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        start = text.find("<", cursor)
        if start < 0:
            break
        if start + 1 >= len(text) or not text[start + 1].isupper():
            cursor = start + 1
            continue

        position = start + 1
        depth = 1
        has_comma = False
        while position < len(text):
            char = text[position]
            if char == "<":
                depth += 1
            elif char == ">":
                depth -= 1
                if depth == 0:
                    if has_comma:
                        spans.append((start, position + 1))
                    cursor = position + 1
                    break
            elif char == ",":
                has_comma = True
            elif not (char.isalnum() or char in "_.," or char.isspace()):
                cursor = position + 1
                break
            position += 1
        else:
            break
        if depth > 0 and position >= len(text):
            break
    return spans


def _plain_generic(generic: str) -> str:
    """Keep type brackets as text while removing any nested HTML tags."""
    inner = generic[1:-1]
    parts: list[str] = []
    position = 0
    removed_tag = False
    nested_types = re.compile(
        r"(?<=[A-Za-z0-9_])<[A-Z][A-Za-z0-9_]*(?:\s*,\s*[A-Z][A-Za-z0-9_]*)*>"
    )
    for match in nested_types.finditer(inner):
        cleaned, count = _HEADING_TAG_RE.subn("", inner[position : match.start()])
        parts.append(cleaned)
        removed_tag |= count > 0
        parts.append(_OPENING_ANGLE_RE.sub("&lt;", match.group()))
        position = match.end()
    cleaned, count = _HEADING_TAG_RE.subn("", inner[position:])
    parts.append(cleaned)
    removed_tag |= count > 0
    return "&lt;" + "".join(parts) + ("" if removed_tag else ">")


def _plain_text(segment: str) -> str:
    parts: list[str] = []
    position = 0
    for start, end in _generic_type_spans(segment):
        parts.append(segment[position:start])
        parts.append(_plain_generic(segment[start:end]))
        position = end
    parts.append(segment[position:])
    # Strip links/images as whole constructs, including ones with a generic in the label.
    text = "".join(parts)
    text = _HEADING_IMAGE_RE.sub("", text)
    text = _HEADING_LINK_RE.sub(r"\1", text)
    text = _HEADING_TAG_RE.sub("", text)
    # Unhandled tag-like fragments are defused; a '<' before whitespace is ordinary text.
    text = _OPENING_ANGLE_RE.sub("&lt;", text).replace("](", "] (")
    # Keep boundary spaces around code spans and preserve untouched heading whitespace.
    return re.sub(r"\s+", " ", text) if _HEADING_TAG_RE.search(segment) else text


def plain_heading(title: str) -> str:
    """The text of a heading without images, link targets, autolinks or inline HTML.

    Code spans are literal in Markdown and kept as written: `Vec<T>` and `crab eat <prey>`
    are text there, not tags. No leading `[label]:` defines a link for another line.
    """
    parts: list[str] = []
    position = 0
    for start, end in _code_spans(title):
        parts.append(_plain_text(title[position:start]))
        parts.append(title[start:end])
        position = end
    parts.append(_plain_text(title[position:]))
    return _HEADING_DEFINITION_RE.sub(r"\1:", "".join(parts)).strip()


def _heading(level: int, raw: str) -> dict[str, object] | None:
    title = plain_heading(raw)
    if is_suspicious(raw) or is_suspicious(title):
        return {"level": level, "text": HEADING_OMITTED}
    return {"level": level, "text": title} if title else None


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


# The HTML blocks that CommonMark runs to an end condition rather than to a blank line (types
# 1-5): `<pre`, `<script`, `<style` and `<textarea` until any of their end tags, a comment until
# `-->`, a processing instruction until `?>`, a declaration until `>` and CDATA until `]]>`.
# Usage text pasted into `<pre>` may hold blank lines and `---`; none of it is a heading.
_HTML_BLOCK_START_RE = re.compile(
    r" {0,3}<(?:(?P<raw>(?i:pre|script|style|textarea))(?=[ \t>]|$)"
    r"|(?P<cdata>!\[CDATA\[)|(?P<comment>!--)|(?P<instruction>\?)|(?P<declaration>![A-Za-z]))"
)
_HTML_RAW_END_RE = re.compile(r"</(?:pre|script|style|textarea)>", re.IGNORECASE)
_HTML_BLOCK_ENDS = {"cdata": "]]>", "comment": "-->", "instruction": "?>", "declaration": ">"}
# `[label]: destination "title"` defines a link and is not paragraph text, so `===` under it
# does not make the definition, URL and all, a heading. Only a whole line of that shape is a
# definition: a destination (`<...>` or one run without spaces) and an optional quoted or
# parenthesised title. `[WIP]: Feature X` is text. Each class excludes its own delimiters and
# the parts are separated by spaces a destination cannot hold, so the match is linear.
_LINK_DEFINITION_RE = re.compile(
    r" {0,3}\[[^\[\]]{1,999}\]:[ \t]*(?:<[^<>]*>|[^\s<]\S*)"
    r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?[ \t]*"
)


def _html_block_ends(kind: str, text: str) -> bool:
    """Whether ``text`` meets the end condition of an HTML block of type ``kind`` (1-5)."""
    if kind == "raw":
        return _HTML_RAW_END_RE.search(text) is not None
    return _HTML_BLOCK_ENDS[kind] in text


def markdown_headings(text: str) -> list[dict[str, object]]:
    """ATX and setext headings outside fenced/indented code, HTML blocks and comments."""
    text = split_frontmatter(text)[1]
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.DOTALL)
    headings: list[dict[str, object]] = []
    fence = ""
    fence_length = 0
    previous = ""
    # Inside a list item, quote, table or HTML block until a blank line: no paragraph starts.
    # An HTML block or a fenced code block leaves it as it found it: one inside a list item does
    # not end the item, so the line after it is still the item's body.
    in_block = False
    # Inside an HTML block of types 1-5 until its end condition, blank lines included.
    html_block = ""
    for line in text.splitlines():
        if html_block:
            if _html_block_ends(html_block, line):
                html_block = ""
            previous = ""
            continue
        if not fence and not line.startswith(("    ", "\t")):
            html = _HTML_BLOCK_START_RE.match(line)
            if html is not None:
                kind = html.lastgroup or ""
                if not _html_block_ends(kind, line[html.start(kind) - 1 :]):
                    html_block = kind
                previous = ""
                continue
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if not fence:
                fence, fence_length = value[0], len(value)
                if not line.startswith(" "):
                    # An unindented fence ends a list item, quote or table (CommonMark).
                    in_block = False
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
        if not line.strip():
            previous = ""
            in_block = False
            continue
        atx = atx_heading(line)
        heading: dict[str, object] | None = None
        if atx is not None:
            heading = _heading(*atx)
            previous = ""
            in_block = False
        elif previous and _SETEXT_UNDERLINE_RE.fullmatch(line):
            heading = _heading(1 if "=" in line else 2, previous)
            previous = ""
        elif _THEMATIC_BREAK_RE.fullmatch(line):
            previous = ""
            in_block = False
        elif _BLOCK_START_RE.match(line) or "|" in line:
            previous = ""
            in_block = True
        elif not previous and _LINK_DEFINITION_RE.fullmatch(line):
            # A definition cannot interrupt a paragraph; under one, the line is paragraph text.
            previous = ""
        else:
            previous = "" if in_block else line.strip()
        if heading is not None:
            headings.append(heading)
    return headings


def _git_bytes(git: GitRunner, *args: str, limit: int) -> bytes:
    """Read local object plumbing with bounded pipes; never materialize a wiki path."""
    env = git_env()
    env["GIT_NO_LAZY_FETCH"] = "1"
    # A recorded object id must mean its own bytes, even in a local wiki with replace refs.
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        result = run_bounded(
            [git.exe, *SAFE_CONFIG, "-C", str(git.cwd), *args],
            env=env,
            timeout=git.timeout,
            stdout_limit=limit,
            stderr_limit=MAX_STDERR_BYTES,
        )
    except (OSError, subprocess.TimeoutExpired, ResponseTooLargeError) as exc:
        raise ExternalCommandError("cannot read wiki Git objects within the read limits") from exc
    if result.returncode:
        raise ExternalCommandError("cannot read wiki Git objects")
    return result.stdout


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
                "unreadable_paths": [],
            },
        }
        pages: list[dict[str, Any]] = []
        root = ctx.wiki_root
        if root is not None:
            git = GitRunner(root)
            sha = ctx.wiki_info.get("sha")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
                raise ExternalCommandError("wiki evidence has no valid recorded commit")
            listing = _git_bytes(git, "ls-tree", "-r", "-z", "-l", sha, limit=MAX_TREE_BYTES)
            for entry in listing.split(b"\0"):
                metadata, separator, raw_name = entry.partition(b"\t")
                fields = metadata.split()
                if not separator or len(fields) != 4 or fields[0] not in (b"100644", b"100755"):
                    continue
                try:
                    name = raw_name.decode("utf-8")
                except UnicodeDecodeError:
                    # Replacement decoding conflates distinct Git paths, including a real
                    # U+FFFD filename. Keep raw identity as hex and refuse healthy coverage.
                    if raw_name.lower().endswith(tuple(ext.encode() for ext in PAGE_EXTENSIONS)):
                        data["coverage"]["healthy"] = False
                        data["coverage"]["unreadable_paths"].append(raw_name.hex())
                    continue
                path = PurePosixPath(name)
                if path.suffix.lower() not in PAGE_EXTENSIONS:
                    continue
                if len(pages) >= MAX_PAGES[ctx.depth]:
                    data["coverage"].update(healthy=False, truncated=True)
                    break
                raw = b""
                size = int(fields[3]) if fields[3].isdigit() else None
                if size is None or fields[1] != b"blob":
                    data["coverage"]["healthy"] = False
                    data["coverage"]["unreadable_pages"].append(name)
                elif size > MAX_PAGE_BYTES:
                    data["coverage"]["healthy"] = False
                    data["coverage"]["oversized_pages"].append(name)
                else:
                    try:
                        raw = _git_bytes(
                            git, "cat-file", "blob", fields[2].decode("ascii"), limit=MAX_PAGE_BYTES
                        )
                        if len(raw) != size:
                            raise ExternalCommandError("wiki blob size differs from its tree entry")
                    except ExternalCommandError:
                        data["coverage"]["healthy"] = False
                        data["coverage"]["unreadable_pages"].append(name)
                        raw = b""
                text = raw.decode("utf-8-sig", errors="replace")
                found = (
                    markdown_headings(text) if path.suffix.lower() in MARKDOWN_EXTENSIONS else []
                )
                headings = [
                    {"level": h["level"], "text": str(h["text"])[:HEADING_CHARS]}
                    for h in found[:MAX_HEADINGS]
                ]
                pages.append(
                    {
                        "path": name,
                        "name": path.stem,
                        "bytes": size or 0,
                        "headings": headings,
                        "heading_count": len(found),
                        "headings_truncated": len(found) > MAX_HEADINGS
                        or any(len(str(h["text"])) > HEADING_CHARS for h in found),
                        # markdown_headings has already replaced a suspicious heading.
                        "suspicious": is_suspicious(name)
                        or any(h["text"] == HEADING_OMITTED for h in found),
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
        by_size = sorted(pages, key=lambda p: (-int(p["bytes"]), str(p["path"])))
        doc.section("Pages (largest first)", priority=1).table(
            ["Page", "Bytes", "Headings", "Flag"],
            (
                [
                    safe_name(p["path"], "page"),
                    p["bytes"],
                    p["heading_count"],
                    "suspicious" if p["suspicious"] else "",
                ]
                for p in by_size
            ),
            max_rows=MD_PAGES,
        )
        for page in by_size[:MD_OUTLINES]:
            doc.section(safe_name(page["path"], "page"), priority=2).bullets(
                (f"H{heading['level']}: {heading['text']}" for heading in page["headings"]),
                max_items=MD_HEADINGS,
            )
        warnings = (
            []
            if data["coverage"]["healthy"]
            else ["wiki visibility loss: inspect wiki.json coverage"]
        )
        return MinerResult(self.name, data, doc, warnings=warnings)
