"""Self-contained, inert HTML views of existing digest and meal artifacts."""

from __future__ import annotations

import base64
import hashlib
import html
import os
import stat
import uuid
from collections.abc import Iterable
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .artifact_read import MAX_PAGE_BYTES, markdown_pages, read_bytes, read_object
from .digest import SCHEMA as DIGEST_SCHEMA
from .digest import incomplete_miners
from .digest_integrity import digest_integrity_errors
from .errors import CrabError, UsageError
from .typeutil import as_dict, as_list

MAX_REPORT_BYTES = 32 * 1024 * 1024
STYLE = files("hungry_crab").joinpath("data/report.css").read_text(encoding="utf-8").strip()


def text(value: object) -> str:
    return html.escape(str(value) if value is not None else "Unknown", quote=True)


def link(label: object, url: object) -> str:
    if isinstance(url, str) and not any(char.isspace() or ord(char) < 32 for char in url):
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme in {"https", "http"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            ):
                return (
                    f'<a href="{text(url)}"'
                    f' target="_blank" rel="noopener noreferrer">{text(label)}</a>'
                )
        except ValueError:
            pass
    return text(label)


def _table(headings: list[str], rows: Iterable[list[object]]) -> str:
    head = "".join(f'<th scope="col">{text(item)}</th>' for item in headings)
    body = "".join(
        "<tr>" + "".join(f"<td>{text(item)}</td>" for item in row) + "</tr>" for row in rows
    )
    return (
        f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _stats(items: list[tuple[object, str]]) -> str:
    return (
        '<div class="stats">'
        + "".join(
            f'<div class="stat"><strong>{text(value)}</strong><span>{text(label)}</span></div>'
            for value, label in items
        )
        + "</div>"
    )


def _notice(message: str) -> str:
    return f'<div class="notice">{text(message)}</div>'


def _identity(label: str, repo: dict[str, Any]) -> str:
    revision = repo.get("sha") or "Unknown revision"
    return (
        f"<p><strong>{text(label)}</strong> {text(repo.get('label', 'Unknown repository'))}"
        f" · <code>{text(revision)}</code></p>"
    )


def _document(title: str, subtitle: str, content: str) -> str:
    style_hash = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode("ascii")
    policy = (
        f"default-src 'none'; style-src 'sha256-{style_hash}'; base-uri 'none'; form-action 'none'"
    )
    result = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{text(policy)}">'
        f"<title>{text(title)} · Hungry Crab</title><style>{STYLE}</style></head><body>"
        '<header><div class="eyebrow">Hungry Crab / Repository intelligence</div>'
        f'<h1>{text(title)}</h1><div class="sub">{text(subtitle)}</div></header>'
        f"<main>{content}</main>"
        f"<footer>Hungry Crab {text(__version__)} · Offline snapshot. "
        "Repository content is untrusted data. Licence modes describe engine decisions, "
        "not legal clearance. No code was executed to render this report."
        "</footer></body></html>\n"
    )
    if len(result.encode("utf-8")) > MAX_REPORT_BYTES:
        raise CrabError("HTML report exceeds its 32 MiB output limit; no file was written")
    return result


def _digest(directory: Path, manifest: dict[str, Any]) -> str:
    pages = markdown_pages(directory, manifest)
    errors = digest_integrity_errors(directory, manifest)
    failed = incomplete_miners(manifest)
    coverage = as_dict(manifest.get("coverage"))
    prey = as_dict(manifest.get("prey"))
    content = _identity("Repository", prey)
    if errors or failed or coverage.get("healthy") is not True:
        content += _notice(
            "Incomplete evidence. Missing facts do not establish that a capability is absent."
        )
        content += (
            "<ul>" + "".join(f"<li>{text(item)}</li>" for item in [*errors, *failed]) + "</ul>"
        )
    else:
        content += '<p class="muted">All recorded miners completed; tree visibility is healthy.</p>'
    content += _stats(
        [
            (len(as_list(manifest.get("miners"))), "miners"),
            (len(pages), "Markdown pages"),
            (manifest.get("markdown_tokens_est", "Unknown"), "Markdown tokens"),
            ("Healthy" if coverage.get("healthy") is True else "Incomplete", "tree visibility"),
        ]
    )
    content += (
        '<nav aria-label="Report sections"><a href="#miners">Miner health</a>'
        '<a href="#pages">Evidence pages</a></nav>'
    )
    content += '<section id="miners"><h2>Miner health</h2><div class="panel">'
    content += (
        _table(
            ["Miner", "Status", "Artifacts", "Details"],
            (
                [
                    row.get("name"),
                    row.get("status", "ok" if row.get("ok") else "failed"),
                    len(as_list(row.get("files"))),
                    row.get("error") or "",
                ]
                for row in as_list(manifest.get("miners"))
                if isinstance(row, dict)
            ),
        )
        + '</div></section><section id="pages"><h2>Evidence pages</h2><div class="panel">'
    )
    if not pages:
        content += "<p>No Markdown pages were retained by this digest policy.</p>"
    total = 0
    for index, name in enumerate(pages):
        remaining = MAX_REPORT_BYTES // 2 - total
        if remaining <= 0:
            content += _notice(
                "Report read budget exhausted; omitted pages: " + ", ".join(pages[index:])
            )
            break
        read_limit = min(MAX_PAGE_BYTES, remaining)
        try:
            raw = read_bytes(directory / name, read_limit)
            total += len(raw)
            content += (
                f"<details><summary>{text(name)}</summary>"
                f"<pre>{text(raw.decode('utf-8', errors='replace'))}</pre></details>"
            )
        except CrabError as exc:
            # A rejected read may have consumed its entire limit plus a sentinel byte.
            # Reserve that amount even for other read errors; failures cannot reset the budget.
            total += read_limit + 1
            content += _notice(f"{name}: {exc.message}")
    content += "</div></section>"
    return _document(
        prey.get("label") or "Repository digest",
        "Digest / Recorded facts, coverage and source evidence",
        content,
    )


def _card(card: dict[str, Any], index: int, review: dict[str, Any] | None = None) -> str:
    evidence = [row for row in as_list(card.get("evidence")) if isinstance(row, dict)]
    trace = as_dict(card.get("trace"))
    parts = [
        (
            f'<article class="card"><div class="muted">Candidate {index} · '
            f"{text(as_dict(review).get('review', 'structural-seed'))} · "
            f"Selection: {text(as_dict(review).get('selection', 'unknown'))}</div>"
        ),
        f"<h3>{text(card.get('title'))}</h3>",
    ]
    for label in (
        card.get("category"),
        card.get("license_mode"),
        f"Effort {card.get('effort', '?')}",
        f"Risk {card.get('risk', '?')}",
    ):
        parts.append(f'<span class="badge">{text(label)}</span>')
    parts.append(
        f"<div><code>{text(card.get('id'))}</code> · Score {text(card.get('score'))}</div>"
    )
    for heading, key in (
        ("What the source does", "what"),
        ("What this maw has", "maw_state"),
        ("Why it matters", "why"),
        ("Suggested change", "how"),
    ):
        value = card.get(key)
        if value:
            parts.append(f"<p><strong>{heading}</strong><br>{text(value)}</p>")
    parts.append("<details><summary>Evidence and provenance</summary><ul>")
    for item in evidence:
        label = str(item.get("path") or item.get("url") or "Evidence")
        if item.get("line"):
            label += f":{item['line']}"
        parts.append(f"<li>{link(label, item.get('url'))}</li>")
    parts.append(
        f"<li>Origin: {text(card.get('origin'))}."
        f" Source: {text(trace.get('prey'))}@{text(trace.get('sha'))}</li>"
    )
    for source in as_list(trace.get("sources")):
        if isinstance(source, dict):
            prey = as_dict(source.get("prey"))
            parts.append(
                f"<li>Corroborating source: {text(prey.get('label'))}@{text(prey.get('sha'))}"
                f"; {text(as_dict(source.get('card')).get('license_mode'))}</li>"
            )
    parts.append("</ul></details></article>")
    return "".join(parts)


def _menu(menu: dict[str, Any], directory: Path | None = None) -> str:
    from .meal_review import project_menu

    review = project_menu(menu, directory)
    raw = menu.get("candidates")
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise CrabError("menu has an invalid candidate list")
    ids = [row.get("id") for row in raw]
    if any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
        raise CrabError("menu has invalid or duplicate nutrient IDs")
    prey, maw = as_dict(menu.get("prey")), as_dict(menu.get("maw"))
    hidden = as_list(menu.get("hidden"))
    content = _identity("Prey", prey) + _identity("Maw", maw)
    content += _notice(
        "These are candidates for review. A score is a ranking signal; "
        "verify the maw gap, evidence and licence mode before carrying anything over."
    )
    content += _stats(
        [
            (len(raw), "candidate nutrients"),
            (len(hidden), "hidden / already decided"),
            (len({str(row.get("category")) for row in raw}), "categories"),
            (menu.get("mode", "normal"), "maw policy"),
        ]
    )
    content += "<section><h2>Evidence and completion</h2>"
    content += _table(
        ["Measure", "Count"], [[key, value] for key, value in review["counts"].items()]
    )
    content += _notice(review["phase_evidence"])
    content += _notice(f"{review['evidence_scope']}; as of {review['as_of']}")
    for exploration in review["explorations"]:
        content += f"<p>Explore report: <code>{text(exploration['report_path'])}</code></p>"
        content += _table(
            [
                "Scope",
                "Complete",
                "Pending facets",
                "Unresolved hypotheses",
                "Unreviewed candidates",
            ],
            [
                [
                    exploration[key]
                    for key in (
                        "scope",
                        "exploration_complete",
                        "pending_facets",
                        "unresolved_hypotheses",
                        "unreviewed_candidates",
                    )
                ]
            ],
        )
        content += _table(
            ["Hypothesis", "Disposition", "Reviewed", "Reason"],
            [
                [entry.get(key) for key in ("hypothesis", "disposition", "reviewed", "reason")]
                for entry in exploration["decisions"]
            ],
        )
    for phase, observations in review["phases"].items():
        content += _table(
            [
                phase,
                "Repository",
                "Artifact/head",
                "Method",
                "Workflow/job",
                "Runner",
                "Executed steps",
            ],
            [
                [
                    entry.get("state"),
                    entry.get("repository"),
                    entry.get("artifact"),
                    entry.get("method"),
                    f"{entry.get('workflow', 'Unknown')} / {entry.get('job', 'Unknown')}",
                    entry.get("runner"),
                    entry.get("executed_steps"),
                ]
                for entry in observations
            ]
            or [["Unknown / not recorded", None, None, None, None, None, None]],
        )
    for diagnostic in review["diagnostics"]:
        content += _notice(diagnostic)
    content += "</section>"
    verdict = as_dict(menu.get("verdict"))
    if verdict:
        content += (
            f'<div class="panel"><strong>Licence decision: {text(verdict.get("mode"))}</strong>'
            f"<p>{text(verdict.get('reason'))}</p></div>"
        )
    if not raw:
        content += (
            "<h2>No new candidates</h2><p>No new recommendation was produced for this snapshot. "
            "Check hidden decisions and evidence coverage before treating this "
            "as proof of no gap.</p>"
        )
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(raw, 1):
        groups.setdefault(str(row.get("category", "other")), []).append((index, row))
    content += (
        '<nav aria-label="Nutrient categories">'
        + "".join(
            f'<a href="#category-{number}">{text(category)} ({len(rows)})</a>'
            for number, (category, rows) in enumerate(groups.items())
        )
        + "</nav>"
    )
    for number, (category, rows) in enumerate(groups.items()):
        content += f'<section id="category-{number}"><h2>{text(category)}</h2><div class="cards">'
        content += (
            "".join(_card(row, index, review["cards"].get(row["id"])) for index, row in rows)
            + "</div></section>"
        )
    if hidden:
        content += '<section><h2>Hidden decisions</h2><div class="panel">'
        content += (
            _table(
                ["Nutrient", "Reason"],
                ([row.get("id"), row.get("reason")] for row in hidden if isinstance(row, dict)),
            )
            + "</div></section>"
        )
    return _document(
        "What is worth carrying over?",
        "Meal / Ranked candidates with licence modes and pinned evidence",
        content,
    )


def render_report(source: Path) -> str:
    source = source.expanduser().resolve()
    if source.is_dir():
        source = source / ("menu.json" if (source / "menu.json").exists() else "manifest.json")
    data = read_object(source)
    if data.get("schema") == DIGEST_SCHEMA:
        return _digest(source.parent, data)
    from .compare import MENU_SCHEMA

    if data.get("schema") == MENU_SCHEMA:
        return _menu(data, source.parent)
    raise UsageError("report source must be a digest directory/manifest or a meal directory/menu")


def write_report(source: Path, output: Path, *, force: bool = False) -> Path:
    if output.suffix.lower() != ".html":
        raise UsageError("report output must end in .html")
    content = render_report(source)
    if output.exists() or output.is_symlink():
        metadata = output.lstat()
        if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 1024:
            raise UsageError("report output must not be a link or special file")
        if not force:
            raise UsageError(
                "report output already exists", hint="choose a new --out or explicitly use --force"
            )
    temporary = output.with_name(f".{output.name}-{uuid.uuid4().hex}.tmp")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8", newline="\n") as target:
            target.write(content)
        if force:
            temporary.replace(output)
        else:
            # A hard link atomically refuses a concurrently-created destination on both platforms.
            os.link(temporary, output)
        return output
    except OSError as exc:
        raise CrabError(f"cannot write HTML report: {exc.strerror}") from exc
    finally:
        temporary.unlink(missing_ok=True)
