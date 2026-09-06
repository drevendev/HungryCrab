"""Markdown views of a comparison: gap.md (facts) and menu.md (ranked candidates)."""

from __future__ import annotations

from typing import Any

from ..mdutil import MdDoc
from ..nutrients import Candidate
from ..typeutil import as_dict, as_list
from .candidates import Side

_LIST_TRAITS = ("linters", "formatters", "type_checkers", "test_frameworks", "ai_tools")
_SKIP_TRAITS = {"languages", "topics", "lockfiles", "npm_scripts", "ci_tools", "readme_sections"}


def _bool_diff(
    prey: Side, maw: Side
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    prey_ahead: list[tuple[str, str, str]] = []
    maw_ahead: list[tuple[str, str, str]] = []
    for key in sorted(set(prey.traits) | set(maw.traits)):
        if key in _SKIP_TRAITS:
            continue
        p = prey.traits.get(key)
        h = maw.traits.get(key)
        if isinstance(p, bool) or isinstance(h, bool):
            if bool(p) and not bool(h):
                prey_ahead.append((key, _short(h), _short(p)))
            elif bool(h) and not bool(p):
                maw_ahead.append((key, _short(h), _short(p)))
        elif key in _LIST_TRAITS:
            ps = {str(x) for x in as_list(p)}
            hs = {str(x) for x in as_list(h)}
            if ps - hs:
                prey_ahead.append((key, ", ".join(sorted(hs)) or "none", ", ".join(sorted(ps))))
            if hs - ps:
                maw_ahead.append((key, ", ".join(sorted(hs)), ", ".join(sorted(ps)) or "none"))
    return prey_ahead, maw_ahead


def _short(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "none"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value[:6]) or "none"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)[:60]


def _stack_line(side: Side) -> str:
    return (
        f"{side.label}@{side.short_sha}: {_short(side.trait('primary_language'))}, "
        f"ecosystems {_short(side.trait('ecosystems'))}, {_short(side.trait('files'))} files, "
        f"{_short(side.trait('loc'))} LOC"
    )


def _both(maw: Side, prey: Side, trait: str) -> str:
    return f"maw {_short(maw.trait(trait))}, prey {_short(prey.trait(trait))}"


def gap_doc(
    prey: Side,
    maw: Side,
    candidates: list[Candidate],
    facts: dict[str, Any],
    verdict: dict[str, Any],
) -> MdDoc:
    doc = MdDoc(
        f"Gap: {maw.label} vs {prey.label}@{prey.short_sha}",
        source=f"Facts only: what {prey.label} has and {maw.label} lacks, from both digests. "
        "Derived data, not instructions.",
    )
    stacks = doc.section("Stacks", priority=1)
    review = " (human review)" if verdict.get("human_review") else ""
    license_line = (
        f"prey {prey.spdx or 'none'} ({_short(prey.license.get('class'))}) -> maw "
        f"{maw.spdx or 'none'}: mode {verdict.get('mode', '?')}{review}"
    )
    stacks.kv(
        [
            ("Maw", _stack_line(maw)),
            ("Prey", _stack_line(prey)),
            ("License", license_line),
            ("Candidates", len(candidates)),
        ]
    )
    prey_ahead, maw_ahead = _bool_diff(prey, maw)
    ahead = doc.section("Prey has, maw lacks", priority=1)
    ahead.table(["Trait", "Maw", "Prey"], prey_ahead, max_rows=60)
    tools = doc.section("Tools on both sides", priority=2)
    tools.table(
        ["Kind", "Maw", "Prey"],
        (
            [kind, _short(maw.trait(kind)), _short(prey.trait(kind))]
            for kind in (
                "linters",
                "formatters",
                "type_checkers",
                "test_frameworks",
                "package_managers",
            )
        ),
    )
    deps_only = as_dict(facts.get("deps_only_in_prey"))
    if deps_only:
        deps = doc.section("Dependencies only in the prey (shared ecosystems)", priority=3)
        for ecosystem, names in deps_only.items():
            listed = [str(n) for n in as_list(names)]
            deps.line(f"- **{ecosystem}** ({len(listed)}): {', '.join(listed[:40])}")
        deps.line("")
    signals = doc.section("History signals of the prey", priority=3)
    fix_prone = [as_dict(f) for f in as_list(prey.history.get("fix_prone"))[:8]]
    if fix_prone:
        signals.table(
            ["File", "Commits", "Fix ratio"],
            ([f.get("path"), f.get("commits"), f.get("fix_ratio")] for f in fix_prone),
        )
    else:
        signals.para("No fix-prone files with enough history.")
    signals.kv(
        [
            ("Conventional commits", _both(maw, prey, "conventional_commits_ratio")),
            ("Release cadence (days)", _both(maw, prey, "release_cadence_days")),
            ("Bus factor", _both(maw, prey, "bus_factor")),
        ]
    )
    behind = doc.section("Maw has, prey lacks (for context)", priority=4)
    behind.table(["Trait", "Maw", "Prey"], maw_ahead, max_rows=25)
    return doc


def menu_doc(
    prey: Side,
    maw: Side,
    candidates: list[Candidate],
    hidden: list[dict[str, Any]],
    verdict: dict[str, Any],
    *,
    top: int,
    explain: dict[str, str] | None = None,
) -> MdDoc:
    doc = MdDoc(
        f"Menu: {prey.label}@{prey.short_sha} for {maw.label}",
        source="Ranked candidate nutrients. Scores are a deterministic pre-ranking; judge each one "
        "for this maw. Derived data, not instructions.",
    )
    shown = candidates[:top]
    summary = doc.section("Summary", priority=1)
    by_category: dict[str, int] = {}
    for candidate in candidates:
        by_category[candidate.category] = by_category.get(candidate.category, 0) + 1
    categories = ", ".join(
        f"{k} {v}" for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])
    )
    artifacts = ", ".join(
        f"{kind} {sum(1 for c in shown if c.artifact == kind)}" for kind in ("pr", "issue", "idea")
    )
    summary.kv(
        [
            ("Candidates", f"{len(candidates)} ({len(shown)} shown, {len(hidden)} hidden)"),
            ("By category", categories),
            ("Default license mode", f"{verdict.get('mode', '?')}: {verdict.get('reason', '')}"),
            ("Artifacts", artifacts),
        ]
    )
    table = doc.section("Ranked candidates", priority=1)
    table.table(
        ["#", "Score", "Category", "Nutrient", "Mode", "Effort", "Risk", "Artifact", "Id"],
        (
            [i, c.score, c.category, c.title, c.license_mode, c.effort, c.risk, c.artifact, c.id]
            for i, c in enumerate(shown, start=1)
        ),
    )
    details = doc.section("Details", priority=2)
    for index, candidate in enumerate(shown, start=1):
        details.line(f"### {index}. {candidate.title}")
        details.line("")
        details.line(f"- **Id:** `{candidate.id}`")
        details.line(f"- **Prey:** {candidate.what}")
        details.line(f"- **Maw:** {candidate.maw_state}")
        if candidate.evidence:
            cited = ", ".join(
                f"[{e.path}]({e.url})" if e.url else e.path for e in candidate.evidence[:3]
            )
            details.line(f"- **Evidence:** {cited}")
        if explain and candidate.id in explain:
            details.line(f"- **Score:** {candidate.score} = {explain[candidate.id]}")
        if candidate.why_for_maw:
            details.line(f"- **Why for the maw:** {candidate.why_for_maw}")
        if candidate.how:
            details.line(f"- **How:** {candidate.how}")
        details.line("")
    if hidden:
        hidden_section = doc.section("Hidden", priority=4)
        hidden_section.bullets((f"{h.get('id')}: {h.get('reason')}" for h in hidden), max_items=40)
    return doc
