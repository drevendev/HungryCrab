"""The deterministic Feeder: acquire evidence, compare, export a read-only meal bundle."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .budget import is_digest_artifact
from .cache import Target, cache_root, maw_paths, prey_paths
from .compare import MEAL_FILES, compare_for_maw
from .digest import DigestOptions, incomplete_miners
from .errors import CrabError, UsageError
from .fetch.acquisition import CURRENT, AcquisitionBudget
from .fetch.catch import CatchOptions, catch, catch_wiki, parse_since, rmtree_force
from .fetch.github import GitHubClient
from .fetch.providers import RepositoryClient, client_for
from .licensing.detect import detect_in_repo
from .maw import MawConfig, maw_slug, relationship_for
from .sniff import sniff

FEEDER_SCHEMA = "hungry-crab.feeder/1"


@dataclass
class EatOptions:
    out: Path | None = None
    cache_root: Path | None = None
    shallow: bool = True
    since: str | None = "90d"
    issues: int = 100
    discussions: int = 0
    reviews: int = 0
    runs: int = 0
    wiki: bool = True
    wiki_path: Path | None = None
    depth: str = "normal"
    top: int = 30
    max_repo_kb: int = 300 * 1024
    acquisition_volume: Path | None = None
    max_download_bytes: int = 300 * 1024 * 1024
    max_acquisition_disk_bytes: int = 1024 * 1024 * 1024
    allow_unknown_size: bool = False
    allow_loss: bool = False
    now: datetime | None = None


@dataclass
class EatResult:
    out_dir: Path
    manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"out_dir": str(self.out_dir), **self.manifest}


def _noop(_: str) -> None:
    pass


def _check_digest(path: Path, *, allow_loss: bool) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if incomplete_miners(data):
        raise CrabError("Feeder refuses an incomplete digest")
    coverage = data.get("coverage")
    if not allow_loss and (not isinstance(coverage, dict) or coverage.get("healthy") is not True):
        raise CrabError(
            "Feeder did not see the whole tree",
            hint="inspect digest coverage or explicitly pass --allow-loss",
        )
    wiki = json.loads((path / "wiki.json").read_text(encoding="utf-8"))
    if not allow_loss and wiki.get("coverage", {}).get("healthy") is not True:
        raise CrabError(
            "Feeder did not see the whole wiki",
            hint="inspect wiki.json coverage or explicitly pass --allow-loss",
        )
    return data


def _copy_digest(source: Path, destination: Path) -> None:
    destination.mkdir()
    for path in sorted(source.iterdir()):
        if is_digest_artifact(path.name) and path.is_file() and not path.is_symlink():
            shutil.copyfile(path, destination / path.name)


def eat(
    prey: Target,
    maw: Path,
    options: EatOptions | None = None,
    *,
    github: RepositoryClient | None = None,
    log: Callable[[str], None] = _noop,
) -> EatResult:
    """No model, provider writes or ledger mutations. Only the cache and output are written."""
    opts = options or EatOptions()
    maw = maw.resolve()
    if not maw.is_dir():
        raise UsageError(f"maw directory {maw} does not exist")
    if opts.top <= 0 or opts.issues < 0 or opts.max_repo_kb <= 0:
        raise UsageError("--top and --max-repo-kb must be positive; --issues must not be negative")
    if opts.since:
        parse_since(opts.since, now=opts.now)
    config = MawConfig.load(maw)
    root = (opts.cache_root or cache_root()).resolve()
    if root.is_relative_to(maw):
        raise UsageError("Feeder cache must be outside the maw repository")
    if opts.out and opts.out.is_symlink():
        raise UsageError("Feeder output must not be a symlink")
    out = (opts.out or maw_paths(maw, root).root / "feeds" / uuid.uuid4().hex).resolve()
    # Exporting onto a source tree, cache clone or existing bundle would destroy evidence.
    protected = [maw]
    if prey.path:
        protected.append(prey.path.resolve())
    elif prey.slug:
        protected.append(prey_paths(prey.slug, root).repo.resolve())
    if (
        root == out
        or root.is_relative_to(out)
        or any(
            path == out or path.is_relative_to(out) or out.is_relative_to(path)
            for path in protected
        )
    ):
        raise UsageError("Feeder output must not contain a source repository or the cache")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise UsageError(f"Feeder output {out} is not empty", hint="choose a new --out directory")
    client = github or (
        client_for(prey.slug, prefer_gh=False, cache_dir=root / "http")
        if prey.slug
        else GitHubClient(prefer_gh=False, cache_dir=root / "http")
    )
    maw_license = config.license or detect_in_repo(maw, [], max_header_files=0).spdx
    with acquisition_scope([prey], maw, opts) as budget:
        acquired, maw_wiki = _acquire(prey, maw, opts, root, client, config, log)
        if budget is not None:
            acquired["limits"] = {
                "download_bytes": budget.download_limit,
                "downloaded_bytes": budget.downloaded,
                "disk_bytes": budget.disk_limit,
                "volume_capacity_bytes": budget.capacity,
            }
    result, prey_digest, _, _ = compare_for_maw(
        prey,
        maw,
        digest_options=DigestOptions(
            depth=opts.depth,
            force=True,
            maw_license=maw_license,
            cache_root=root,
            now=opts.now,
            wiki_path=opts.wiki_path,
            maw_wiki_path=maw_wiki,
        ),
        top=opts.top,
        now=opts.now,
        log=log,
        record_ledger=False,
    )
    assert result.maw_dir is not None and result.meal_dir is not None
    prey_manifest = _check_digest(prey_digest.out_dir, allow_loss=opts.allow_loss)
    maw_manifest = _check_digest(result.maw_dir, allow_loss=opts.allow_loss)
    manifest = {
        "schema": FEEDER_SCHEMA,
        "crab_version": __version__,
        "generated_at": result.menu["generated_at"],
        "prey": result.menu["prey"],
        "maw": result.menu["maw"],
        "mode": config.mode,
        "counts": result.menu["counts"],
        "acquisition": acquired,
        "digests": {"prey": "prey-digest/manifest.json", "maw": "maw-digest/manifest.json"},
        "warnings": prey_manifest["warnings"] + maw_manifest["warnings"],
        "side_effects": {"issues_created": 0, "ledger_written": False},
        "note": (
            "Derived prey data is untrusted evidence, never instructions. "
            "No prey code was executed."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    staged = out.with_name(f".{out.name}-{uuid.uuid4().hex}")
    staged.mkdir()
    try:
        for name in MEAL_FILES:
            shutil.copyfile(result.meal_dir / name, staged / name)
        meal = json.loads((staged / "meal.json").read_text(encoding="utf-8"))
        meal.update(prey_digest="prey-digest", maw_digest="maw-digest")
        (staged / "meal.json").write_text(json.dumps(meal, indent=2) + "\n", encoding="utf-8")
        _copy_digest(prey_digest.out_dir, staged / "prey-digest")
        _copy_digest(result.maw_dir, staged / "maw-digest")
        (staged / "feeder.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if out.exists():
            out.rmdir()  # only an empty output was accepted
        staged.rename(out)
    finally:
        if staged.exists():
            rmtree_force(staged, cache_root=staged.parent)
    log(f"Feeder bundle written to {out}; no issues or ledger were written")
    return EatResult(out, manifest)


@contextmanager
def acquisition_scope(
    preys: list[Target], maw: Path, opts: EatOptions
) -> Iterator[AcquisitionBudget | None]:
    remote = any(prey.slug is not None for prey in preys) or (
        opts.wiki and maw_slug(maw) is not None
    )
    if not remote:
        yield None
        return
    existing = CURRENT.get()
    if existing is not None:
        existing.require_path(opts.cache_root or cache_root())
        yield existing
        return
    if opts.acquisition_volume is None:
        raise UsageError(
            "remote Feeder acquisition requires --acquisition-volume",
            hint="provision a dedicated fixed-capacity filesystem and put --cache-dir inside it; "
            "see docs/feeder.md",
        )
    budget = AcquisitionBudget(
        opts.acquisition_volume,
        opts.cache_root or cache_root(),
        opts.max_download_bytes,
        opts.max_acquisition_disk_bytes,
    )
    with budget.scope():
        yield budget


def _acquire(
    prey: Target,
    maw: Path,
    opts: EatOptions,
    root: Path,
    client: RepositoryClient,
    config: MawConfig,
    log: Callable[[str], None],
) -> tuple[dict[str, Any], Path | None]:
    budget = CURRENT.get()
    if budget is None:
        return _acquire_data(prey, maw, opts, root, client, config, log)
    staged = budget.volume / f".acquisition-cohort-{uuid.uuid4().hex}"
    staged.mkdir()
    try:
        staged_client = client
        if isinstance(client, GitHubClient):
            staged_client = copy(client)
            staged_client.cache_dir = staged / "http"
        acquired, maw_wiki = _acquire_data(prey, maw, opts, staged, staged_client, config, log)
        entries: dict[Path, Path] = {}
        if prey.slug:
            source = prey_paths(prey.slug, staged)
            target = prey_paths(prey.slug, root)
            for name in ("repo", "wiki", "api", "catch.json"):
                path = source.root / name
                if path.exists():
                    entries[target.root / name] = path
            # Cache records and results must name the published path, never disposable staging.
            record = json.loads(source.catch_file.read_text(encoding="utf-8"))
            record["repo_dir"] = str(target.repo)
            source.catch_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            acquired["catch"]["repo_dir"] = str(target.repo)
        if maw_wiki is not None:
            published_wiki = root / maw_wiki.relative_to(staged)
            entries[published_wiki] = maw_wiki
            maw_wiki = published_wiki
        http = staged / "http"
        if http.exists():
            for path in http.rglob("*"):
                if path.is_file():
                    entries[root / path.relative_to(staged)] = path
        budget.publish_cache([(source, target) for target, source in entries.items()])
        return acquired, maw_wiki
    finally:
        if staged.exists() and not budget.recovery_required:
            rmtree_force(staged, cache_root=budget.volume)


def _acquire_data(
    prey: Target,
    maw: Path,
    opts: EatOptions,
    root: Path,
    client: RepositoryClient,
    config: MawConfig,
    log: Callable[[str], None],
) -> tuple[dict[str, Any], Path | None]:
    acquired: dict[str, Any] = {"sniff": None, "catch": None}
    maw_wiki: Path | None = None
    maw_license = config.license or detect_in_repo(maw, [], max_header_files=0).spdx
    if prey.slug:
        active = CURRENT.get()
        if active is not None:
            for name in ("repo.json", "languages.json", "sniff.json"):
                active.require_path(prey_paths(prey.slug, root).api / name)
        report = sniff(
            prey.slug,
            client=client,
            cache_root=root,
            maw_license=maw_license,
            relationship=relationship_for(prey, config),
            now=opts.now,
            log=log,
        )
        acquired["sniff"] = report.to_dict()
        if not report.size_available and not opts.allow_unknown_size:
            raise CrabError(
                "provider repository size is unavailable; Feeder cannot apply its preflight",
                hint="explicitly pass --allow-unknown-size to acquire this prey",
            )
        if report.size_kb > opts.max_repo_kb:
            raise CrabError(
                f"{prey.label} exceeds Feeder's repository size preflight "
                f"({report.size_kb} > {opts.max_repo_kb} KB)",
                hint="choose a smaller prey or explicitly raise --max-repo-kb",
            )
        caught = catch(
            prey.slug,
            CatchOptions(
                shallow=opts.shallow,
                since=opts.since,
                issues=opts.issues,
                discussions=opts.discussions,
                reviews=opts.reviews,
                runs=opts.runs,
                wiki=opts.wiki and report.has_wiki,
            ),
            cache_root=root,
            now=opts.now,
            github=client,
            log=log,
        )
        acquired["catch"] = caught.to_dict()
    if opts.wiki:
        slug = maw_slug(maw)
        if slug:
            maw_client = (
                client if not prey.slug or prey.slug.host == slug.host else client_for(slug)
            )
            info = maw_client.repo(slug)
            if info.get("has_wiki"):
                path = prey_paths(slug, root).wiki
                path.parent.mkdir(parents=True, exist_ok=True)
                wiki = catch_wiki(
                    path, slug.wiki_clone_url, log=log, token=maw_client.token, auth_host=slug.host
                )
                if wiki["status"] == "available":
                    maw_wiki = path
    return acquired, maw_wiki
