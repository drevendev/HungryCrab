"""History miner: what the commit log says about how the project is really built.

Hotspots, fix ratio, reverts, co-change coupling, cadence, bus factor, tags and release cadence,
conventional-commit discipline. Read-only ``git log`` on the current branch; merges are counted
but their numstat is not attributed (``--no-renames`` keeps churn attribution simple).
"""

from __future__ import annotations

import itertools
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..mdutil import MdDoc
from ..safety import is_suspicious
from .base import MineContext, MinerResult

_FORMAT = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%P%x1f%s%x1f%b%x1f"
_FIX_RE = re.compile(
    r"\b(?:fix(?:es|ed|ing)?|bug(?:fix)?|hotfix|regression|crash(?:es)?|broken|resolve[sd]?)\b",
    re.IGNORECASE,
)
_CONVENTIONAL_RE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?!?: \S")
_CONVENTIONAL_TYPES = frozenset(
    {"feat", "fix", "docs", "style", "refactor", "perf", "test", "tests", "build", "ci", "chore",
     "revert", "deps", "release", "security", "improvement", "types"}
)  # fmt: skip
_PR_STYLE_RE = re.compile(r"\(#\d+\)\s*$")
_SECURITY_RE = re.compile(
    r"\b(?:security|cve-\d{4}-\d+|vuln(?:erabilit(?:y|ies))?|xss|csrf|ssrf|injection|"
    r"exploit|sanitiz(?:e|ation)|(?:d|dd)os\b|path traversal|rce\b)",
    re.IGNORECASE,
)
_SEMVER_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")

MAX_COMMITS = {"normal": 20_000, "deep": 100_000}
MAX_COUPLING_FILES = 15
MAX_PAIR_INCREMENTS = 2_000_000


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    date: datetime
    parents: int
    subject: str
    is_revert: bool
    files: list[tuple[str, int | None, int | None]] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        return self.parents > 1

    @property
    def lines(self) -> int:
        return sum((a or 0) + (d or 0) for _, a, d in self.files)


def _parse_date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed


def parse_log(output: str) -> list[Commit]:
    commits: list[Commit] = []
    for record in output.split("\x1e")[1:]:
        fields = record.split("\x1f")
        if len(fields) < 9:
            continue
        sha, author, email, date_text, _committer_date, parents, subject = fields[:7]
        body = "\x1f".join(fields[7:-1])
        rest = fields[-1]
        date = _parse_date(date_text)
        if date is None:
            continue
        files: list[tuple[str, int | None, int | None]] = []
        for line in rest.strip().splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added = int(parts[0]) if parts[0].isdigit() else None
            deleted = int(parts[1]) if parts[1].isdigit() else None
            files.append((parts[2], added, deleted))
        is_revert = subject.startswith("Revert ") or "This reverts commit" in body
        commits.append(
            Commit(
                sha=sha,
                author=author.strip(),
                email=email.strip().lower(),
                date=date,
                parents=len(parents.split()),
                subject=subject.strip(),
                is_revert=is_revert,
                files=files,
            )
        )
    return commits


def is_conventional(subject: str) -> bool:
    match = _CONVENTIONAL_RE.match(subject)
    return bool(match and match.group("type") in _CONVENTIONAL_TYPES)


def is_fix(subject: str) -> bool:
    return bool(_FIX_RE.search(subject))


def bus_factor(counts: Counter[str]) -> int:
    total = sum(counts.values())
    if not total:
        return 0
    running = 0
    for index, (_, count) in enumerate(counts.most_common(), start=1):
        running += count
        if running * 2 >= total:
            return index
    return len(counts)


def _safe_subject(subject: str) -> str:
    return "[subject omitted: instruction-like]" if is_suspicious(subject) else subject[:100]


def analyse(commits: list[Commit], *, now: datetime, noise: set[str]) -> dict[str, Any]:
    if not commits:
        return {"available": True, "commits": 0}
    newest = commits[0].date
    oldest = commits[-1].date
    non_merge = [c for c in commits if not c.is_merge]

    authors: Counter[str] = Counter()
    names: dict[str, str] = {}
    for commit in commits:
        key = commit.email or commit.author.lower()
        authors[key] += 1
        names.setdefault(key, commit.author)
    top_authors = [
        {"name": names[key], "commits": count, "share": round(count / len(commits), 2)}
        for key, count in authors.most_common(10)
    ]

    months: Counter[str] = Counter()
    for commit in commits:
        months[commit.date.strftime("%Y-%m")] += 1
    last_year_keys = []
    cursor = newest.replace(day=1)
    for _ in range(12):
        last_year_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    per_month = {key: months.get(key, 0) for key in reversed(last_year_keys)}
    now_aware = now if now.tzinfo else now.replace(tzinfo=newest.tzinfo)
    recent_90 = sum(1 for c in commits if (now_aware - c.date).days <= 90)
    recent_365 = sum(1 for c in commits if (now_aware - c.date).days <= 365)

    per_file: dict[str, dict[str, int]] = defaultdict(
        lambda: {"commits": 0, "added": 0, "deleted": 0, "fixes": 0}
    )
    for commit in non_merge:
        fix = is_fix(commit.subject)
        for path, added, deleted in commit.files:
            if path in noise:
                continue
            entry = per_file[path]
            entry["commits"] += 1
            entry["added"] += added or 0
            entry["deleted"] += deleted or 0
            entry["fixes"] += 1 if fix else 0
    hotspots = sorted(
        per_file.items(), key=lambda kv: (-kv[1]["commits"], -(kv[1]["added"] + kv[1]["deleted"]))
    )[:20]
    fix_prone = sorted(
        ((path, s) for path, s in per_file.items() if s["commits"] >= 3 and s["fixes"]),
        key=lambda kv: (-(kv[1]["fixes"] / kv[1]["commits"]), -kv[1]["commits"]),
    )[:15]

    pairs: Counter[tuple[str, str]] = Counter()
    increments = 0
    for commit in non_merge:
        paths = sorted(p for p, _, _ in commit.files if p not in noise)
        if not 2 <= len(paths) <= MAX_COUPLING_FILES:
            continue
        for i, first in enumerate(paths):
            for second in paths[i + 1 :]:
                pairs[(first, second)] += 1
                increments += 1
        if increments > MAX_PAIR_INCREMENTS:
            break
    coupling = [
        {
            "a": a,
            "b": b,
            "count": count,
            "share": round(count / max(1, min(per_file[a]["commits"], per_file[b]["commits"])), 2),
        }
        for (a, b), count in pairs.most_common(40)
        if count >= 2
    ][:15]

    fixes = [c for c in non_merge if is_fix(c.subject)]
    reverts = [c for c in commits if c.is_revert]
    conventional = sum(1 for c in non_merge if is_conventional(c.subject))
    pr_style = sum(
        1
        for c in commits
        if _PR_STYLE_RE.search(c.subject) or c.subject.startswith("Merge pull request")
    )
    security = [c for c in commits if _SECURITY_RE.search(c.subject)]
    largest = sorted(non_merge, key=lambda c: (-len(c.files), -c.lines))[:5]

    return {
        "available": True,
        "commits": len(commits),
        "merges": len(commits) - len(non_merge),
        "first_commit": oldest.isoformat(),
        "last_commit": newest.isoformat(),
        "age_days": (newest - oldest).days,
        "authors": len(authors),
        "bus_factor": bus_factor(authors),
        "top_authors": top_authors,
        "commits_per_month_last_year": per_month,
        "active_months": len(months),
        "commits_last_90d": recent_90,
        "commits_last_365d": recent_365,
        "conventional_commits_ratio": round(conventional / len(non_merge), 2) if non_merge else 0.0,
        "pr_style_ratio": round(pr_style / len(commits), 2),
        "fix_commits": len(fixes),
        "fix_ratio": round(len(fixes) / len(non_merge), 2) if non_merge else 0.0,
        "reverts": [
            {
                "sha": c.sha[:7],
                "date": c.date.date().isoformat(),
                "subject": _safe_subject(c.subject),
            }
            for c in reverts[:10]
        ],
        "revert_count": len(reverts),
        "hotspots": [
            {"path": p, **s, "fix_ratio": round(s["fixes"] / s["commits"], 2)} for p, s in hotspots
        ],
        "fix_prone": [
            {"path": p, **s, "fix_ratio": round(s["fixes"] / s["commits"], 2)} for p, s in fix_prone
        ],
        "coupling": coupling,
        "largest_commits": [
            {
                "sha": c.sha[:7],
                "date": c.date.date().isoformat(),
                "files": len(c.files),
                "lines": c.lines,
                "subject": _safe_subject(c.subject),
            }
            for c in largest
        ],
        "security_commits": [
            {
                "sha": c.sha[:7],
                "date": c.date.date().isoformat(),
                "subject": _safe_subject(c.subject),
            }
            for c in security[:10]
        ],
        "security_commit_count": len(security),
        "files_touched": len(per_file),
    }


def parse_tags(output: str) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, date_text = parts[0], parts[1]
        date = _parse_date(date_text)
        tags.append(
            {
                "name": name,
                "date": date.isoformat() if date else None,
                "semver": bool(_SEMVER_TAG_RE.match(name)),
                "annotated": len(parts) > 2 and parts[2] == "tag",
            }
        )
    return tags


def analyse_tags(tags: list[dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    semver = [t for t in tags if t["semver"] and t["date"]]
    dates = [datetime.fromisoformat(t["date"]) for t in semver[:30]]
    gaps = [(a - b).days for a, b in itertools.pairwise(dates) if (a - b).days >= 0]
    now_aware = now if now.tzinfo or not dates else now.replace(tzinfo=dates[0].tzinfo)
    last_year = sum(1 for d in dates if (now_aware - d).days <= 365) if dates else 0
    return {
        "count": len(tags),
        "semver_count": len(semver),
        "semver_ratio": round(len(semver) / len(tags), 2) if tags else None,
        "latest": tags[0]["name"] if tags else None,
        "latest_date": tags[0]["date"] if tags else None,
        "annotated_ratio": round(sum(1 for t in tags if t["annotated"]) / len(tags), 2)
        if tags
        else None,
        "release_cadence_days": round(statistics.median(gaps)) if gaps else None,
        "releases_last_year": last_year,
        "recent": [t["name"] for t in tags[:10]],
    }


class HistoryMiner:
    name = "history"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "history.json"
    md_file = "history.md"

    def run(self, ctx: MineContext) -> MinerResult:
        git = ctx.git
        if git is None:
            reason = "not a git repository (or git is missing)"
            data = {"available": False, "reason": reason}
            return MinerResult(self.name, data, doc=self._unavailable(ctx, reason))
        limit = MAX_COMMITS["deep" if ctx.deep else "normal"]
        output = git.run(
            "log",
            f"--format={_FORMAT}",
            "--numstat",
            "--no-renames",
            "--date=iso-strict",
            f"-n{limit}",
            "HEAD",
            timeout=900,
        )
        commits = parse_log(output)
        noise = {f.path for f in ctx.files() if f.vendored or f.generated or f.lockfile}
        data = analyse(commits, now=ctx.now, noise=noise)
        tags_output = (
            git.try_run(
                "tag",
                "--sort=-creatordate",
                "--format=%(refname:short)%09%(creatordate:iso-strict)%09%(objecttype)",
            )
            or ""
        )
        data["tags"] = analyse_tags(parse_tags(tags_output), now=ctx.now)
        data["shallow"] = ctx.shallow
        data["truncated"] = len(commits) >= limit
        warnings = []
        if ctx.shallow:
            warnings.append("shallow clone: history metrics cover only the fetched commits")
        return MinerResult(self.name, data, doc=self._doc(ctx, data), warnings=warnings)

    def _unavailable(self, ctx: MineContext, reason: str) -> MdDoc:
        doc = MdDoc(f"History: {ctx.label}", source=ctx.source_line())
        doc.section("Summary", priority=1).para(f"History not available: {reason}.")
        return doc

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"History: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        if not data.get("commits"):
            summary.para("No commits on the current branch.")
            return doc
        tags = data["tags"]
        semver_text = tags["semver_ratio"] if tags["semver_ratio"] is not None else "n/a"
        tags_text = f"{tags['count']} (semver {semver_text}), latest {tags['latest'] or 'none'}"
        cadence_text = "n/a"
        if tags["release_cadence_days"] is not None:
            cadence_text = (
                f"median {tags['release_cadence_days']} days, "
                f"{tags['releases_last_year']} in the last year"
            )
        summary.kv(
            [
                (
                    "Commits (current branch)",
                    f"{data['commits']}"
                    + (" (shallow clone)" if data["shallow"] else "")
                    + (" (truncated)" if data["truncated"] else ""),
                ),
                (
                    "First / last commit",
                    (
                        f"{data['first_commit'][:10]} / {data['last_commit'][:10]} "
                        f"({data['age_days']} days)"
                    ),
                ),
                ("Authors / bus factor", f"{data['authors']} / {data['bus_factor']}"),
                (
                    "Commits last 90 / 365 days",
                    f"{data['commits_last_90d']} / {data['commits_last_365d']}",
                ),
                ("Merge commits", data["merges"]),
                ("Conventional commits", f"{data['conventional_commits_ratio'] * 100:.0f}%"),
                ("PR-style subjects", f"{data['pr_style_ratio'] * 100:.0f}%"),
                ("Fix commits", f"{data['fix_commits']} ({data['fix_ratio'] * 100:.0f}%)"),
                ("Reverts", data["revert_count"]),
                ("Security-related commits", data["security_commit_count"]),
                ("Tags", tags_text),
                ("Release cadence", cadence_text),
            ]
        )
        cadence = doc.section("Cadence (commits per month, last 12 months of activity)", priority=3)
        cadence.line(", ".join(f"{k}: {v}" for k, v in data["commits_per_month_last_year"].items()))
        cadence.line("")
        hotspots = doc.section("Hotspots (most changed files)", priority=1)
        hotspots.table(
            ["File", "Commits", "Fixes", "Fix ratio", "Churn"],
            (
                [h["path"], h["commits"], h["fixes"], h["fix_ratio"], h["added"] + h["deleted"]]
                for h in data["hotspots"]
            ),
            max_rows=20,
        )
        fix_prone = doc.section("Fix-prone files (3+ commits, by fix ratio)", priority=2)
        fix_prone.table(
            ["File", "Commits", "Fixes", "Fix ratio"],
            ([h["path"], h["commits"], h["fixes"], h["fix_ratio"]] for h in data["fix_prone"]),
            max_rows=15,
        )
        coupling = doc.section("Co-change coupling (files that change together)", priority=2)
        coupling.table(
            ["File A", "File B", "Together", "Share"],
            ([c["a"], c["b"], c["count"], c["share"]] for c in data["coupling"]),
            max_rows=15,
        )
        if data["reverts"]:
            reverts = doc.section("Reverts", priority=3)
            reverts.table(
                ["SHA", "Date", "Subject"],
                ([r["sha"], r["date"], r["subject"]] for r in data["reverts"]),
            )
        if data["security_commits"]:
            security = doc.section("Security-related commits", priority=2)
            security.table(
                ["SHA", "Date", "Subject"],
                ([s["sha"], s["date"], s["subject"]] for s in data["security_commits"]),
            )
        largest = doc.section("Largest commits", priority=4)
        largest.table(
            ["SHA", "Date", "Files", "Lines", "Subject"],
            (
                [c["sha"], c["date"], c["files"], c["lines"], c["subject"]]
                for c in data["largest_commits"]
            ),
        )
        authors = doc.section("Top authors", priority=4)
        authors.table(
            ["Author", "Commits", "Share"],
            ([a["name"], a["commits"], a["share"]] for a in data["top_authors"]),
        )
        if tags["recent"]:
            recent = doc.section("Recent tags", priority=4)
            recent.line(", ".join(tags["recent"]))
            recent.line("")
        return doc
