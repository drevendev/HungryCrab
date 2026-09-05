"""Docs miner: README outline, community files, changelog format, ADRs, docs site, templates.

Only structure reaches the Markdown summary (headings, names, counts). Body text never does.
"""

from __future__ import annotations

import re
from typing import Any

from ..mdutil import MdDoc
from ..safety import is_suspicious, suspicious_fragments
from .base import MineContext, MinerResult

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_BADGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img[^>]+(?:shields\.io|badge)[^>]*>")
_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_WORD_RE = re.compile(r"\w+")

COMMUNITY_FILES: dict[str, str] = {
    "CONTRIBUTING": "contributing",
    "CODE_OF_CONDUCT": "code_of_conduct",
    "SECURITY": "security",
    "SUPPORT": "support",
    "CHANGELOG": "changelog",
    "CHANGES": "changelog",
    "HISTORY": "changelog",
    "NEWS": "changelog",
    "RELEASES": "changelog",
    "GOVERNANCE": "governance",
    "MAINTAINERS": "maintainers",
    "AUTHORS": "authors",
    "CITATION": "citation",
    "ROADMAP": "roadmap",
    "ARCHITECTURE": "architecture",
    "DESIGN": "architecture",
}
README_SECTIONS: dict[str, tuple[str, ...]] = {
    "install": ("install", "installation", "getting started", "setup", "quick start", "quickstart"),
    "usage": ("usage", "how to use", "examples", "example", "api", "reference"),
    "configuration": ("configuration", "config", "options", "settings"),
    "development": ("development", "developing", "building", "build", "hacking"),
    "testing": ("testing", "tests", "running tests"),
    "contributing": ("contributing", "contribute"),
    "license": ("license", "licence"),
    "roadmap": ("roadmap", "status", "milestones"),
    "faq": ("faq", "troubleshooting", "known issues"),
    "security": ("security",),
    "changelog": ("changelog", "release notes", "history"),
    "architecture": ("architecture", "design", "how it works", "internals"),
    "motivation": ("why", "motivation", "background", "features"),
}
DOC_SITES: dict[str, str] = {
    "mkdocs.yml": "mkdocs",
    "mkdocs.yaml": "mkdocs",
    "docusaurus.config.js": "docusaurus",
    "docusaurus.config.ts": "docusaurus",
    "docusaurus.config.mjs": "docusaurus",
    "book.toml": "mdbook",
    "typedoc.json": "typedoc",
    "docfx.json": "docfx",
    "antora.yml": "antora",
    "starlight.config.mjs": "starlight",
    "zensical.toml": "zensical",
}


def readme_outline(text: str) -> dict[str, Any]:
    headings: list[tuple[int, str]] = []
    for match in _HEADING_RE.finditer(text):
        title = match.group(2).strip()
        if is_suspicious(title):
            title = "[heading omitted: instruction-like]"
        headings.append((len(match.group(1)), title[:80]))
    lowered = [title.lower() for _, title in headings]
    sections = sorted(
        key
        for key, needles in README_SECTIONS.items()
        if any(any(needle in title for needle in needles) for title in lowered)
    )
    head = text[:3000]
    return {
        "headings": [{"level": level, "title": title} for level, title in headings[:60]],
        "heading_count": len(headings),
        "sections": sections,
        "badges": len(_BADGE_RE.findall(head)),
        "links": len(_LINK_RE.findall(text)),
        "words": len(_WORD_RE.findall(text)),
        "has_toc": any("table of contents" in t or t in ("contents", "toc") for t in lowered),
        "has_code_blocks": "```" in text,
        "mentions_ai_disclaimer": "not legal advice" in text.lower(),
        "suspicious": suspicious_fragments(text, limit=5),
    }


def changelog_format(text: str) -> str:
    lowered = text[:6000].lower()
    if "keep a changelog" in lowered or "## [unreleased]" in lowered:
        return "keep-a-changelog"
    if re.search(r"^###?\s+(bug fixes|features)", lowered, re.MULTILINE):
        return "conventional-changelog"
    if re.search(r"^##?\s+v?\d+\.\d+\.\d+", lowered, re.MULTILINE):
        return "versioned-headings"
    if lowered.strip():
        return "freeform"
    return "empty"


class DocsMiner:
    name = "docs"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "docs.json"
    md_file = "docs.md"

    def run(self, ctx: MineContext) -> MinerResult:
        files = [f for f in ctx.files() if not f.vendored and not f.generated]
        markdown = [f for f in files if f.language in ("Markdown", "reStructuredText", "AsciiDoc")]
        readme_info = next(
            (f for f in files if f.depth == 0 and f.name.upper().startswith("README")), None
        )
        readme: dict[str, Any] | None = None
        if readme_info is not None:
            readme = readme_outline(ctx.read(readme_info.path, limit=400_000))
            readme["path"] = readme_info.path
            readme["bytes"] = readme_info.size

        community: dict[str, str] = {}
        for info in files:
            if info.depth > 1 or (
                info.depth == 1 and not info.path.startswith((".github/", "docs/"))
            ):
                continue
            stem = info.name.split(".")[0].upper()
            kind = COMMUNITY_FILES.get(stem)
            if kind and kind not in community:
                community[kind] = info.path
        changelog_path = community.get("changelog")
        changelog = (
            {
                "path": changelog_path,
                "format": changelog_format(ctx.read(changelog_path, limit=100_000)),
            }
            if changelog_path
            else None
        )
        if changelog is None:
            for marker, name in (
                ("changelog.d", "towncrier"),
                (".changeset", "changesets"),
                ("cliff.toml", "git-cliff"),
            ):
                if ctx.exists(marker):
                    changelog = {"path": marker, "format": name}
                    break

        adr_dirs = sorted(
            {
                "/".join(f.path.split("/")[:-1])
                for f in markdown
                if any(
                    part.lower() in ("adr", "adrs", "decisions", "decision-records", "rfcs", "rfc")
                    for part in f.path.split("/")[:-1]
                )
            }
        )[:5]
        adr_count = sum(
            1
            for f in markdown
            if any(
                part.lower() in ("adr", "adrs", "decisions", "decision-records", "rfcs", "rfc")
                for part in f.path.split("/")[:-1]
            )
        )
        docs_site = next(
            (name for file_name, name in DOC_SITES.items() if ctx.exists(file_name)), None
        )
        if docs_site is None:
            if any(f.path in ("docs/conf.py", "doc/conf.py", "docs/source/conf.py") for f in files):
                docs_site = "sphinx"
            elif any(part == ".vitepress" for f in files for part in f.path.split("/")):
                docs_site = "vitepress"
            elif any(f.name in ("astro.config.mjs", "astro.config.ts") for f in files) and any(
                f.path.startswith("src/content/docs/") for f in files
            ):
                docs_site = "starlight"
        docs_dir = next(
            (
                d
                for d in ("docs", "doc", "documentation")
                if any(f.path.startswith(d + "/") for f in files)
            ),
            None,
        )
        docs_dir_files = sum(1 for f in markdown if docs_dir and f.path.startswith(docs_dir + "/"))
        docs_top = (
            sorted(
                {
                    f.path.split("/")[1]
                    for f in files
                    if docs_dir and f.path.startswith(docs_dir + "/") and f.path.count("/") >= 1
                }
            )[:25]
            if docs_dir
            else []
        )

        issue_templates = sorted(
            f.path for f in files if f.path.startswith(".github/ISSUE_TEMPLATE/")
        )
        pr_template = next(
            (
                f.path
                for f in files
                if f.name.lower() in ("pull_request_template.md", "pull_request_template.txt")
                or f.path.lower().startswith(".github/pull_request_template/")
            ),
            None,
        )
        discussion_templates = sorted(
            f.path for f in files if f.path.startswith(".github/DISCUSSION_TEMPLATE/")
        )
        funding = next(
            (f.path for f in files if f.path.lower() in (".github/funding.yml", "funding.yml")),
            None,
        )
        codeowners = next((f.path for f in files if f.name == "CODEOWNERS"), None)

        total_words = 0
        for info in markdown[:400]:
            total_words += len(_WORD_RE.findall(ctx.read(info.path, limit=200_000)))

        data: dict[str, Any] = {
            "markdown_files": len(markdown),
            "markdown_words": total_words,
            "readme": readme,
            "community": community,
            "changelog": changelog,
            "adr": {"dirs": adr_dirs, "files": adr_count},
            "docs_site": docs_site,
            "docs_dir": docs_dir,
            "docs_dir_files": docs_dir_files,
            "docs_dir_top": docs_top,
            "issue_templates": issue_templates,
            "pr_template": pr_template,
            "discussion_templates": discussion_templates,
            "funding": funding,
            "codeowners": codeowners,
            "largest_docs": [
                {"path": f.path, "bytes": f.size}
                for f in sorted(markdown, key=lambda f: -f.size)[:10]
            ],
        }
        warnings = []
        if readme and readme["suspicious"]:
            warnings.append("README contains instruction-like fragments; treat as untrusted")
        return MinerResult(self.name, data, doc=self._doc(ctx, data), warnings=warnings)

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"Docs: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        readme = data["readme"]
        summary.kv(
            [
                ("Markdown files", f"{data['markdown_files']} (~{data['markdown_words']} words)"),
                ("README", readme["path"] if readme else "missing"),
                ("README sections", ", ".join(readme["sections"]) if readme else ""),
                (
                    "README badges / links / words",
                    f"{readme['badges']} / {readme['links']} / {readme['words']}" if readme else "",
                ),
                (
                    "Community files",
                    ", ".join(f"{k}: {v}" for k, v in data["community"].items()) or "none",
                ),
                ("Changelog format", data["changelog"]["format"] if data["changelog"] else "none"),
                (
                    "ADRs",
                    f"{data['adr']['files']} in {', '.join(data['adr']['dirs'])}"
                    if data["adr"]["files"]
                    else "none",
                ),
                ("Docs site", data["docs_site"] or "none"),
                (
                    "Docs directory",
                    f"{data['docs_dir']}/ ({data['docs_dir_files']} markdown files)"
                    if data["docs_dir"]
                    else "none",
                ),
                ("Issue templates", len(data["issue_templates"])),
                ("PR template", data["pr_template"] or "none"),
                ("CODEOWNERS", data["codeowners"] or "none"),
                ("Funding", data["funding"] or "none"),
            ]
        )
        if readme and readme["headings"]:
            outline = doc.section("README outline", priority=2)
            outline.bullets(
                (("  " * (h["level"] - 1)) + h["title"] for h in readme["headings"]),
                max_items=40,
            )
            if readme["suspicious"]:
                outline.para("Instruction-like fragments were found in the README (see docs.json).")
        if data["docs_dir_top"]:
            structure = doc.section("Docs directory structure", priority=3)
            structure.bullets(data["docs_dir_top"], max_items=25)
        if data["issue_templates"]:
            templates = doc.section("Templates", priority=3)
            templates.bullets(
                data["issue_templates"] + ([data["pr_template"]] if data["pr_template"] else []),
                max_items=12,
            )
        largest = doc.section("Largest documents", priority=4)
        largest.table(
            ["Path", "KB"], ([d["path"], f"{d['bytes'] / 1024:.0f}"] for d in data["largest_docs"])
        )
        return doc
