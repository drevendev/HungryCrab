"""An owned Python script route for installations whose console shim is blocked."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from .artifact_read import read_bytes
from .config_edit import atomic_text
from .errors import CrabError
from .fs import is_link_stat

MARKER = "# HungryCrab owned interpreter launcher v1\n"


def install_launcher(path: Path) -> Path:
    """Write our exact launcher; no PATH edits, shell quoting or App Control changes."""
    # Resolving a venv's Python symlink selects the base interpreter on Unix,
    # losing the installed package. Preserve the trusted running interpreter route.
    interpreter = Path(sys.executable).absolute()
    if not interpreter.is_file():
        raise CrabError("running interpreter is unavailable for the owned launcher")
    # repr produces a Python string literal, never shell command text.
    literal = repr(str(interpreter))
    content = (
        MARKER
        + "import subprocess, sys\n"
        + f"command = [{literal}, '-m', 'hungry_crab', *sys.argv[1:]]\n"
        + "raise SystemExit(subprocess.call(command))\n"
    )
    path = path.expanduser().absolute()
    if path.suffix.lower() != ".py":
        raise CrabError("owned launcher destination must end in .py")
    parent = path.parent
    if not parent.is_dir() or is_link_stat(parent.lstat()):
        raise CrabError("owned launcher requires an existing regular destination directory")
    if path.exists() or path.is_symlink():
        if is_link_stat(path.lstat()):
            raise CrabError("refusing a linked launcher destination")
        current = read_bytes(path, 16_384).decode("utf-8")
        if not current.startswith(MARKER):
            raise CrabError("refusing to overwrite an unowned launcher")
        # A marker alone cannot authorize replacing arbitrary edited content.
        try:
            receipt = json.loads(read_bytes(path.with_suffix(".py.owner.json"), 16_384))
        except (ValueError, CrabError) as exc:
            raise CrabError("existing launcher has no valid ownership receipt") from exc
        if receipt.get("sha256") != hashlib.sha256(current.encode()).hexdigest():
            raise CrabError("existing owned launcher was edited")
    elif (
        path.with_suffix(".py.owner.json").exists()
        or path.with_suffix(".py.owner.json").is_symlink()
    ):
        raise CrabError("refusing an existing unowned launcher receipt")

    atomic_text(path, content)
    atomic_text(
        path.with_suffix(".py.owner.json"),
        json.dumps(
            {
                "schema": "hungry-crab.launcher/1",
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "interpreter": str(interpreter),
            },
            indent=2,
        )
        + "\n",
    )
    return path
