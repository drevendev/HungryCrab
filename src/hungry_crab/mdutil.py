"""A small Markdown builder with deterministic, lossless paging support.

Miners describe their findings as sections with a priority. ``MdDoc.render_pages`` is the
lossless path every digest file takes: it splits a document into bounded Markdown pages without
mutating the source doc. ``MdDoc.render(max_tokens)`` is the older truncating path, which the
meal files (``menu.md``, ``gap.md``) still use (HungryCrab#140).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .tokens import estimate_tokens


def cell(value: object) -> str:
    """Render a table cell: no newlines, escaped pipes, booleans as yes/no."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    text = str(value)
    return text.replace("\r", "").replace("\n", " ").replace("|", "\\|")


@dataclass
class MdSection:
    heading: str
    priority: int = 5
    lines: list[str] = field(default_factory=list)
    omitted: int = 0

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def para(self, text: str) -> None:
        self.lines.append(text)
        self.lines.append("")

    def bullets(self, items: Iterable[object], *, max_items: int | None = None) -> None:
        count = 0
        for item in items:
            if max_items is not None and count >= max_items:
                self.lines.append("- ... (more in the JSON file)")
                break
            self.lines.append(f"- {cell(item)}")
            count += 1
        if count:
            self.lines.append("")

    def kv(self, pairs: Iterable[tuple[str, object]]) -> None:
        for key, value in pairs:
            self.lines.append(f"- **{key}:** {cell(value)}")
        self.lines.append("")

    def table(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[object]],
        *,
        max_rows: int | None = None,
    ) -> None:
        self.lines.append("| " + " | ".join(cell(h) for h in headers) + " |")
        self.lines.append("|" + "|".join("---" for _ in headers) + "|")
        shown = 0
        hidden = 0
        for row in rows:
            if max_rows is not None and shown >= max_rows:
                hidden += 1
                continue
            self.lines.append("| " + " | ".join(cell(v) for v in row) + " |")
            shown += 1
        if hidden:
            filler = " |" * (len(headers) - 1)
            self.lines.append(f"| ... {hidden} more rows in the JSON file{filler} |")
        self.lines.append("")

    def render_lines(self) -> list[str]:
        out = [f"## {self.heading}", "", *self.lines]
        if self.omitted:
            out.append(
                f"_... {self.omitted} lines omitted to fit the token budget; "
                "the JSON file has everything._"
            )
        if out[-1] != "":
            out.append("")
        return out


@dataclass(frozen=True)
class MdPage:
    """One physical Markdown page and its most important represented section priority."""

    text: str
    priority: int


class MdDoc:
    def __init__(self, title: str, *, source: str | None = None) -> None:
        self.title = title
        self.source = source
        self.sections: list[MdSection] = []

    def section(self, heading: str, *, priority: int = 5) -> MdSection:
        section = MdSection(heading, priority)
        self.sections.append(section)
        return section

    def render(self, max_tokens: int | None = None) -> str:
        if max_tokens is not None:
            self._trim(max_tokens)
        return self._render()

    def render_pages(self, max_tokens: int, filename: str) -> list[MdPage]:
        """Render deterministic pages without dropping source lines or mutating the document.

        Every page repeats the document title and source, so the trace travels with each page.
        Non-final pages name the next physical file. If one generated line is too large to fit
        on an otherwise empty page, it is split into character chunks; this may wrap Markdown
        syntax, but no characters are discarded.
        """
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not filename.endswith(".md"):
            raise ValueError("filename must end with .md")

        remaining = deque(self._page_lines())
        if not remaining:
            return [MdPage(self._render_page([], next_name=None), 5)]

        pages: list[MdPage] = []
        page_number = 1
        while remaining:
            page_lines: list[tuple[str, int]] = []
            while remaining:
                line, priority = remaining[0]
                has_more_after = len(remaining) > 1
                next_name = self._page_name(filename, page_number + 1) if has_more_after else None
                candidate = [*page_lines, (line, priority)]
                if estimate_tokens(self._render_page(candidate, next_name=next_name)) <= max_tokens:
                    page_lines.append(remaining.popleft())
                    continue

                if page_lines:
                    break

                # The fixed page header plus this one generated line cannot fit. Split the line
                # rather than silently truncating it. The remainder stays first in the queue.
                prefix, suffix = self._split_line(line, priority, max_tokens, filename, page_number)
                page_lines.append((prefix, priority))
                remaining[0] = (suffix, priority)
                break

            next_name = self._page_name(filename, page_number + 1) if remaining else None
            text = self._render_page(page_lines, next_name=next_name)
            if estimate_tokens(text) > max_tokens:
                raise ValueError(f"page header exceeds token budget {max_tokens}")
            page_priority = min((priority for _, priority in page_lines), default=5)
            pages.append(MdPage(text=text, priority=page_priority))
            page_number += 1
        return pages

    def _page_lines(self) -> list[tuple[str, int]]:
        lines: list[tuple[str, int]] = []
        for section in self.sections:
            lines.extend((line, section.priority) for line in section.render_lines())
        return lines

    def _render_page(self, lines: list[tuple[str, int]], *, next_name: str | None) -> str:
        out = [f"# {self.title}", ""]
        if self.source:
            out += [f"> {self.source}", ""]
        out.extend(line for line, _ in lines)
        if next_name:
            if out and out[-1] != "":
                out.append("")
            out.append(f"> Next: `{next_name}`")
        return "\n".join(out).rstrip() + "\n"

    @staticmethod
    def _page_name(filename: str, page_number: int) -> str:
        if page_number <= 1:
            return filename
        stem = filename[:-3]
        return f"{stem}.{page_number}.md"

    def _split_line(
        self,
        line: str,
        priority: int,
        max_tokens: int,
        filename: str,
        page_number: int,
    ) -> tuple[str, str]:
        if len(line) < 2:
            raise ValueError(f"page header exceeds token budget {max_tokens}")
        next_name = self._page_name(filename, page_number + 1)
        low, high = 1, len(line) - 1
        best = 0
        while low <= high:
            mid = (low + high) // 2
            text = self._render_page([(line[:mid], priority)], next_name=next_name)
            if estimate_tokens(text) <= max_tokens:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best == 0:
            raise ValueError(f"page header exceeds token budget {max_tokens}")
        return line[:best], line[best:]

    def _render(self) -> str:
        lines = [f"# {self.title}", ""]
        if self.source:
            lines += [f"> {self.source}", ""]
        for section in self.sections:
            lines += section.render_lines()
        return "\n".join(lines).rstrip() + "\n"

    def _trim(self, max_tokens: int) -> None:
        """Pop lines from the least important, longest section until the estimate fits."""
        for _ in range(200_000):
            if estimate_tokens(self._render()) <= max_tokens:
                return
            candidates = [s for s in self.sections if s.lines]
            if not candidates:
                return
            victim = max(candidates, key=lambda s: (s.priority, len(s.lines)))
            victim.lines.pop()
            victim.omitted += 1
