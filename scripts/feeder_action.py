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


def maw_in_public_caller(maw: Path) -> bool:
    """A tracked directory of the public calling repository's own checkout (a monorepo package).

    The caller's checkout sits at the workspace root; a directory below it holding files the
    caller tracks is part of that public repository. An untracked or ignored directory is not:
    it may hold anything a previous step wrote there.
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
    tracked = GitRunner(root).try_run("ls-files", "-z", "--", relative.as_posix() or ".")
    return bool(tracked)


def maw_is_public(maw: Path) -> bool:
    """True only when the maw is the public calling repository or the forge says it is public.

    The maw digest is uploaded beside the prey digest, so a private checkout digested as the maw
    is as exposed as a private prey. A maw with no recognisable origin is unconfirmed, unless it
    is a tracked directory of the public caller's own checkout.
    """
    if maw_in_public_caller(maw):
        return True
    slug = maw_origin(maw)
    if slug is None:
        return False
    caller = os.environ.get("GITHUB_REPOSITORY", "")
    if (
        slug.host == "github.com"
        and f"{slug.owner}/{slug.repo}".casefold() == caller.casefold()
        and caller_is_private() is False
    ):
        return True
    return forge_says_public(slug)


def run() -> int:
    temp = Path(os.environ["RUNNER_TEMP"])
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    output = os.environ.get("CRAB_OUT") or str(temp / f"crab-feeder-{uuid.uuid4().hex}")
    cache = os.environ.get("CRAB_CACHE") or str(temp / "hungry-crab")
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
        if not prey_is_public(prey, local=local):
            unconfirmed.append("the prey")
        if not maw_is_public(maw):
            unconfirmed.append(f"the maw ({os.environ.get('CRAB_MAW', '.')})")
        if unconfirmed:
            verb = "are" if len(unconfirmed) > 1 else "is"
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
