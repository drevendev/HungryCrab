"""A small Markdown builder with a token budget.

Miners describe their findings as sections with a priority; ``MdDoc.render(max_tokens)`` drops
lines from the least important sections first until the estimate fits. Dropped content is never
lost: the JSON twin of every section keeps the full data.
"""

from __future__ import annotations

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
