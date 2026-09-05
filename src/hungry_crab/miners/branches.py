"""Branches miner: what lives outside the default branch (ahead/behind, freshness, subjects)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..mdutil import MdDoc
from ..safety import is_suspicious
from .base import MineContext, MinerResult

STALE_DAYS = 180
MAX_BRANCHES = {"normal": 50, "deep": 200}
MAX_SUBJECTS = {"normal": 12, "deep": 40}
_FORMAT = "%(refname)%09%(objectname)%09%(committerdate:iso-strict)%09%(contents:subject)"


def _parse_refs(output: str) -> dict[str, dict[str, Any]]:
    """Merge local and remote-tracking refs by short name; the remote view wins."""
    branches: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        ref, sha, date_text, subject = parts
        if ref.endswith("/HEAD"):
            continue
        if ref.startswith("refs/remotes/origin/"):
            short, remote = ref[len("refs/remotes/origin/") :], True
        elif ref.startswith("refs/heads/"):
            short, remote = ref[len("refs/heads/") :], False
        else:
            continue
        existing = branches.get(short)
        if existing and existing["remote"] and not remote:
            continue
        try:
            date = datetime.fromisoformat(date_text.strip())
        except ValueError:
            continue
        branches[short] = {
            "name": short,
            "ref": ref,
            "sha": sha,
            "date": date,
            "subject": subject,
            "remote": remote,
        }
    return branches


class BranchesMiner:
    name = "branches"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "branches.json"
    md_file = "branches.md"

    def run(self, ctx: MineContext) -> MinerResult:
        git = ctx.git
        if git is None:
            data: dict[str, Any] = {
                "available": False,
                "reason": "not a git repository (or git is missing)",
            }
            return MinerResult(self.name, data, doc=self._doc(ctx, data))
        output = (
            git.try_run("for-each-ref", f"--format={_FORMAT}", "refs/heads", "refs/remotes/origin")
            or ""
        )
        branches = _parse_refs(output)
        default = git.default_branch()
        default_ref = (
            f"refs/remotes/origin/{default}"
            if any(b["ref"] == f"refs/remotes/origin/{default}" for b in branches.values())
            else f"refs/heads/{default}"
        )
        others = sorted(
            (b for b in branches.values() if b["name"] != default),
            key=lambda b: b["date"],
            reverse=True,
        )
        limit = MAX_BRANCHES["deep" if ctx.deep else "normal"]
        subjects_limit = MAX_SUBJECTS["deep" if ctx.deep else "normal"]
        now = ctx.now if ctx.now.tzinfo else ctx.now.replace(tzinfo=None)
        rows: list[dict[str, Any]] = []
        for branch in others[:limit]:
            counts = git.try_run(
                "rev-list", "--left-right", "--count", f"{default_ref}...{branch['ref']}"
            )
            behind = ahead = 0
            if counts:
                left, _, right = counts.strip().partition("\t")
                behind, ahead = int(left or 0), int(right or 0)
            merged = git.ok("merge-base", "--is-ancestor", branch["ref"], default_ref)
            branch_date = branch["date"]
            compare_now = (
                now
                if (now.tzinfo is None) == (branch_date.tzinfo is None)
                else (
                    now.replace(tzinfo=branch_date.tzinfo)
                    if now.tzinfo is None
                    else now.astimezone(branch_date.tzinfo)
                )
            )
            age_days = max(0, (compare_now - branch_date).days)
            subjects: list[str] = []
            if not merged and ahead:
                log = (
                    git.try_run(
                        "log",
                        f"{default_ref}..{branch['ref']}",
                        "--format=%s",
                        "--no-merges",
                        f"-n{subjects_limit}",
                    )
                    or ""
                )
                for subject in log.splitlines():
                    cleaned = subject.strip()
                    if cleaned and cleaned not in subjects:
                        subjects.append(
                            "[subject omitted: instruction-like]"
                            if is_suspicious(cleaned)
                            else cleaned[:100]
                        )
            rows.append(
                {
                    "name": branch["name"],
                    "sha": branch["sha"][:7],
                    "last_commit": branch_date.date().isoformat(),
                    "age_days": age_days,
                    "ahead": ahead,
                    "behind": behind,
                    "merged": merged,
                    "stale": age_days > STALE_DAYS,
                    "subjects": subjects,
                }
            )
        default_info = branches.get(default)
        data = {
            "available": True,
            "default_branch": default,
            "default_last_commit": default_info["date"].date().isoformat()
            if default_info
            else None,
            "total": len(others),
            "analyzed": len(rows),
            "merged": sum(1 for r in rows if r["merged"]),
            "stale": sum(1 for r in rows if r["stale"]),
            "active_unmerged": sum(1 for r in rows if not r["merged"] and not r["stale"]),
            "branches": rows,
        }
        return MinerResult(self.name, data, doc=self._doc(ctx, data))

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"Branches: {ctx.label}", source=ctx.source_line())
        summary = doc.section("Summary", priority=1)
        if not data.get("available"):
            summary.para(f"Branches not available: {data.get('reason')}.")
            return doc
        summary.kv(
            [
                (
                    "Default branch",
                    f"{data['default_branch']} (last commit {data['default_last_commit']})",
                ),
                ("Other branches", f"{data['total']} ({data['analyzed']} analysed)"),
                ("Merged into default", data["merged"]),
                ("Stale (older than 180 days)", data["stale"]),
                ("Active and unmerged", data["active_unmerged"]),
            ]
        )
        if data["branches"]:
            table = doc.section("Branches by freshness", priority=2)
            table.table(
                ["Branch", "Last commit", "Ahead", "Behind", "Merged", "Stale", "About"],
                (
                    [
                        b["name"],
                        b["last_commit"],
                        b["ahead"],
                        b["behind"],
                        b["merged"],
                        b["stale"],
                        "; ".join(b["subjects"][:2])[:120],
                    ]
                    for b in data["branches"]
                ),
                max_rows=25,
            )
            unmerged = [b for b in data["branches"] if not b["merged"] and b["subjects"]]
            if unmerged:
                about = doc.section("What unmerged branches are about", priority=3)
                for branch in unmerged[:10]:
                    about.line(
                        f"- **{branch['name']}** ({branch['ahead']} ahead, "
                        f"{branch['age_days']} days old):"
                    )
                    for subject in branch["subjects"][:5]:
                        about.line(f"  - {subject}")
                about.line("")
        return doc
