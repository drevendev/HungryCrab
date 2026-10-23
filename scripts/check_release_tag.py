"""Fail closed unless the release tag being published names the packaged release version.

`publish.yml` runs this on the checked-out tag before building: the tag must be `vX.Y.Z`,
equal to `v` + `[project].version` in `pyproject.toml`, that version must not be a
development version, and the run must have been started from that tag (`GITHUB_REF`, when
set). Usage: `TAG=v0.6.2 python scripts/check_release_tag.py`.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

_RELEASE_TAG = re.compile(r"v\d{1,9}\.\d{1,9}\.\d{1,9}")


def tag_problem(tag: str, version: str, ref: str | None = None) -> str | None:
    """Why ``tag`` may not publish ``version``, or None when it may.

    ``ref`` is the ref the workflow run was started from (``GITHUB_REF``), when known: a release
    event runs on its tag, and a manual run has to be started on the tag it publishes, which is
    also the only ref the ``pypi`` environment accepts.
    """
    if ".dev" in version:
        return f"{version} is a development version, not a release"
    if not _RELEASE_TAG.fullmatch(tag):
        return f"{tag!r} is not a release tag of the form vX.Y.Z"
    if tag != f"v{version}":
        return f"tag {tag} does not name the packaged version {version}"
    if ref is not None and ref != f"refs/tags/{tag}":
        return f"the run was started from {ref}, not from refs/tags/{tag}"
    return None


def main() -> int:
    tag = os.environ.get("TAG", "")
    ref = os.environ.get("GITHUB_REF") or None
    try:
        with Path("pyproject.toml").open("rb") as handle:
            version = str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"cannot read [project].version from pyproject.toml: {exc}", file=sys.stderr)
        return 1
    problem = tag_problem(tag, version, ref)
    if problem is not None:
        print(f"refusing to publish: {problem}", file=sys.stderr)
        return 1
    print(f"publishing hungry-crab {version} from {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
