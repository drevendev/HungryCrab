"""Operator-scoped deterministic operations exposed by MCP, without publication authority."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Literal

from .artifact_read import MAX_PAGE_BYTES, markdown_pages, read_bytes, read_object
from .cache import Slug, Target
from .compare import compare_for_maw
from .digest import DigestOptions, incomplete_miners, run_digest
from .digest_integrity import digest_integrity_errors
from .errors import CrabError, UsageError
from .fetch.catch import CatchOptions
from .fetch.git import GitRunner
from .fs import is_link_stat
from .nutrients import CATEGORIES

_GIT_STORES = ("objects", "refs", "packed-refs", "info/grafts", "shallow")
# Git itself follows alternates of alternates this deep.
_MAX_ALTERNATE_DEPTH = 5
_GIT_DATA_OUTSIDE = "this directory's git data lies outside the operator-approved roots"
_GIT_DATA_LINKED = (
    "this directory's git data is reached through a symlink or junction, which could lead "
    "outside the operator-approved roots"
)


def _alternate_lines(path: Path) -> list[str]:
    """The entries of an alternates file as git reads them; no file means no entries.

    Git splits on newlines only and skips empty lines and ``#`` comments.
    """
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise UsageError("cannot inspect this repository's git data") from exc
    return [line for line in text.split("\n") if line and not line.startswith("#")]


def refuse_network_path(value: str) -> None:
    """Refuse UNC and device paths before any filesystem call.

    On Windows, resolving or even probing ``\\\\host\\share`` opens an SMB or WebDAV
    connection and offers the user's credentials, before any containment check can run. Tool
    arguments come from a model that reads prey text, so such a path is never touched.
    """
    text = value.strip()
    if len(text) >= 2 and text[0] in "\\/" and text[1] in "\\/":
        raise UsageError("network and device paths are not accepted; use a path under a root")


class AnalysisService:
    def __init__(
        self,
        roots: list[Path],
        cache: Path,
        *,
        allow_network: bool = False,
        validate: bool = True,
    ) -> None:
        self.roots = [root.expanduser().resolve() for root in roots]
        # As the operator wrote them, so a path under a root reached through a link passes the
        # lexical check that runs before anything is resolved.
        self.lexical_roots = [
            Path(os.path.normpath(root.expanduser().absolute())) for root in roots
        ]
        self.cache = cache.expanduser().resolve()
        self.allow_network = allow_network
        # The CLI cache is shared mutable state; serialize this server's own requests.
        self.lock = threading.Lock()
        if not validate:
            # `crab mcp --check` builds the server only to prove the SDK loads; it serves no
            # request, so it needs no roots and no cache placed outside them.
            return
        if not roots:
            raise UsageError("MCP needs at least one operator-approved --root")
        if any(not root.is_dir() for root in self.roots):
            raise UsageError("every MCP root must be an existing directory")
        if any(self.cache.is_relative_to(root) for root in self.roots):
            raise UsageError("MCP cache must be outside the approved source roots")

    def local(self, value: str) -> Path:
        if not value.strip() or "\x00" in value:
            raise UsageError("a local repository path is required")
        refuse_network_path(value)
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.roots[0] / path
        # Resolving touches the path (a mapped network drive, a slow mount), so only a path that
        # already lies under a root as written may be resolved and checked again.
        lexical = Path(os.path.normpath(path.absolute()))
        if not any(lexical.is_relative_to(root) for root in [*self.lexical_roots, *self.roots]):
            raise UsageError("repository is outside the operator-approved MCP roots")
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.roots) or path.is_relative_to(
            self.cache
        ):
            raise UsageError("repository is outside the operator-approved MCP roots")
        if not path.is_dir():
            raise UsageError("repository must be an existing local directory")
        self._refuse_outer_repository(path)
        return path

    def _refuse_outer_repository(self, path: Path) -> None:
        """Git looks upward for a repository; one above every root is not the operator's grant.

        A plain directory under a root that sits inside a repository outside the roots (a
        dotfiles repository at the home directory, a monorepo above a vendor drop) would
        otherwise hand that repository's log, paths and tags to the client. The work tree is not
        the whole answer: a ``.git`` file (``gitdir: <path>``), a ``.git`` link or a worktree's
        ``commondir`` moves the git directory, and with it the history, without moving the work
        tree, so the git and common directories must lie under a root too, and so must the
        object and ref stores inside them and every object alternate.
        """
        if not GitRunner.available():
            return
        git = GitRunner(path)
        top = (git.try_run("rev-parse", "--show-toplevel") or "").strip()
        if top and not any(Path(top).resolve().is_relative_to(root) for root in self.roots):
            raise UsageError(
                "this directory belongs to a git repository outside the operator-approved roots"
            )
        out = git.try_run("rev-parse", "--absolute-git-dir", "--git-common-dir") or ""
        git_dirs = [line for line in out.splitlines() if line.strip()]
        if top and not git_dirs:
            raise UsageError("cannot tell where this repository's git directory lies")
        if not git_dirs:
            return
        for line in git_dirs:
            self._git_data(path, line)
        # The history lives in the object and ref stores, and each can be moved on its own: a
        # junction or symlink for `objects/`, `refs/` or anything inside them, or an
        # `objects/info/alternates` entry that borrows another repository's objects.
        args = [arg for name in _GIT_STORES for arg in ("--git-path", name)]
        stores = [line for line in (git.try_run("rev-parse", *args) or "").splitlines() if line]
        if len(stores) != len(_GIT_STORES):
            raise UsageError("cannot tell where this repository's git data lies")
        for name, line in zip(_GIT_STORES, stores, strict=True):
            where = self._git_data(path, line)
            if name == "objects":
                self._check_object_store(where, depth=0)
            elif name == "refs":
                self._refuse_links_below(where)

    def _git_data(self, base: Path, printed: str) -> Path:
        """A path git reported (relative ones are relative to ``base``), checked for scope.

        Every component below the approved root (or ``base``, which is already real) is
        inspected without following it, so a symlink or junction anywhere on the way is refused
        even when it currently points inside the roots. The scope check runs on the path as
        the system resolves it, so a ``..`` after a link cannot step around it. Returns that
        resolved path.
        """
        written = base / printed
        lexical = Path(os.path.normpath(written))
        anchors = [a for a in (base, *self.roots, *self.lexical_roots) if lexical.is_relative_to(a)]
        if not anchors:
            raise UsageError(_GIT_DATA_OUTSIDE)
        current = max(anchors, key=lambda anchor: len(anchor.parts))
        for part in lexical.relative_to(current).parts:
            current = current / part
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                break
            except OSError as exc:
                raise UsageError("cannot inspect this repository's git data") from exc
            if is_link_stat(info):
                raise UsageError(_GIT_DATA_LINKED)
        resolved = written.resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots) or (
            resolved.is_relative_to(self.cache)
        ):
            raise UsageError(_GIT_DATA_OUTSIDE)
        return resolved

    def _refuse_links_below(self, directory: Path) -> None:
        """No symlink or junction anywhere in a git store, at any depth."""
        pending = [directory]
        while pending:
            try:
                with os.scandir(pending.pop()) as entries:
                    for entry in entries:
                        # Elsewhere a symlink is the only link, and `is_symlink` costs no call;
                        # on Windows the entry's cached lstat carries the junction's tag.
                        if entry.is_symlink() or (
                            os.name == "nt" and is_link_stat(entry.stat(follow_symlinks=False))
                        ):
                            raise UsageError(_GIT_DATA_LINKED)
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                raise UsageError("cannot inspect this repository's git data") from exc

    def _check_object_store(self, objects: Path, *, depth: int) -> None:
        """An object store and, as git follows them, its alternates, all inside the roots."""
        if depth > _MAX_ALTERNATE_DEPTH:
            raise UsageError("this repository's object alternates are nested too deeply")
        self._refuse_links_below(objects)
        info = objects / "info"
        if _alternate_lines(info / "http-alternates"):
            raise UsageError(_GIT_DATA_OUTSIDE)
        for line in _alternate_lines(info / "alternates"):
            if line.startswith('"'):
                raise UsageError("this repository's object alternates cannot be verified")
            # A relative alternate is relative to the object directory that names it.
            self._check_object_store(self._git_data(objects, line), depth=depth + 1)

    def prey(self, value: str) -> Target:
        if not value.strip() or "\x00" in value:
            raise UsageError("prey must not be empty")
        refuse_network_path(value)
        explicit = value.startswith((".", "/", "\\", "~")) or re.match(r"^[A-Za-z]:", value)
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.roots[0] / candidate
        # Stating a path is access too: a value that leaves the roots as written would let a
        # client probe which local paths exist (and wake an automounter). Out-of-root and
        # explicit values are handed to local(), which refuses them lexically without
        # touching the disk; a value inside a root is only probed when every component
        # below its root is a real entry, so a symlink or junction inside a root cannot
        # turn the probe into a look at an outside target.
        lexical = Path(os.path.normpath(candidate.absolute()))
        inside = any(lexical.is_relative_to(root) for root in [*self.lexical_roots, *self.roots])
        if explicit or not inside:
            return Target(path=self.local(value))
        # A normalized endpoint inside a root does not prove that every raw prefix stays
        # there. Reject implicit traversal before lstat/exists can probe an outside sibling.
        if ".." in candidate.parts:
            raise UsageError("cannot parse repository reference with parent traversal")
        if self._no_link_below(candidate) and candidate.exists():
            return Target(path=self.local(value))
        slug = Slug.parse(value)
        if not self.allow_network:
            raise UsageError("remote prey requires the operator's --allow-network option")
        return Target(slug=slug)

    def _no_link_below(self, path: Path) -> bool:
        """Whether no component of ``path`` below its containing root is a symlink or junction."""
        for root in [*self.lexical_roots, *self.roots]:
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                continue
            current = root
            for part in parts:
                current = current / part
                try:
                    if is_link_stat(current.lstat()):
                        return False
                except OSError:
                    return False
            return True
        return False

    def options(self, depth: Literal["normal", "deep"]) -> DigestOptions:
        if depth not in {"normal", "deep"}:
            raise UsageError("depth must be normal or deep")
        return DigestOptions(
            depth=depth,
            cache_root=self.cache,
            catch_options=CatchOptions(shallow=True, since="90d", issues=0, wiki=False),
        )

    def digest(
        self, prey: str, depth: Literal["normal", "deep"] = "normal", page: str | None = None
    ) -> dict[str, Any]:
        with self.lock:
            target = self.prey(prey)
            result = run_digest(target, self.options(depth))
            errors = digest_integrity_errors(result.out_dir, result.manifest)
            if (
                errors
                or incomplete_miners(result.manifest)
                or result.manifest.get("coverage", {}).get("healthy") is not True
            ):
                raise CrabError("MCP refuses incomplete or damaged digest evidence")
            pages = markdown_pages(result.out_dir, result.manifest)
            payload: dict[str, Any] = {
                "schema": "hungry-crab.mcp-digest/1",
                "untrusted_data": True,
                "manifest": result.manifest,
                "digest_dir": str(result.out_dir),
                "pages": pages,
                "side_effects": {
                    "scope": "cache only",
                    "ledger_written": False,
                    "published": False,
                },
            }
            if page is not None:
                if page not in pages:
                    raise UsageError("page must be one of the listed digest Markdown artifacts")
                payload["page"] = {
                    "name": page,
                    "text": read_bytes(result.out_dir / page, MAX_PAGE_BYTES).decode(
                        "utf-8", errors="replace"
                    ),
                }
            return payload

    def menu(
        self, prey: str, maw: str, offset: int = 0, limit: int = 15, category: str | None = None
    ) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise UsageError("offset must be non-negative and limit must be in 1..100")
        if category is not None and category not in CATEGORIES:
            raise UsageError("unknown nutrient category")
        with self.lock:
            target, root = self.prey(prey), self.local(maw)
            result, _, _, _ = compare_for_maw(
                target,
                root,
                digest_options=self.options("normal"),
                record_ledger=False,
                persist_meal=False,
                issue_lookup=None,
            )
            for directory in (result.prey_dir, result.maw_dir):
                if directory is None:
                    raise CrabError("MCP menu is missing its source digest")
                manifest = read_object(directory / "manifest.json")
                if (
                    incomplete_miners(manifest)
                    or digest_integrity_errors(directory, manifest)
                    or manifest.get("coverage", {}).get("healthy") is not True
                ):
                    raise CrabError("MCP refuses incomplete menu source evidence")
            rows = result.menu["candidates"]
            if category is not None:
                rows = [row for row in rows if row["category"] == category]
            selected = rows[offset : offset + limit]
            return {
                "schema": "hungry-crab.mcp-menu/1",
                "untrusted_data": True,
                "prey": result.menu["prey"],
                "maw": result.menu["maw"],
                "verdict": result.menu["verdict"],
                "meal_dir": str(result.meal_dir) if result.meal_dir is not None else None,
                "candidates": selected,
                "total": len(rows),
                "offset": offset,
                "next_offset": offset + len(selected)
                if offset + len(selected) < len(rows)
                else None,
                "hidden_count": len(result.hidden),
                "side_effects": {
                    "scope": "cache only",
                    "ledger_written": False,
                    "published": False,
                },
            }
