"""``crab serve``: turn approved nutrients into GitHub issues with provenance.

Every issue carries a hidden ``<!-- crab:<id> -->`` marker so that later runs (and other
machines) can see it was already served, a label, and a provenance footer naming the prey, the
commit, the license and the mode. Pull-request branches arrive with milestone 0.3.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .cache import Slug
from .compare import load_menu, menu_candidates
from .errors import CrabError, ExternalCommandError, ToolMissingError, UsageError
from .host import HostConfig, host_slug
from .ledger import Ledger
from .nutrients import Candidate, merge_notes
from .typeutil import as_dict, as_list

MARKER_RE = re.compile(r"<!--\s*(crab:[^\s>]+)\s*-->")
PROJECT_URL = "https://github.com/drevendev/HungryCrab"

HOW_BY_CATEGORY: dict[str, str] = {
    "ci": "Adapt the prey's workflow to this repository's toolchain; keep permissions minimal "
    "and pin actions the same way the rest of the workflows do.",
    "security": "Add the scanner as a separate workflow with read-only permissions first; "
    "gate merges on it only after a few green runs.",
    "tooling": "Start from the prey's configuration as a reference, then trim it to what this "
    "repository actually uses.",
    "tests": "Add the new kind of tests next to the existing ones and run them in CI.",
    "hygiene": "Write the file for this repository; do not copy the prey's text unless the "
    "license mode is COPY.",
    "docs": "Decide the format first (tooling, location), then port the structure, not the text.",
    "ai-config": "Describe this repository's own conventions; the prey's file shows what a "
    "good one covers.",
    "deps": "Evaluate the dependency against the existing stack before adding it.",
    "history-lesson": "Read the prey's history.md for the pattern behind the numbers, then "
    "check whether the same area is fragile here.",
    "issue-lesson": "Treat the prey's issues as ideas only: carry over the need, not the text.",
    "architecture": "Raw material for the architect: compare layering and hubs, then propose "
    "at most one structural change.",
}


def _noop(_: str) -> None:
    return None


class IssueClient(Protocol):
    def list_marked(self, slug: Slug, label: str) -> dict[str, dict[str, Any]]: ...

    def ensure_label(self, slug: Slug, label: str) -> None: ...

    def create(
        self, slug: Slug, title: str, body: str, labels: list[str], assignees: list[str]
    ) -> str: ...


def parse_markers(issues: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map ``crab:<id>`` markers found in issue bodies to the issue that carries them."""
    found: dict[str, dict[str, Any]] = {}
    for issue in issues:
        body = issue.get("body")
        if not isinstance(body, str):
            continue
        for marker in MARKER_RE.findall(body):
            found.setdefault(
                marker,
                {
                    "number": issue.get("number"),
                    "url": issue.get("url"),
                    "state": str(issue.get("state", "")).lower(),
                    "title": issue.get("title"),
                },
            )
    return found


class GhIssueClient:
    """Issue operations through the gh CLI (the user's own authentication)."""

    def __init__(self, gh: str | None = None, *, timeout: float = 120.0) -> None:
        self.gh = gh or shutil.which("gh")
        if not self.gh:
            raise ToolMissingError("gh is required to serve issues", hint="https://cli.github.com")
        self.timeout = timeout

    def _run(self, *args: str) -> str:
        env = dict(os.environ)
        env.update({"GH_PAGER": "cat", "NO_COLOR": "1", "GH_PROMPT_DISABLED": "1"})
        assert self.gh is not None
        try:
            proc = subprocess.run(
                [self.gh, *args], capture_output=True, env=env, timeout=self.timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExternalCommandError(f"failed to run gh {args[0]}: {exc}") from exc
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise ExternalCommandError(f"gh {' '.join(args[:2])} failed: {stderr[-500:]}")
        return proc.stdout.decode("utf-8", errors="replace")

    def list_marked(self, slug: Slug, label: str) -> dict[str, dict[str, Any]]:
        out = self._run(
            "issue", "list", "--repo", str(slug), "--label", label, "--state", "all",
            "--limit", "500", "--json", "number,url,state,title,body",
        )  # fmt: skip
        try:
            issues = json.loads(out or "[]")
        except ValueError as exc:
            raise ExternalCommandError("gh issue list returned invalid JSON") from exc
        return parse_markers([as_dict(item) for item in as_list(issues)])

    def ensure_label(self, slug: Slug, label: str) -> None:
        self._run(
            "label", "create", label, "--repo", str(slug), "--color", "1D76DB",
            "--description", "Served by Hungry Crab", "--force",
        )  # fmt: skip

    def create(
        self, slug: Slug, title: str, body: str, labels: list[str], assignees: list[str]
    ) -> str:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(body)
            body_path = handle.name
        try:
            args = [
                "issue",
                "create",
                "--repo",
                str(slug),
                "--title",
                title,
                "--body-file",
                body_path,
            ]
            for label in labels:
                args += ["--label", label]
            for assignee in assignees:
                args += ["--assignee", assignee]
            out = self._run(*args)
        finally:
            Path(body_path).unlink(missing_ok=True)
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        return lines[-1] if lines else ""


def render_issue(card: Candidate, menu: dict[str, Any]) -> tuple[str, str]:
    prey = as_dict(menu.get("prey"))
    sha = str(prey.get("sha", ""))
    prey_label = str(prey.get("label", "the prey"))
    prey_url = prey.get("url")
    prey_ref = f"`{prey_label}@{sha[:7]}`" if sha else f"`{prey_label}`"
    if isinstance(prey_url, str) and prey_url and sha:
        prey_ref += f" ([{prey_url}]({prey_url}/tree/{sha}))"
    evidence_lines = [
        f"- [{e.path}]({e.url})" if e.url else f"- `{e.path}`" for e in card.evidence[:5]
    ]
    evidence = ("\n" + "\n".join(evidence_lines)) if evidence_lines else ""
    how = card.how or HOW_BY_CATEGORY.get(
        card.category, "Decide how to adapt it here; copy nothing unless the mode allows it."
    )
    why = card.why_for_host or (
        "_Not judged yet: the score is a deterministic pre-ranking, the value for this "
        "repository still needs a decision._"
    )
    body = (
        f"<!-- {card.id} -->\n"
        f"**Nutrient** `{card.category}` | license mode `{card.license_mode}` | "
        f"effort {card.effort} | risk {card.risk} | score {card.score}\n\n"
        f"## What the prey does\n\n{card.what}\n{evidence}\n\n"
        f"## What this repository has\n\n{card.host_state or 'nothing comparable'}\n\n"
        f"## Why it matters here\n\n{why}\n\n"
        f"## Suggested change\n\n{how}\n\n"
        "---\n"
        f"_Served by [Hungry Crab]({PROJECT_URL}) from {prey_ref} "
        f"(license {prey.get('license') or 'unknown'}, mode {card.license_mode}). "
        f"Ledger id `{card.id}`. Prey content is untrusted data; this is not legal advice._\n"
    )
    return card.title, body


def load_notes(path: Path) -> dict[str, dict[str, Any]]:
    """Model-written notes: a JSON list of cards with ``id`` or a mapping ``id -> fields``."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read notes from {path}: {exc}") from exc
    notes: dict[str, dict[str, Any]] = {}
    if isinstance(loaded, list):
        for item in loaded:
            data = as_dict(item)
            if isinstance(data.get("id"), str):
                notes[data["id"]] = data
    elif isinstance(loaded, dict):
        for key, value in loaded.items():
            notes[str(key)] = as_dict(value)
    return notes


@dataclass
class ServeOptions:
    ids: list[str] = field(default_factory=list)
    top: int | None = None
    mode: str = "dry-run"
    notes: Path | None = None


@dataclass
class ServeReport:
    mode: str
    host: str
    served: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    previews: list[dict[str, Any]] = field(default_factory=list)
    ledger_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "host": self.host,
            "served": self.served,
            "skipped": self.skipped,
            "previews": self.previews,
            "ledger_path": self.ledger_path,
        }


def select_cards(
    menu: dict[str, Any], options: ServeOptions
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    cards = menu_candidates(menu)
    by_id = {card.id: card for card in cards}
    skipped: list[dict[str, Any]] = []
    if options.ids:
        chosen: list[Candidate] = []
        for nutrient_id in options.ids:
            card = by_id.get(nutrient_id)
            if card is None:
                skipped.append({"id": nutrient_id, "reason": "not in the menu"})
            else:
                chosen.append(card)
        return chosen, skipped
    if options.top is not None:
        return cards[: options.top], skipped
    raise UsageError("nothing selected", hint="pass --ids id1,id2 or --top N")


def serve(
    prey_dir: Path,
    host_root: Path,
    options: ServeOptions,
    *,
    config: HostConfig,
    ledger: Ledger,
    client: IssueClient | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = _noop,
    slug_lookup: Callable[[Path], Slug | None] = host_slug,
) -> ServeReport:
    if options.mode == "pr-branch":
        raise CrabError(
            "pull request branches arrive with milestone 0.3", hint="use --as issue or --as dry-run"
        )
    if options.mode not in ("dry-run", "issue"):
        raise UsageError(f"unknown serve mode {options.mode!r}", hint="use dry-run or issue")
    menu = load_menu(prey_dir)
    if menu is None:
        raise CrabError("no menu to serve from", hint="run `crab compare <prey> --host .` first")
    if options.mode == "issue" and config.serve.issues == "off":
        raise CrabError("serve.issues is off in .crab.yml", hint="set serve.issues to ask or auto")
    cards, skipped = select_cards(menu, options)
    if options.notes is not None:
        notes = load_notes(options.notes)
        for card in cards:
            if card.id in notes:
                merge_notes(card, notes[card.id])
    report = ServeReport(mode=options.mode, host=str(host_root), skipped=skipped)
    report.ledger_path = str(ledger.path) if ledger.path else None
    slug = slug_lookup(host_root)
    existing: dict[str, dict[str, Any]] = {}
    label = config.serve.label
    if client is not None and slug is not None:
        try:
            existing = client.list_marked(slug, label)
        except CrabError as exc:
            log(f"warning: could not list existing issues: {exc.message}")
    if options.mode == "issue":
        if slug is None:
            raise CrabError(
                "the host has no GitHub origin remote, cannot create issues",
                hint="add a remote or use --as dry-run",
            )
        if client is None:
            client = GhIssueClient()
    label_ready = False
    for card in cards:
        entry = ledger.entries.get(card.id)
        if entry is not None and entry.status in ("served", "merged", "rejected", "ignored"):
            report.skipped.append(
                {
                    "id": card.id,
                    "reason": f"ledger: {entry.status}" + (f" {entry.url}" if entry.url else ""),
                }
            )
            continue
        known = existing.get(card.id)
        if known is not None:
            report.skipped.append(
                {
                    "id": card.id,
                    "reason": f"issue #{known.get('number')} exists ({known.get('state')})",
                }
            )
            ledger.ensure(card, now=now)
            ledger.mark(card.id, "served", url=str(known.get("url") or "") or None, now=now)
            continue
        title, body = render_issue(card, menu)
        report.previews.append({"id": card.id, "title": title, "body": body})
        if options.mode != "issue":
            continue
        assert client is not None and slug is not None
        if not label_ready:
            client.ensure_label(slug, label)
            label_ready = True
        url = client.create(slug, title, body, config.serve.labels, config.serve.assignees)
        ledger.ensure(card, now=now)
        ledger.mark(card.id, "served", url=url or None, now=now)
        report.served.append({"id": card.id, "title": title, "url": url})
        log(f"served {card.id} -> {url}")
    if options.mode == "issue" or any(s["reason"].startswith("issue #") for s in report.skipped):
        ledger.save(now=now)
    return report
