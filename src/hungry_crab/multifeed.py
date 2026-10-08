"""Atomic multi-prey exports with stable-id deduplication and complete source cards."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cache import Target, cache_root, maw_paths, prey_paths
from .compare import MENU_SCHEMA, load_menu, menu_candidates
from .errors import CrabError, UsageError
from .feeder import EatOptions, EatResult, eat
from .fetch.catch import rmtree_force
from .mdutil import MdDoc, inline
from .nutrients import Candidate

SCHEMA = "hungry-crab.feeder-multi/1"
MODES = {"HUMAN": 0, "IDEAS_ONLY": 1, "REIMPLEMENT": 2, "COPY_FILE": 3, "COPY": 4}


def merge_menus(sources: Mapping[str, dict[str, Any]], *, top: int = 30) -> dict[str, Any]:
    if not sources:
        raise UsageError("a multi-prey meal needs sources")
    ordered = sorted(sources.items())
    first = ordered[0][1]
    maw = first["maw"]
    groups: dict[str, list[tuple[str, Candidate]]] = {}
    for name, menu in ordered:
        if (
            menu.get("schema") != MENU_SCHEMA
            or menu.get("maw") != maw
            or menu.get("mode") != first.get("mode")
        ):
            raise CrabError("multi-prey menus must describe the same maw snapshot and policy")
        for card in menu_candidates(menu):
            groups.setdefault(card.id, []).append((name, card))
    cards = []
    for _nutrient_id, group in sorted(groups.items()):
        # The representative already carries the strictest source ceiling. Never borrow a
        # permissive source's score or prose while serving a more restricted source's card.
        group.sort(key=lambda row: (MODES.get(row[1].license_mode, 0), -row[1].score, row[0]))
        primary, card = group[0]
        data = card.to_dict()
        data["trace"] = {
            **card.trace,
            "primary_source": primary,
            "sources": [
                {"source": name, "prey": menu_prey(sources[name]), "card": other.to_dict()}
                for name, other in group
            ],
        }
        cards.append(data)
    cards.sort(key=lambda card: (-card["score"], card["id"]))
    hidden = [{**item, "source": name} for name, menu in ordered for item in menu.get("hidden", [])]
    by_category: dict[str, int] = {}
    for row in cards:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    return {
        "schema": MENU_SCHEMA,
        "kind": "multi-prey",
        "generated_at": max(menu["generated_at"] for _, menu in ordered),
        "prey": {"label": "multi-prey", "sha": "", "url": None, "license": None},
        "preys": [menu_prey(menu) for _, menu in ordered],
        "maw": maw,
        "mode": first.get("mode", "normal"),
        "hunger": first.get("hunger", {}),
        "memory": first.get("memory", {}),
        "counts": {
            "total": len(cards),
            "top": top,
            "hidden": len(hidden),
            "by_category": by_category,
        },
        "candidates": cards,
        "hidden": hidden,
    }


def menu_prey(menu: dict[str, Any]) -> dict[str, Any]:
    return dict(menu["prey"])


def render_multi(menu: dict[str, Any]) -> MdDoc:
    doc = MdDoc("Multi-prey menu")
    doc.section("Sources").para(
        "One card per stable nutrient id. Each card retains all pinned source cards."
    )
    for card in menu["candidates"]:
        section = doc.section(inline(card["title"]))
        section.line(
            f"`{inline(card['id'])}` — score {card['score']:.2f}; {inline(card['license_mode'])}."
        )
        for source in card["trace"]["sources"]:
            prey = source["prey"]
            section.line(
                f"- {inline(prey['label'])}@{inline(prey['sha'])}: "
                f"{inline(source['card']['license_mode'])}"
            )
    return doc


def _fingerprints(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CrabError("multi-prey sources must not contain symlinks")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def eat_many(
    preys: list[Target],
    maw: Path,
    options: EatOptions,
    *,
    log: Callable[[str], None] = lambda _: None,
) -> EatResult:
    if not 2 <= len(preys) <= 10:
        raise UsageError("multi-prey Feeder accepts between two and ten prey")
    identities = [
        os.path.normcase(str(prey.path.resolve())) if prey.path else str(prey.slug).casefold()
        for prey in preys
    ]
    if len(set(identities)) != len(preys):
        raise UsageError("multi-prey Feeder refuses duplicate prey")
    maw = maw.resolve()
    root = (options.cache_root or cache_root()).resolve()
    out = options.out or maw_paths(maw, root).root / "feeds" / uuid.uuid4().hex
    if out.is_symlink() or any(p.is_symlink() for p in out.parents):
        raise UsageError("multi-prey output must not traverse symlinks")
    out = out.resolve()
    protected = [maw]
    for prey in preys:
        if prey.path:
            protected.append(prey.path.resolve())
        elif prey.slug:
            protected.append(prey_paths(prey.slug, root).repo.resolve())
    if (
        root.is_relative_to(maw)
        or root.is_relative_to(out)
        or any(out.is_relative_to(path) or path.is_relative_to(out) for path in protected)
    ):
        raise UsageError(
            "multi-prey output must be outside source repositories and not contain the cache"
        )
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise UsageError("multi-prey output must be new or empty")
    out.parent.mkdir(parents=True, exist_ok=True)
    staged = out.with_name(f".{out.name}-{uuid.uuid4().hex}")
    staged.mkdir()
    try:
        (staged / "sources").mkdir()
        menus = {}
        sources = []
        for identity, prey in sorted(zip(identities, preys, strict=True)):
            key = hashlib.sha256(identity.encode()).hexdigest()[:20]
            relative = "sources/" + key
            result = eat(prey, maw, replace(options, out=staged / relative), log=log)
            menu = load_menu(result.out_dir)
            if menu is None:
                raise CrabError("a source did not produce a menu")
            menus[key] = menu
            sources.append(
                {
                    "id": key,
                    "path": relative,
                    "target": identity,
                    "prey": result.manifest["prey"],
                    "files": _fingerprints(result.out_dir),
                }
            )
        menu = merge_menus(menus, top=options.top)
        menu_text = json.dumps(menu, indent=2, ensure_ascii=False) + "\n"
        (staged / "menu.json").write_text(menu_text, encoding="utf-8")
        pages = render_multi(menu).render_pages(3500, filename="menu.md")
        for index, page in enumerate(pages, 1):
            name = "menu.md" if index == 1 else f"menu.{index}.md"
            (staged / name).write_text(page.text, encoding="utf-8")
        manifest = {
            "schema": SCHEMA,
            "maw": menu["maw"],
            "counts": menu["counts"],
            "sources": sources,
            "side_effects": {"issues_created": 0, "ledger_written": False},
        }
        (staged / "feeder.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if out.exists():
            out.rmdir()
        staged.rename(out)
        return EatResult(out, manifest)
    finally:
        if staged.exists():
            rmtree_force(staged)


def load_multi(root: Path) -> tuple[dict[str, Any], dict[str, Path], dict[str, str]]:
    """Verify source files and rebuild the aggregate before a menu can drive serving."""
    root = root.resolve()
    try:
        manifest = json.loads((root / "feeder.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("sources"), list):
            raise ValueError("unsupported multi-prey schema")
        paths = {}
        targets = {}
        menus = {}
        for source in manifest["sources"]:
            key = source["id"]
            if (
                not isinstance(key, str)
                or len(key) != 20
                or any(ch not in "0123456789abcdef" for ch in key)
                or key in paths
            ):
                raise ValueError("invalid or duplicate source id")
            path = root / "sources" / key
            if (
                source["path"] != "sources/" + key
                or path.is_symlink()
                or not path.resolve().is_relative_to(root)
            ):
                raise ValueError("invalid source path")
            if _fingerprints(path) != source["files"]:
                raise ValueError("source bundle integrity mismatch")
            paths[key], targets[key] = path, source["target"]
            menu = load_menu(path)
            if menu is None:
                raise ValueError("missing source menu")
            menus[key] = menu
        saved = load_menu(root)
        if saved is None:
            raise ValueError("missing aggregate menu")
        rebuilt = merge_menus(menus, top=saved["counts"]["top"])
        if rebuilt != saved:
            raise ValueError("aggregate menu differs from its sources")
        return rebuilt, paths, targets
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CrabError(f"invalid multi-prey bundle: {exc}") from exc
