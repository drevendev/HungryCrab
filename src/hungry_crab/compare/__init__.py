"""``crab compare``: prey digest minus maw digest, scored into a menu.

Outputs land in the prey's digest folder next to the miners' files: ``gap.md`` (facts),
``menu.md`` and ``menu.json`` (ranked candidates), ``compare.json`` (what was compared). The
comparison is maw-specific, so the last compare wins; ``compare.json`` says which maw it was.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..cache import Slug, Target
from ..digest import DigestOptions, DigestResult, refresh_manifest, run_digest
from ..errors import CrabError
from ..ledger import Ledger
from ..licensing import decide
from ..maw import MawConfig, maw_slug
from ..nutrients import Candidate
from ..typeutil import as_dict
from .candidates import Side, build_candidates
from .render import gap_doc, menu_doc
from .scoring import Scoring

MENU_SCHEMA = "hungry-crab.menu/1"
COMPARE_FILES = ("gap.md", "menu.md", "menu.json", "compare.json")


def _noop(_: str) -> None:
    return None


@dataclass
class CompareOptions:
    hunger: dict[str, Any] = field(default_factory=dict)
    scoring: dict[str, Any] | None = None
    ignore: list[str] = field(default_factory=list)
    top: int = 30
    maw_license: str | None = None
    hidden_ids: dict[str, str] = field(default_factory=dict)  # id -> reason (ledger, issues)
    now: datetime | None = None
    md_budget: int = 3500


@dataclass
class CompareResult:
    prey: Side
    maw: Side
    candidates: list[Candidate]
    hidden: list[dict[str, Any]]
    verdict: dict[str, Any]
    scoring: Scoring
    facts: dict[str, Any]
    gap_md: str
    menu_md: str
    menu: dict[str, Any]
    prey_dir: Path | None = None
    maw_dir: Path | None = None

    @property
    def shown(self) -> list[Candidate]:
        return self.candidates[: int(self.menu["counts"]["top"])]


def apply_hunger(
    candidates: list[Candidate], hunger: dict[str, Any]
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """``false`` drops a category, ``issues-only``/``ideas-only`` downgrade what it is served as."""
    kept: list[Candidate] = []
    hidden: list[dict[str, Any]] = []
    for candidate in candidates:
        setting = hunger.get(candidate.category, True)
        if setting is False or (
            isinstance(setting, str) and setting.lower() in ("off", "false", "no")
        ):
            hidden.append({"id": candidate.id, "reason": f"hunger: {candidate.category} is off"})
            continue
        if isinstance(setting, str):
            mode = setting.lower()
            if mode == "issues-only" and candidate.serve_as == "pr":
                candidate.serve_as = "issue"
            elif mode == "ideas-only":
                candidate.serve_as = "idea"
        kept.append(candidate)
    return kept, hidden


def compare_digests(
    prey_dir: Path,
    maw_dir: Path,
    *,
    prey_root: Path | None = None,
    maw_root: Path | None = None,
    options: CompareOptions | None = None,
) -> CompareResult:
    opts = options or CompareOptions()
    prey = Side.load(prey_dir, root=prey_root)
    maw = Side.load(maw_dir, root=maw_root)
    maw_spdx = opts.maw_license or maw.spdx
    verdict = decide(prey.spdx, maw_spdx).to_dict()
    scoring = Scoring.default().merged(opts.scoring)
    candidates, facts = build_candidates(prey, maw)
    now = opts.now or datetime.now(UTC)
    for candidate in candidates:
        candidate.license_mode = str(verdict["mode"])
        candidate.applicability = round(
            candidate.applicability * scoring.applicability_for("same_stack"), 2
        )
        candidate.trace = {
            "prey": prey.label,
            "url": prey.url,
            "sha": prey.sha,
            "license": prey.spdx,
            "maw": maw.label,
            "maw_sha": maw.sha,
            "compared_at": now.isoformat(timespec="seconds"),
        }
    candidates, hidden = apply_hunger(candidates, opts.hunger)
    still: list[Candidate] = []
    for candidate in candidates:
        reason = opts.hidden_ids.get(candidate.id)
        if reason:
            hidden.append({"id": candidate.id, "reason": reason})
        else:
            still.append(candidate)
    candidates = still
    for candidate in candidates:
        candidate.score = scoring.score(candidate)
    candidates.sort(key=lambda c: (-c.score, c.category, c.key))
    explain = {c.id: scoring.explain(c) for c in candidates[: opts.top]}
    gap_md = gap_doc(prey, maw, candidates, facts, verdict).render(opts.md_budget)
    menu_md = menu_doc(
        prey, maw, candidates, hidden, verdict, top=opts.top, explain=explain
    ).render(opts.md_budget)
    by_category: dict[str, int] = {}
    for candidate in candidates:
        by_category[candidate.category] = by_category.get(candidate.category, 0) + 1
    menu = {
        "schema": MENU_SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "prey": {
            "label": prey.label,
            "url": prey.url,
            "sha": prey.sha,
            "license": prey.spdx,
            "license_class": prey.license.get("class"),
            "ecosystems": sorted(prey.ecosystems),
        },
        "maw": {
            "label": maw.label,
            "sha": maw.sha,
            "license": maw_spdx,
            "ecosystems": sorted(maw.ecosystems),
            "root": str(maw_root) if maw_root else None,
        },
        "verdict": verdict,
        "hunger": opts.hunger,
        "scoring": scoring.to_dict(),
        "counts": {
            "total": len(candidates),
            "top": opts.top,
            "hidden": len(hidden),
            "by_category": by_category,
        },
        "candidates": [c.to_dict() for c in candidates],
        "hidden": hidden,
    }
    return CompareResult(
        prey=prey,
        maw=maw,
        candidates=candidates,
        hidden=hidden,
        verdict=verdict,
        scoring=scoring,
        facts=facts,
        gap_md=gap_md,
        menu_md=menu_md,
        menu=menu,
        prey_dir=prey_dir,
        maw_dir=maw_dir,
    )


def write_compare(result: CompareResult, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gap.md").write_text(result.gap_md, encoding="utf-8", newline="\n")
    (out_dir / "menu.md").write_text(result.menu_md, encoding="utf-8", newline="\n")
    (out_dir / "menu.json").write_text(
        json.dumps(result.menu, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    compare_info = {
        "schema": "hungry-crab.compare/1",
        "generated_at": result.menu["generated_at"],
        "prey": result.menu["prey"],
        "maw": result.menu["maw"],
        "maw_digest": str(result.maw_dir) if result.maw_dir else None,
        "candidates": result.menu["counts"]["total"],
        "hidden": result.menu["counts"]["hidden"],
        "verdict": result.verdict,
    }
    (out_dir / "compare.json").write_text(
        json.dumps(compare_info, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    maw_license = as_dict(result.menu["maw"]).get("license")
    refresh_manifest(out_dir, {"license": {"verdict": result.verdict, "maw_license": maw_license}})
    return list(COMPARE_FILES)


def load_menu(prey_dir: Path) -> dict[str, Any] | None:
    path = prey_dir / "menu.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return as_dict(loaded) if isinstance(loaded, dict) else None


def menu_candidates(menu: dict[str, Any]) -> list[Candidate]:
    return [Candidate.from_dict(as_dict(item)) for item in menu.get("candidates", [])]


IssueLookup = Callable[[Slug, str], dict[str, dict[str, Any]]]


def compare_for_maw(
    prey_target: Target,
    maw_root: Path,
    *,
    digest_options: DigestOptions | None = None,
    top: int = 30,
    issue_lookup: IssueLookup | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = _noop,
) -> tuple[CompareResult, DigestResult, Ledger, MawConfig]:
    """The full maw-aware comparison: .crab.yml hunger and scoring, ledger and issue dedup."""
    config = MawConfig.load(maw_root)
    d_opts = digest_options or DigestOptions()
    ledger = Ledger.load(config.ledger_path(d_opts.cache_root), maw=maw_root.name)
    hidden = ledger.hidden_ids()
    if issue_lookup is not None:
        slug = maw_slug(maw_root)
        if slug is not None:
            try:
                for nutrient_id, info in issue_lookup(slug, config.serve.label).items():
                    hidden.setdefault(
                        nutrient_id, f"issue #{info.get('number')} ({info.get('state')})"
                    )
            except CrabError as exc:
                log(f"warning: could not check existing issues: {exc.message}")
    options = CompareOptions(
        hunger=config.hunger,
        scoring=config.scoring,
        ignore=config.ignore,
        top=top,
        maw_license=config.license or d_opts.maw_license,
        hidden_ids=hidden,
        now=now,
    )
    result, prey_digest, _ = run_compare(
        prey_target, maw_root, digest_options=d_opts, options=options, log=log
    )
    new = ledger.record_meal(result.menu, result.candidates, now=now)
    saved = ledger.save(now=now)
    log(
        f"ledger: {new} new nutrients, {len(ledger.entries)} known"
        + (f", saved to {saved}" if saved else " (ledger mode: none)")
    )
    return result, prey_digest, ledger, config


def run_compare(
    prey_target: Target,
    maw_path: Path,
    *,
    digest_options: DigestOptions | None = None,
    options: CompareOptions | None = None,
    log: Callable[[str], None] = _noop,
) -> tuple[CompareResult, DigestResult, DigestResult]:
    """Digest both sides (cached by SHA), compare, and write the outputs into the prey digest."""
    d_opts = digest_options or DigestOptions()
    opts = options or CompareOptions()
    prey_result = run_digest(prey_target, d_opts, log=log)
    maw_options = DigestOptions(
        depth=d_opts.depth,
        force=d_opts.force,
        maw_license=opts.maw_license or d_opts.maw_license,
        now=d_opts.now,
        cache_root=d_opts.cache_root,
        ignore=opts.ignore,
    )
    maw_result = run_digest(Target(path=maw_path), maw_options, log=log)
    prey_root_value = as_dict(prey_result.manifest.get("prey")).get("root")
    prey_root = Path(prey_root_value) if isinstance(prey_root_value, str) else None
    result = compare_digests(
        prey_result.out_dir,
        maw_result.out_dir,
        prey_root=prey_root if prey_root and prey_root.is_dir() else None,
        maw_root=maw_path,
        options=opts,
    )
    write_compare(result, prey_result.out_dir)
    log(
        f"compared {result.prey.label}@{result.prey.short_sha} with "
        f"{result.maw.label}@{result.maw.short_sha}: {len(result.candidates)} candidates"
    )
    return result, prey_result, maw_result
