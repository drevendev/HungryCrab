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

from .analysis_context import fingerprint, ledger_identity
from .cache import Slug, Target, cache_root, maw_paths, prey_paths
from .compare import MENU_SCHEMA, load_menu, menu_candidates
from .errors import CrabError, UsageError
from .feeder import EatOptions, EatResult, acquisition_scope, eat
from .fetch.catch import rmtree_force
from .mdutil import MdDoc, inline
from .nutrients import Candidate, slugify

LEGACY_SCHEMA = "hungry-crab.feeder-multi/1"
SCHEMA = "hungry-crab.feeder-multi/2"
MODES = {"HUMAN": 0, "IDEAS_ONLY": 1, "REIMPLEMENT": 2, "COPY_FILE": 3, "COPY": 4}


def merge_menus(sources: Mapping[str, dict[str, Any]], *, top: int = 30) -> dict[str, Any]:
    if not sources:
        raise UsageError("a multi-prey meal needs sources")
    ordered = sorted(sources.items())
    first = ordered[0][1]
    maw = first["maw"]
    context = _receiving_identity(first)
    groups: dict[str, list[tuple[str, Candidate]]] = {}
    prey_keys: dict[str, str] = {}
    for name, menu in ordered:
        observed = menu.get("receiving_context")
        if isinstance(observed, dict) and (
            not isinstance(observed.get("worktree"), str) or observed["worktree"] in {"", "unknown"}
        ):
            raise CrabError(
                "multi-prey receiving snapshot is unknown; regenerate from a known worktree"
            )
        if (
            menu.get("schema") != MENU_SCHEMA
            or menu.get("maw") != maw
            or menu.get("mode") != first.get("mode")
            or _receiving_identity(menu) != context
        ):
            raise CrabError("multi-prey menus must describe the same maw snapshot and policy")
        prey_key = slugify(str(menu["prey"]["label"]))
        if prey_key in prey_keys:
            raise CrabError(
                f"multi-prey menus contain ambiguous prey labels for {prey_keys[prey_key]!r} "
                f"and {name!r} (nutrient slug {prey_key!r})"
            )
        prey_keys[prey_key] = name
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
        "receiving_context": first.get("receiving_context"),
        "receiving_context_status": "observed"
        if first.get("receiving_context")
        else "legacy-unknown",
        "counts": {
            "total": len(cards),
            "top": top,
            "hidden": len(hidden),
            "by_category": by_category,
        },
        "candidates": cards,
        "hidden": hidden,
    }


def _receiving_identity(menu: dict[str, Any]) -> str:
    inputs = menu.get("analysis_inputs", {})
    return fingerprint(
        {
            "context": menu.get("receiving_context"),
            "worktree": menu.get("post_comparison_worktree"),
            "policy": inputs.get("policy"),
            "ledger": ledger_identity(inputs.get("ledger")),
            "hunger": menu.get("hunger"),
            "scoring": menu.get("scoring"),
            "memory": menu.get("memory"),
        }
    )


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


def _target_identity(target: Target) -> str:
    if target.slug is not None and target.path is None:
        return str(target.slug).casefold()
    if target.path is not None and target.slug is None:
        return os.path.normcase(str(target.path.resolve()))
    raise UsageError("a multi-prey target must be exactly one slug or local path")


def _target_record(target: Target) -> dict[str, str]:
    if target.slug is not None and target.path is None:
        return {"kind": "slug", "value": str(target.slug)}
    if target.path is not None and target.slug is None:
        return {"kind": "path", "value": str(target.path)}
    raise UsageError("a multi-prey target must be exactly one slug or local path")


def _source_id(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _parse_recorded_slug(value: str) -> Slug:
    try:
        slug = Slug.parse(value)
    except UsageError as exc:
        raise ValueError("invalid recorded slug") from exc
    if str(slug) != value:
        raise ValueError("recorded slug is not canonical")
    return slug


def _parse_recorded_slug_url(value: str) -> Slug:
    try:
        slug = Slug.parse(value)
    except UsageError as exc:
        raise ValueError("invalid recorded slug URL") from exc
    if slug.url != value:
        raise ValueError("recorded slug URL is not canonical")
    return slug


def _local_source_root(path: Path) -> str:
    try:
        manifest = json.loads((path / "prey-digest" / "manifest.json").read_text(encoding="utf-8"))
        prey = manifest["prey"]
        root = prey["root"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("missing local prey source identity") from exc
    if not isinstance(root, str):
        raise ValueError("invalid local prey source identity")
    return root


def _target_from_v2(source: dict[str, Any], path: Path, menu: dict[str, Any]) -> Target:
    identity = source.get("identity")
    if not isinstance(identity, str) or not identity:
        raise ValueError("missing source identity")
    key = source.get("id")
    if key != _source_id(identity):
        raise ValueError("source id does not match identity")
    record = source.get("target")
    if not isinstance(record, dict) or set(record) != {"kind", "value"}:
        raise ValueError("invalid typed target")
    kind, value = record.get("kind"), record.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("invalid target value")
    prey = menu.get("prey")
    if not isinstance(prey, dict):
        raise ValueError("missing source prey identity")
    if kind == "slug":
        slug = _parse_recorded_slug(value)
        if identity != str(slug).casefold():
            raise ValueError("source identity does not match slug target")
        if prey.get("label") != value or prey.get("url") != slug.url:
            raise ValueError("slug target does not match source menu")
        return Target(slug=slug)
    if kind == "path":
        local_path = Path(value)
        if not local_path.is_absolute() or str(local_path.resolve()) != value:
            raise ValueError("local target must be a canonical absolute path")
        if identity != os.path.normcase(value):
            raise ValueError("source identity does not match local target")
        if prey.get("url") is not None or prey.get("label") != local_path.name:
            raise ValueError("local target does not match source menu")
        if _local_source_root(path) != value:
            raise ValueError("local target does not match acquired source path")
        return Target(path=local_path)
    raise ValueError("invalid target kind")


def _target_from_v1(value: str, path: Path, menu: dict[str, Any]) -> Target:
    prey = menu.get("prey")
    if not isinstance(prey, dict) or not isinstance(prey.get("label"), str):
        raise ValueError("missing legacy source prey identity")
    url = prey.get("url")
    if isinstance(url, str) and url:
        slug = _parse_recorded_slug_url(url)
        if prey["label"] != str(slug) or value != str(slug).casefold():
            raise ValueError("legacy slug target does not match source menu")
        return Target(slug=slug)
    if url is not None:
        raise ValueError("invalid legacy source URL")
    local_path = Path(value)
    if not local_path.is_absolute():
        raise ValueError("legacy local target must be absolute")
    identity = os.path.normcase(str(local_path.resolve()))
    if value != identity or os.path.normcase(prey["label"]) != os.path.normcase(local_path.name):
        raise ValueError("legacy local target does not match source menu")
    source_root = _local_source_root(path)
    # Version 1 did not record the producer CWD. If this relative digest root now
    # resolves differently, its source cannot be matched safely; fail closed.
    if os.path.normcase(str(Path(source_root).resolve())) != identity:
        raise ValueError("legacy local target does not match acquired source path")
    return Target(path=local_path)


def eat_many(
    preys: list[Target],
    maw: Path,
    options: EatOptions,
    *,
    log: Callable[[str], None] = lambda _: None,
) -> EatResult:
    if not 2 <= len(preys) <= 10:
        raise UsageError("multi-prey Feeder accepts between two and ten prey")
    preys = [
        Target(path=prey.path.resolve()) if prey.path is not None and prey.slug is None else prey
        for prey in preys
    ]
    identities = [_target_identity(prey) for prey in preys]
    if len(set(identities)) != len(preys):
        raise UsageError("multi-prey Feeder refuses duplicate prey")
    # Prey-specific nutrient IDs use slugified labels, not the unique cache identities.
    # Refuse collisions before creating a staged bundle or acquiring untrusted prey.
    prey_keys: dict[str, str] = {}
    for identity, prey in zip(identities, preys, strict=True):
        prey_key = slugify(prey.label)
        if prey_key in prey_keys:
            raise UsageError(
                f"multi-prey Feeder refuses ambiguous prey labels for {prey_keys[prey_key]!r} "
                f"and {identity!r} (nutrient slug {prey_key!r}); "
                "rename a local prey directory or use distinct repository names"
            )
        prey_keys[prey_key] = identity
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
        with acquisition_scope(preys, maw, options):
            for identity, prey in sorted(zip(identities, preys, strict=True)):
                key = _source_id(identity)
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
                        "identity": identity,
                        "target": _target_record(prey),
                        "prey": menu["prey"],
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
            rmtree_force(staged, cache_root=staged.parent)


def load_multi(root: Path) -> tuple[dict[str, Any], dict[str, Path], dict[str, Target]]:
    """Verify source files and rebuild the aggregate before a menu can drive serving."""
    root = root.resolve()
    try:
        manifest = json.loads((root / "feeder.json").read_text(encoding="utf-8"))
        schema = manifest.get("schema")
        if schema not in {LEGACY_SCHEMA, SCHEMA} or not isinstance(manifest.get("sources"), list):
            raise ValueError("unsupported multi-prey schema")
        legacy = schema == LEGACY_SCHEMA
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
            menu = load_menu(path)
            if menu is None:
                raise ValueError("missing source menu")
            if source.get("prey") != menu.get("prey"):
                raise ValueError("source prey identity differs from its menu")
            if legacy:
                target = source.get("target")
                if not isinstance(target, str) or key != _source_id(target):
                    raise ValueError("source id does not match legacy target")
                targets[key] = _target_from_v1(target, path, menu)
            else:
                targets[key] = _target_from_v2(source, path, menu)
            paths[key] = path
            menus[key] = menu
        saved = load_menu(root)
        if saved is None:
            raise ValueError("missing aggregate menu")
        rebuilt = merge_menus(menus, top=saved["counts"]["top"])
        # Older saved aggregates did not advertise receiving-context coverage.
        for additive in ("receiving_context", "receiving_context_status"):
            if additive not in saved:
                rebuilt.pop(additive, None)
        if rebuilt != saved:
            raise ValueError("aggregate menu differs from its sources")
        return rebuilt, paths, targets
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CrabError(f"invalid multi-prey bundle: {exc}") from exc
