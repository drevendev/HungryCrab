"""Trusted action adapter. Inputs reach argparse as arguments, never as shell code."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import uuid
from pathlib import Path

from hungry_crab.cache import Slug
from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.fetch.git import GitRunner
from hungry_crab.fetch.providers import client_for
from hungry_crab.maw import maw_slug

EXPOSURE_REFUSAL = (
    "refusing to build a meal artifact: the calling repository is public, or its visibility is "
    "unknown, and {sides} not verifiably public. The bundle carries both digests (file trees, "
    "issue titles and excerpts, commit subjects, wiki headings), which would be downloadable by "
    "anyone signed in to GitHub. Set allow-private-artifact: true to upload it anyway."
)


def caller_is_private() -> bool | None:
    """Visibility of the calling repository, from the event payload the runner provides."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return None
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    repository = event.get("repository") if isinstance(event, dict) else None
    if not isinstance(repository, dict):
        return None
    if repository.get("visibility") in {"private", "internal"} or repository.get("private") is True:
        return True
    if repository.get("visibility") == "public" or repository.get("private") is False:
        return False
    return None


def forge_says_public(slug: Slug) -> bool:
    """True only when the forge answers and its answer is public; contradictions are not."""
    try:
        data = client_for(slug).repo(slug)
    except (CrabError, ValueError):
        return False
    visibility = data.get("visibility")
    if visibility is not None:
        return bool(visibility == "public")
    return data.get("private") is False


def prey_is_public(prey: str, *, local: bool) -> bool:
    """True only when the forge confirms the prey is public; a local checkout never is."""
    if local:
        return False
    try:
        slug = Slug.parse(prey)
    except (CrabError, ValueError):
        return False
    return forge_says_public(slug)


def maw_origin(maw: Path) -> Slug | None:
    """The forge repository a maw checkout is the root of, or None when that is not known.

    Git searches parent directories for a repository, so a plain directory inside the caller's
    checkout (a tarball download, say) would otherwise borrow the caller's origin.
    """
    if not maw.is_dir() or not GitRunner.available():
        return None
    top = GitRunner(maw).toplevel()
    try:
        if top is None or not top.samefile(maw):
            return None
    except OSError:
        return None
    return maw_slug(maw)


def _is_caller(slug: Slug | None) -> bool:
    caller = os.environ.get("GITHUB_REPOSITORY", "")
    return (
        slug is not None
        and slug.host == "github.com"
        and f"{slug.owner}/{slug.repo}".casefold() == caller.casefold()
    )


GITLINK_MODE = "160000"


def _tracks_a_file(listing: str) -> bool:
    """True when ``git ls-files -s -z`` output holds an entry that is not a gitlink.

    A gitlink (mode 160000) is a submodule or nested repository: another repository whose
    visibility the caller's says nothing about, not a file the caller tracks.
    """
    for entry in listing.split("\0"):
        mode = entry.partition(" ")[0]
        if mode and mode != GITLINK_MODE:
            return True
    return False


def maw_in_public_caller(maw: Path) -> bool:
    """A tracked directory of the public calling repository's own checkout (a monorepo package).

    The caller's checkout sits at the workspace root; a directory below it holding files the
    caller tracks is part of that public repository. An untracked or ignored directory is not:
    it may hold anything a previous step wrote there. Neither is a submodule, or a directory
    inside one: its gitlink is the only entry the caller tracks, and the repository behind it
    may be private, so it is confirmed by its own origin or not at all.
    """
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace or caller_is_private() is not False or not GitRunner.available():
        return False
    root = Path(workspace)
    try:
        relative = maw.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    if not maw.is_dir() or not _is_caller(maw_origin(root)):
        return False
    # The maw must belong to the caller's own work tree, not to a repository nested in it.
    top = GitRunner(maw).toplevel()
    try:
        if top is None or not top.samefile(root):
            return False
    except OSError:
        return False
    tracked = GitRunner(root).try_run("ls-files", "-s", "-z", "--", relative.as_posix() or ".")
    return tracked is not None and _tracks_a_file(tracked)


def _origin_is_public(slug: Slug) -> bool:
    """The public calling repository itself, or a repository the forge confirms is public."""
    if _is_caller(slug) and caller_is_private() is False:
        return True
    return forge_says_public(slug)


def maw_is_public(maw: Path) -> bool:
    """True only when the maw is the public calling repository or the forge says it is public.

    The maw digest is uploaded beside the prey digest, so a private checkout digested as the maw
    is as exposed as a private prey. A maw with no recognisable origin is unconfirmed, unless it
    is a tracked directory of the public caller's own checkout. Selecting a populated gitlink
    as the maw must not bypass the refusal of local submodule bytes below a public maw.
    """
    # Keep the original path for gitlink junctions, and resolve aliases containing '..'.
    try:
        roots = {maw, maw.resolve()}
    except (OSError, RuntimeError):
        return False
    if any(_tracked_gitlink_checkout(root) for root in roots):
        return False
    if maw_in_public_caller(maw):
        return True
    slug = maw_origin(maw)
    return slug is not None and _origin_is_public(slug)


# Bound the number of populated submodules reported in a refusal.
# Uninitialised gitlinks hold nothing and do not count.
MAX_SUBMODULES = 256


def _gitlinks(directory: Path) -> list[str] | None:
    """The gitlink paths git tracks at or below ``directory``, relative to it; None if unknown."""
    listing = GitRunner(directory).try_run("ls-files", "-s", "-z")
    if listing is None:
        return None
    paths = []
    for entry in listing.split("\0"):
        meta, _, path = entry.partition("\t")
        if meta.startswith(f"{GITLINK_MODE} ") and path:
            paths.append(path)
    return paths


def _populated(place: Path) -> bool:
    """Whether anything sits at a gitlink's path; an empty directory is an uninitialised one."""
    try:
        if place.is_symlink():
            return True
        return place.exists() and (not place.is_dir() or any(place.iterdir()))
    except OSError:
        return True


def unconfirmed_submodules(maw: Path) -> list[str] | None:
    """Populated gitlinks whose local bytes cannot be vouched for by a public remote.

    The inventory walks the maw's directory and reads whatever files sit there, so a checked-out
    submodule reaches the maw digest like the maw's own files, while the caller's visibility says
    nothing about it. Even a public origin cannot attest to untracked, ignored, modified or
    unpublished content. Refuse every populated gitlink, including ones without Git metadata;
    this also blocks anything nested inside it without inspecting another repository.
    Empty (uninitialised) gitlinks hold nothing to digest. Return sorted POSIX paths below the
    maw, or None if they cannot be listed within the bounds.
    """
    links = _gitlinks(maw)
    if links is None:
        return None
    unconfirmed: list[str] = []
    for path in links:
        # A path that did not decode cannot be looked up, so it is not vouched for.
        if chr(0xFFFD) in path or _populated(maw / path):
            unconfirmed.append(path)
            if len(unconfirmed) > MAX_SUBMODULES:
                return None
    return sorted(unconfirmed)


# Independent nested Git checkouts are not gitlinks and may carry private files.
# The inventory also walks untracked directories, so inspect these before digest.
MAX_NESTED_CHECKOUTS = 256


def _tracked_gitlink_checkout(directory: Path) -> bool:
    """Only tracked submodules are handled by unconfirmed_submodules instead."""
    if not GitRunner.available():
        return False
    parent = GitRunner(directory.parent).toplevel()
    if parent is None:
        return False
    try:
        relative = directory.relative_to(parent).as_posix()
    except ValueError:
        return False
    # Git pathspecs remain case-sensitive on Windows even when the filesystem is not.
    # Query literal case variants, then prove filesystem identity to avoid conflating
    # distinct directories on a case-sensitive volume (including Windows ones).
    listing = GitRunner(parent).try_run("ls-files", "-s", "-z", "--", f":(literal,icase){relative}")
    if listing is None:
        return False
    for entry in listing.split("\0"):
        if entry.startswith(f"{GITLINK_MODE} "):
            try:
                if (parent / entry.partition("\t")[2]).samefile(directory):
                    return True
            except OSError:
                continue
    return False


def unconfirmed_nested_checkouts(maw: Path) -> list[str] | None:
    """Find separately checked-out Git repositories below a public maw.

    A public remote does not prove that its local worktree is safe to publish:
    untracked, ignored, modified or unpublished files may still be private.
    Refuse independent Git roots unless allow-private-artifact explicitly opts in.
    Ordinary untracked build output without a Git root is left alone.
    A failed traversal or too many nested checkouts fails closed.
    """
    failures: list[OSError] = []
    unconfirmed: list[str] = []
    examined = 0
    for base, dirs, files in os.walk(maw, topdown=True, followlinks=False, onerror=failures.append):
        # Git treats .GIT / .GiT as .git on case-insensitive workspaces (Windows).
        # Match and prune all case variants before any maw inventory can read them.
        marker = any(name.casefold() == ".git" for name in (*dirs, *files))
        dirs[:] = [name for name in dirs if name.casefold() != ".git"]
        checkout = Path(base)
        if checkout == maw or not marker or _tracked_gitlink_checkout(checkout):
            continue
        examined += 1
        if examined > MAX_NESTED_CHECKOUTS:
            return None
        # Remote visibility says nothing about local bytes read by the maw inventory.
        unconfirmed.append(checkout.relative_to(maw).as_posix())
    if failures:
        return None
    return sorted(unconfirmed)


def _nested_side(maw_label: str, paths: list[str] | None) -> tuple[str, bool]:
    if paths is None:
        return f"the maw ({maw_label}), whose nested checkouts cannot be inspected,", False
    shown = ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
    plural = len(paths) > 1
    noun = "nested checkouts" if plural else "nested checkout"
    return f"the {noun} {shown} inside the maw ({maw_label})", plural


def _submodule_side(maw_label: str, paths: list[str] | None) -> tuple[str, bool]:
    """How the refusal names the submodules of a public maw, and whether that is plural."""
    if paths is None:
        return f"the maw ({maw_label}), whose submodules cannot be listed,", False
    shown = ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
    plural = len(paths) > 1
    noun = "submodules" if plural else "submodule"
    return f"the {noun} {shown} checked out in the maw ({maw_label})", plural


def run() -> int:
    temp = Path(os.environ["RUNNER_TEMP"])
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    # Explicit destinations must be absolute: default maw="." is GITHUB_WORKSPACE,
    # so anchoring a relative output/cache there violates Feeder's source protections.
    output_input = os.environ.get("CRAB_OUT", "")
    if output_input and not Path(output_input).is_absolute():
        print(
            "Feeder action: CRAB_OUT must be an absolute path outside the maw; "
            "leave out empty to use runner temporary storage.",
            file=sys.stderr,
        )
        return 2
    output = output_input or str(temp / f"crab-feeder-{uuid.uuid4().hex}")
    volume = os.environ.get("CRAB_ACQUISITION_VOLUME", "")
    cache_input = os.environ.get("CRAB_CACHE", "")
    if cache_input and not Path(cache_input).is_absolute():
        print(
            "Feeder action: CRAB_CACHE must be an absolute path outside the maw; "
            "leave cache-dir empty to use runner temporary storage.",
            file=sys.stderr,
        )
        return 2
    cache = cache_input or str((Path(volume) if volume else temp) / "hungry-crab")
    maw = Path(os.environ.get("CRAB_MAW", "."))
    if not maw.is_absolute():
        maw = workspace / maw
    prey = os.environ["CRAB_PREY"]
    # uv runs in the trusted action checkout; local prey paths belong to the caller workspace.
    local_prey = workspace / Path(prey).expanduser()
    local = local_prey.is_dir()
    if local:
        prey = str(local_prey.resolve())
    allow = os.environ.get("CRAB_ALLOW_PRIVATE_ARTIFACT", "false").lower()
    if allow not in {"true", "false"}:
        raise ValueError("allow-private-artifact must be true or false")
    # Neither side of the bundle may become a public download unless it is public already:
    # artifacts of a public repository are readable by anyone signed in to GitHub, and the
    # bundle holds the maw digest as well as the prey digest.
    if allow == "false" and caller_is_private() is not True:
        unconfirmed = []
        plural = False
        if not prey_is_public(prey, local=local):
            unconfirmed.append("the prey")
        maw_label = os.environ.get("CRAB_MAW", ".")
        if not maw_is_public(maw):
            unconfirmed.append(f"the maw ({maw_label})")
        else:
            # A public maw still carries whatever its checked-out submodules hold.
            submodules = unconfirmed_submodules(maw)
            if submodules != []:
                side, plural = _submodule_side(maw_label, submodules)
                unconfirmed.append(side)
            # A second actions/checkout is not a gitlink, but its files reach the artifact.
            nested = unconfirmed_nested_checkouts(maw)
            if nested != []:
                side, many = _nested_side(maw_label, nested)
                unconfirmed.append(side)
                plural = plural or many
        if unconfirmed:
            verb = "are" if len(unconfirmed) > 1 or plural else "is"
            sides = f"{' and '.join(unconfirmed)} {verb}"
            print(EXPOSURE_REFUSAL.format(sides=sides), file=sys.stderr)
            return 2
    args = [
        "--cache-dir",
        cache,
        "eat",
        prey,
        "--deterministic",
        "--maw",
        str(maw),
        "--out",
        output,
        "--json",
    ]
    if volume:
        args += ["--acquisition-volume", volume]
    args += [
        "--max-download-bytes",
        os.environ.get("CRAB_MAX_DOWNLOAD_BYTES", "314572800"),
        "--max-acquisition-disk-bytes",
        os.environ.get("CRAB_MAX_ACQUISITION_DISK_BYTES", "1073741824"),
    ]
    for name, flag in [
        ("SINCE", "--since"),
        ("ISSUES", "--issues"),
        ("TOP", "--top"),
        ("MAX_REPO_KB", "--max-repo-kb"),
        ("DEPTH", "--depth"),
    ]:
        args += [flag, os.environ[f"CRAB_{name}"]]
    for name in ("SHALLOW", "WIKI", "ALLOW_LOSS"):
        value = os.environ[f"CRAB_{name}"].lower()
        if value not in {"true", "false"}:
            raise ValueError(f"{name.lower()} must be true or false")
        if name == "SHALLOW" and value == "false":
            args.append("--no-shallow")
        if name == "WIKI" and value == "false":
            args.append("--no-wiki")
        if name == "ALLOW_LOSS" and value == "true":
            args.append("--allow-loss")
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(args)
    if code:
        print(stdout.getvalue(), end="")
        return code
    result = json.loads(stdout.getvalue())
    bundle = Path(result["out_dir"])
    outputs = {
        "bundle-path": str(bundle),
        "menu-path": str(bundle / "menu.json"),
        "candidate-count": str(result["counts"]["total"]),
    }
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            if "\n" in value or "\r" in value:
                raise ValueError("action output paths must not contain newlines")
            handle.write(f"{key}={value}\n")
    summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
    with summary.open("a", encoding="utf-8") as handle:
        handle.write(
            f"## Hungry Crab Feeder\n\n{outputs['candidate-count']} candidates. "
            "Download the meal artifact and open `menu.md`.\n\n"
            "No model, issue creation or ledger writes. The artifact contains "
            "untrusted evidence derived from the prey.\n"
        )
    print(f"Feeder: {outputs['candidate-count']} candidates, bundle at {bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
