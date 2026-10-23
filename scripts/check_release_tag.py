"""Fail closed unless the release tag being published names the packaged release version.

`publish.yml` runs this on the checked-out tag before building: the tag must be `vX.Y.Z`,
equal to `v` + `[project].version` in `pyproject.toml`, and that version must not be a
development version. Usage: `TAG=v0.6.2 python scripts/check_release_tag.py`.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

_RELEASE_TAG = re.compile(r"v\d{1,9}\.\d{1,9}\.\d{1,9}")


def tag_problem(tag: str, version: str) -> str | None:
    """Why ``tag`` may not publish ``version``, or None when it may."""
    if ".dev" in version:
        return f"{version} is a development version, not a release"
    if not _RELEASE_TAG.fullmatch(tag):
        return f"{tag!r} is not a release tag of the form vX.Y.Z"
    if tag != f"v{version}":
        return f"tag {tag} does not name the packaged version {version}"
    return None


def main() -> int:
    tag = os.environ.get("TAG", "")
    try:
        with Path("pyproject.toml").open("rb") as handle:
            version = str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"cannot read [project].version from pyproject.toml: {exc}", file=sys.stderr)
        return 1
    problem = tag_problem(tag, version)
    if problem is not None:
        print(f"refusing to publish: {problem}", file=sys.stderr)
        return 1
    print(f"publishing hungry-crab {version} from {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
