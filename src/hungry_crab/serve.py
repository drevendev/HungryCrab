"""``crab serve``: turn approved nutrients into GitHub issues or guarded pull requests.

Every issue and pull request carries a hidden ``<!-- crab:<id> -->`` marker so later runs (and
other machines) can reconcile provider truth before creating another artifact. Pull-request mode
accepts only the strict clean-room implementation receipts produced by the isolated REIMPLEMENT
worker; it never infers publication membership from a dirty maw working tree.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TextIO, cast

from .cache import Slug
from .compare import load_menu, menu_candidates
from .errors import CrabError, ExternalCommandError, ToolMissingError, UsageError
from .fetch.git import GitRunner
from .ledger import Ledger
from .maw import MawConfig, maw_slug
from .nutrients import Candidate, merge_notes
from .pr_publication import (
    PreparedPullRequest,
    PullRequestPublication,
    load_cleanroom_implementation_receipt,
)
from .pr_serve import prepare_cleanroom_pull_request, publish_prepared_cleanroom_git_pull_request
from .pr_serving import serve_cleanroom_pull_requests
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

    def ensure_label(self, slug: Slug, label: str) -> bool:
        """True when the label exists afterwards. False means: serve without labels."""
        ...

    def create(
        self, slug: Slug, title: str, body: str, labels: list[str], assignees: list[str]
    ) -> str: ...

    def identity(self) -> str:
        """Who the issues will be filed as, for the log. Empty when it cannot be resolved."""
        ...


class PullRequestClient(Protocol):
    """The provider surface needed after the complete PR payload is prepared and scanned."""

    def list_marked_prs(self, slug: Slug) -> dict[str, dict[str, Any]]: ...

    def run_gh(self, *args: str) -> str: ...

    def identity(self) -> str: ...


def _opens_with(body: str, marker: str) -> bool:
    """A Hungry Crab artifact starts with its marker; a quote carries it further down."""
    match = MARKER_RE.match(body.lstrip())
    return match is not None and match.group(1) == marker


def parse_markers(issues: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map ``crab:<id>`` markers to the direct artifact that carries them.

    A marker can be in more than one issue because an HTML comment survives a quote. The artifact
    the crab filed is the one whose body opens with the marker; among several direct carriers, or
    failing any, the oldest wins. The answer does not depend on provider result order.
    """
    ranked: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
    for issue in issues:
        body = issue.get("body")
        if not isinstance(body, str):
            continue
        number = issue.get("number")
        age = number if isinstance(number, int) else sys.maxsize
        for marker in set(MARKER_RE.findall(body)):
            rank = (0 if _opens_with(body, marker) else 1, age)
            current = ranked.get(marker)
            if current is not None and current[0] <= rank:
                continue
            ranked[marker] = (
                rank,
                {
                    "number": number,
                    "url": issue.get("url"),
                    "state": str(issue.get("state", "")).lower(),
                    "title": issue.get("title"),
                },
            )
    return {marker: found for marker, (_, found) in ranked.items()}


def _json_documents(text: str) -> list[Any]:
    """``gh api --paginate`` prints one JSON document per page, back to back."""
    decoder = json.JSONDecoder()
    documents: list[Any] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        try:
            value, index = decoder.raw_decode(text, index)
        except ValueError as exc:
            raise ExternalCommandError("gh api returned invalid JSON") from exc
        documents.append(value)
    return documents


class GhIssueClient:
    """Issue and pull-request provider operations through the gh CLI."""

    def __init__(
        self, gh: str | None = None, *, timeout: float = 120.0, token_env: str = ""
    ) -> None:
        self.gh = gh or shutil.which("gh")
        if not self.gh:
            raise ToolMissingError("gh is required to serve issues", hint="https://cli.github.com")
        self.timeout = timeout
        self.token_env = token_env

    def _run(self, *args: str) -> str:
        env = dict(os.environ)
        env.update({"GH_PAGER": "cat", "NO_COLOR": "1", "GH_PROMPT_DISABLED": "1"})
        token = env.get(self.token_env, "").strip() if self.token_env else ""
        if token:
            env["GH_TOKEN"] = token
            env.pop("GITHUB_TOKEN", None)
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

    def run_gh(self, *args: str) -> str:
        """Expose the same authenticated gh runner to the guarded PR effect adapter."""
        return self._run(*args)

    def list_marked(self, slug: Slug, label: str) -> dict[str, dict[str, Any]]:
        """Markers in every issue of the repository, whatever its label, however many."""
        del label
        out = self._run(
            "api", "--paginate", f"repos/{slug}/issues?state=all&per_page=100&direction=asc"
        )
        issues: list[dict[str, Any]] = []
        for page in _json_documents(out):
            for item in as_list(page):
                data = as_dict(item)
                if "pull_request" in data:
                    continue
                issues.append(
                    {
                        "number": data.get("number"),
                        "url": data.get("html_url"),
                        "state": data.get("state"),
                        "title": data.get("title"),
                        "body": data.get("body"),
                    }
                )
        return parse_markers(issues)

    def list_marked_prs(self, slug: Slug) -> dict[str, dict[str, Any]]:
        """Markers in every pull request, across all pages and states."""
        out = self._run(
            "api", "--paginate", f"repos/{slug}/issues?state=all&per_page=100&direction=asc"
        )
        pull_requests: list[dict[str, Any]] = []
        for page in _json_documents(out):
            for item in as_list(page):
                data = as_dict(item)
                if "pull_request" not in data:
                    continue
                pull_requests.append(
                    {
                        "number": data.get("number"),
                        "url": data.get("html_url"),
                        "state": data.get("state"),
                        "title": data.get("title"),
                        "body": data.get("body"),
                    }
                )
        return parse_markers(pull_requests)

    def ensure_label(self, slug: Slug, label: str) -> bool:
        """Creating a label needs write access; filing an issue does not."""
        try:
            self._run(
                "label",
                "create",
                label,
                "--repo",
                str(slug),
                "--color",
                "1D76DB",
                "--description",
                "Served by Hungry Crab",
                "--force",
            )
        except CrabError:
            return False
        return True

    def _token(self) -> str:
        return os.environ.get(self.token_env, "").strip() if self.token_env else ""

    def identity(self) -> str:
        source = f" (${self.token_env})" if self._token() else ""
        try:
            login = self._run("api", "user", "-q", ".login").strip()
        except CrabError:
            return f"the app installation in ${self.token_env}" if source else ""
        return f"{login}{source}" if login else ""

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


def _maw_state(card: Candidate) -> str:
    state = card.maw_state.strip()
    if state.lower() in ("", "no", "none", "false"):
        return "nothing comparable"
    return state


def _license_trace(card: Candidate) -> str:
    lines = [
        f"- content origin: `{card.origin}`",
        f"- license mode: `{card.license_mode}`",
    ]
    if card.license_reason:
        lines.append(f"- origin cap: {card.license_reason}")
    return "\n".join(lines)


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
    why = card.why or (
        "_Not judged yet: the score is a deterministic pre-ranking, the value for this "
        "repository still needs a decision._"
    )
    body = (
        f"<!-- {card.id} -->\n"
        f"**Nutrient** `{card.category}` | license mode `{card.license_mode}` | "
        f"effort {card.effort} | risk {card.risk} | score {card.score}\n\n"
        f"## License trace\n\n{_license_trace(card)}\n\n"
        f"## What the prey does\n\n{card.what}\n{evidence}\n\n"
        f"## What this repository has\n\n{_maw_state(card)}\n\n"
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


def load_cleanroom_receipts(payload: str) -> dict[str, str]:
    """Parse one or more strict receipt JSON documents from the trusted caller stream.

    Documents may be separated by arbitrary whitespace. Each raw document is passed through the
    strict receipt parser independently, so duplicate object members and unknown fields still fail
    closed. The stream is transport only: it is never persisted as a second publication state.
    """
    decoder = json.JSONDecoder()
    receipts: dict[str, str] = {}
    index = 0
    while index < len(payload):
        while index < len(payload) and payload[index].isspace():
            index += 1
        if index >= len(payload):
            break
        start = index
        try:
            _, index = decoder.raw_decode(payload, index)
        except ValueError as exc:
            raise UsageError(
                "invalid clean-room receipt stream",
                hint="pipe one or more complete clean-room receipt JSON objects to stdin",
            ) from exc
        raw = payload[start:index]
        receipt = load_cleanroom_implementation_receipt(raw)
        if receipt.nutrient_id in receipts:
            raise UsageError(
                f"duplicate clean-room receipt for {receipt.nutrient_id}",
                hint="provide exactly one implementation receipt per selected nutrient",
            )
        receipts[receipt.nutrient_id] = raw
    if not receipts:
        raise CrabError(
            "milestone 0.3 pull-request serving requires a clean-room implementation receipt",
            hint=(
                "pipe one strict implementer receipt JSON object per selected REIMPLEMENT "
                "nutrient to stdin"
            ),
        )
    return receipts


@dataclass
class ServeOptions:
    ids: list[str] = field(default_factory=list)
    top: int | None = None
    mode: str = "dry-run"
    notes: Path | None = None


@dataclass
class ServeReport:
    mode: str
    maw: str
    served: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    previews: list[dict[str, Any]] = field(default_factory=list)
    ledger_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "maw": self.maw,
            "served": self.served,
            "skipped": self.skipped,
            "previews": self.previews,
            "ledger_path": self.ledger_path,
        }


def select_cards(
    menu: dict[str, Any], options: ServeOptions, ledger: Ledger | None = None
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    cards = menu_candidates(menu)
    by_id = {card.id: card for card in cards}
    skipped: list[dict[str, Any]] = []
    if options.ids:
        chosen: list[Candidate] = []
        for nutrient_id in options.ids:
            card = by_id.get(nutrient_id)
            if card is not None:
                chosen.append(card)
                continue
            entry = ledger.entries.get(nutrient_id) if ledger is not None else None
            if entry is not None and entry.status != "proposed":
                reason = f"ledger: {entry.status}" + (f" {entry.url}" if entry.url else "")
            else:
                reason = "not in the menu"
            skipped.append({"id": nutrient_id, "reason": reason})
        return chosen, skipped
    if options.top is not None:
        return cards[: options.top], skipped
    raise UsageError("nothing selected", hint="pass --ids id1,id2 or --top N")


def decode_receipt_stream(stream: TextIO) -> str:
    """The receipt stream as text, decoded as UTF-8 whatever the console believes.

    On Windows ``sys.stdin`` decodes with the console code page, so a UTF-8 receipt whose
    summary carries an em dash or an accented word arrives as mojibake with lone surrogates:
    the scan and the reconciliation pass, the branch is pushed, and the encode before
    ``gh pr create`` raises. The bytes are read raw and decoded once; a BOM is tolerated.
    """
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return str(stream.read())
    raw = bytes(buffer.read())
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UsageError(
            "clean-room receipt stream is not UTF-8",
            hint="write the receipt as UTF-8 and pipe its bytes unchanged",
        ) from exc


def _read_receipt_stream() -> dict[str, str]:
    if sys.stdin.isatty():
        return load_cleanroom_receipts("")
    try:
        payload = decode_receipt_stream(sys.stdin)
    except OSError:
        payload = ""
    return load_cleanroom_receipts(payload)


def _require_repository_root(maw_root: Path) -> None:
    """Pull-request publication stages files relative to the repository root.

    ``--maw packages/app`` reads ``src/x.py`` under ``packages/app`` and would push it as
    ``src/x.py`` at the root: a plausible pull request that changes the wrong file. The maw of
    a pull request must be the root of the repository the pull request goes into.
    """
    git = GitRunner(maw_root) if GitRunner.available() else None
    toplevel = git.toplevel() if git is not None and git.is_repo() else None
    if toplevel is None:
        raise CrabError("the maw is not a git repository; cannot publish a pull request")
    if toplevel.resolve() != maw_root.resolve():
        raise CrabError(
            "pull-request publication needs the maw to be the repository root",
            hint=f"pass --maw {toplevel}",
        )


def _serve_pull_requests(
    cards: list[Candidate],
    receipts: Mapping[str, str],
    *,
    menu: dict[str, Any],
    maw_root: Path,
    config: MawConfig,
    ledger: Ledger,
    client: PullRequestClient,
    explicit_selection: bool,
    now: datetime | None,
    log: Callable[[str], None],
    slug: Slug,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def prepare(card: Candidate, receipt_payload: str) -> PreparedPullRequest:
        title, body = render_issue(card, menu)
        return prepare_cleanroom_pull_request(card.id, title, body, receipt_payload, maw_root)

    def publish(
        card: Candidate, prepared: PreparedPullRequest, allow_create: bool
    ) -> PullRequestPublication | None:
        return publish_prepared_cleanroom_git_pull_request(
            card.id,
            prepared,
            maw_root,
            slug,
            list_marked_prs=lambda: client.list_marked_prs(slug),
            run_gh=client.run_gh,
            allow_create=allow_create,
        )

    result = serve_cleanroom_pull_requests(
        cards,
        receipts,
        config=config,
        ledger=ledger,
        explicit_selection=explicit_selection,
        preparer=prepare,
        publisher=publish,
        now=now,
    )
    served: list[dict[str, Any]] = []
    skipped = list(result.skipped)
    for item in result.served:
        if item.get("created"):
            served.append(item)
            log(f"served {item['id']} -> {item['url']}")
        else:
            skipped.append(
                {
                    "id": item["id"],
                    "reason": f"pull request exists {item['url']}; ledger reconciled",
                }
            )
            log(f"reconciled {item['id']} -> {item['url']}")
    return served, skipped


def serve(
    meal_dir: Path,
    maw_root: Path,
    options: ServeOptions,
    *,
    config: MawConfig,
    ledger: Ledger,
    client: IssueClient | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = _noop,
    slug_lookup: Callable[[Path], Slug | None] = maw_slug,
    receipt_payloads: Mapping[str, str] | None = None,
) -> ServeReport:
    if options.mode not in ("dry-run", "issue", "pr-branch"):
        raise UsageError(
            f"unknown serve mode {options.mode!r}", hint="use dry-run, issue, or pr-branch"
        )
    menu = load_menu(meal_dir)
    if menu is None:
        raise CrabError("no menu to serve from", hint="run `crab compare <prey> --maw .` first")
    if options.mode == "issue" and config.serve.issues == "off":
        raise CrabError("serve.issues is off in .crab.yml", hint="set serve.issues to ask or auto")
    cards, skipped = select_cards(menu, options, ledger)
    if options.notes is not None:
        notes = load_notes(options.notes)
        for card in cards:
            if card.id in notes:
                merge_notes(card, notes[card.id])
    report = ServeReport(mode=options.mode, maw=str(maw_root), skipped=skipped)
    report.ledger_path = str(ledger.path) if ledger.path else None
    slug = slug_lookup(maw_root)

    if options.mode == "pr-branch":
        receipts = (
            dict(receipt_payloads) if receipt_payloads is not None else _read_receipt_stream()
        )
        if slug is None:
            raise CrabError(
                "the maw has no GitHub origin remote, cannot create pull requests",
                hint="add a remote or use --as dry-run",
            )
        _require_repository_root(maw_root)
        pr_client = (
            cast(PullRequestClient, client)
            if client is not None
            else GhIssueClient(token_env=config.serve.token_env)
        )
        who = pr_client.identity()
        target = f"serving pull requests into {slug}"
        log(f"{target} as {who}" if who else target)
        served, pr_skipped = _serve_pull_requests(
            cards,
            receipts,
            menu=menu,
            maw_root=maw_root,
            config=config,
            ledger=ledger,
            client=pr_client,
            explicit_selection=bool(options.ids),
            now=now,
            log=log,
            slug=slug,
        )
        report.served.extend(served)
        report.skipped.extend(pr_skipped)
        return report

    existing: dict[str, dict[str, Any]] = {}
    label = config.serve.label
    if client is not None and slug is not None:
        try:
            existing = client.list_marked(slug, label)
        except CrabError as exc:
            if options.mode == "issue":
                # The listing is the only deduplication on a repository without a ledger, or
                # from a second machine. Filing without it is filing duplicates.
                raise CrabError(
                    "could not list the existing issues; refusing to file what may be duplicates",
                    hint=exc.message,
                ) from exc
            log(f"warning: could not list existing issues: {exc.message}")
    if options.mode == "issue":
        if slug is None:
            raise CrabError(
                "the maw has no GitHub origin remote, cannot create issues",
                hint="add a remote or use --as dry-run",
            )
        if client is None:
            client = GhIssueClient(token_env=config.serve.token_env)
        who = client.identity()
        log(f"serving into {slug} as {who}" if who else f"serving into {slug}")
    label_ready = False
    labels = list(config.serve.labels)
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
            if not client.ensure_label(slug, label):
                labels = []
                log(
                    f"warning: cannot create the {label!r} label in {slug} (that needs write "
                    "access); serving without labels. Deduplication is unaffected: it reads the "
                    "crab:<id> marker in the issue body."
                )
            label_ready = True
        url = client.create(slug, title, body, labels, config.serve.assignees)
        ledger.ensure(card, now=now)
        ledger.mark(card.id, "served", url=url or None, now=now)
        report.served.append({"id": card.id, "title": title, "url": url})
        log(f"served {card.id} -> {url}")
    if options.mode == "issue" or any(s["reason"].startswith("issue #") for s in report.skipped):
        ledger.save(now=now)
    return report
