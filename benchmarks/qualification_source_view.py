"""Bounded inert worktrees for both published and current source readers.

No checkout, hooks, filters, submodules or donor programs run. Git objects remain
immutable shared input; an owned Git root makes sha:./path valid for 0.6.8.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_VIEW_BYTES = 256 * 1024 * 1024
MAX_VIEW_FILES = 30000
MAX_FILE_BYTES = 32 * 1024 * 1024


def git(root: Path, *arguments: str, maximum: int = 4096) -> bytes:
    from hungry_crab.fetch.bounded import run_bounded

    result = run_bounded(
        [
            "git",
            "--no-replace-objects",
            "--no-pager",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.attributesFile=" + os.devnull,
            "-C",
            str(root),
            *arguments,
        ],
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        timeout=180,
        stdout_limit=maximum,
        stderr_limit=8192,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", "replace")[-1000:])
    return result.stdout


def source_view(source: Path, pin: str, destination: Path) -> dict[str, Any]:
    """Materialize regular bytes only, with explicit omissions and finite bounds."""
    import re

    if not re.fullmatch(r"[a-f0-9]{40}", pin):
        raise ValueError("source view requires a complete immutable SHA")
    if git(source, "rev-parse", pin + "^{commit}").decode().strip() != pin:
        raise ValueError("source view pin is unavailable")
    gitdir = Path(git(source, "rev-parse", "--absolute-git-dir").decode().strip())
    destination.mkdir(parents=True, exist_ok=False)
    git(destination, "init", "--quiet", "--template=")
    git(destination, "config", "core.hooksPath", os.devnull)
    git(destination, "config", "core.autocrlf", "false")
    info = destination / ".git/info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_bytes(b"* -export-subst -export-ignore -filter -text\n")
    alternates = destination / ".git/objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_bytes(((gitdir / "objects").as_posix() + "\n").encode())
    if (gitdir / "shallow").exists():
        (destination / ".git/shallow").write_bytes((gitdir / "shallow").read_bytes())
    remote = git(source, "config", "--get", "remote.origin.url").decode().strip()
    git(destination, "config", "remote.origin.url", remote)
    git(destination, "update-ref", "--no-deref", "HEAD", pin)
    archive = git(
        destination, "archive", "--format=tar", pin, maximum=MAX_VIEW_BYTES + 32 * 1024 * 1024
    )
    count = size = 0
    omitted = []
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as reader:
        members = []
        spellings: dict[str, str] = {}
        reserved = {"con", "prn", "aux", "nul"} | {
            stem + str(n) for stem in ("com", "lpt") for n in range(1, 10)
        }
        for member in reader:
            members.append(member)
            if len(members) > 50000:
                raise ValueError("source view archive entry allowance exhausted")
            path = PurePosixPath(member.name)
            if path.is_absolute() or any(p.casefold() in {"..", ".git"} for p in path.parts):
                raise ValueError("unsafe archive path")
            if "\\" in member.name or ":" in member.name:
                raise ValueError("unportable archive path")
            for index, part in enumerate(path.parts):
                if part.rstrip(" .") != part or part.split(".")[0].casefold() in reserved:
                    raise ValueError("unportable source view component")
                prefix = "/".join(path.parts[: index + 1])
                prior = spellings.setdefault(prefix.casefold(), prefix)
                if prior != prefix:
                    raise ValueError("case-colliding source directories")
            if not member.isfile():
                if not member.isdir():
                    omitted.append({"path": member.name, "reason": "non-regular-entry"})
                continue
            key = member.name.casefold()
            if key in seen:
                raise ValueError("case-colliding source paths")
            seen.add(key)
            count += 1
            size += member.size
            if count > MAX_VIEW_FILES or size > MAX_VIEW_BYTES or member.size > MAX_FILE_BYTES:
                raise ValueError("inert source view allowance exhausted")
        for member in members:
            if member.isdir():
                # Git archives include empty gitlink directories. Keep these
                # inert directories so an uninitialized submodule is not
                # misrepresented as a deleted working-tree entry. Never fetch
                # or execute its contents and never create filesystem links.
                destination.joinpath(*PurePosixPath(member.name).parts).mkdir(
                    parents=True, exist_ok=True
                )
                continue
            if not member.isfile():
                continue
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("source view path escapes its owned root")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = reader.extractfile(member)
            assert stream is not None
            with stream, target.open("xb") as out:
                out.write(stream.read())
    git(destination, "read-tree", pin)
    listing = git(destination, "ls-tree", "-r", "-z", pin, maximum=8 * 1024 * 1024)
    for entry in listing.split(b"\0"):
        meta, _, name = entry.partition(b"\t")
        if meta.startswith(b"160000 "):
            omitted.append(
                {
                    "path": name.decode("utf-8", "replace"),
                    "reason": "submodule-content-not-acquired",
                }
            )
    tree = git(destination, "rev-parse", "HEAD^{tree}").decode().strip()
    record = {
        "schema": "crab.qualification-source-view/1",
        "source": str(source.resolve()),
        "root": str(destination.resolve()),
        "sha": pin,
        "tree": tree,
        "regular_files": count,
        "regular_bytes": size,
        "omissions": omitted,
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "donor_executed": False,
    }
    return record


def reader_preflight(python: Path, view: Path, pin: str, path: str) -> dict[str, Any]:
    """Actually import the selected arm's reader; a current-reader proxy is insufficient."""
    program = (
        "from pathlib import Path; import json; import hungry_crab; "
        "from hungry_crab.attribution import GitSourceReader; "
        "text=GitSourceReader(Path("
        + repr(str(view))
        + ")).read("
        + repr(pin)
        + ","
        + repr(path)
        + "); print(json.dumps({'version':hungry_crab.__version__,"
        "'readable':text is not None,"
        "'lines':len(text.splitlines()) if text is not None else None}))"
    )
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", program],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    value = json.loads(result.stdout)
    if value["readable"] is not True:
        raise ValueError("the actual arm reader cannot read this source representation")
    return dict(value)
