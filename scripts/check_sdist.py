"""Fail closed if the published source archive has missing CLI sources or uncollectable tests.

The full test suite runs on the checked-out repository in CI; it depends on the
plugin/hook/benchmark fixture layout and must not silently be shipped half-complete.
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path, PurePosixPath


def verify_sdist(archive: Path) -> None:
    with tarfile.open(archive, mode="r:gz") as source:
        members = [PurePosixPath(member.name) for member in source.getmembers() if member.isfile()]
    if not members:
        raise ValueError("sdist is empty")
    roots = {member.parts[0] for member in members if member.parts}
    if len(roots) != 1:
        raise ValueError(f"sdist must have one package root, got {sorted(roots)}")

    relative = {PurePosixPath(*member.parts[1:]).as_posix() for member in members}
    required = {
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "NOTICE.md",
        "THIRD_PARTY_NOTICES.md",
        "CODE_OF_CONDUCT.md",
        "src/hungry_crab/__init__.py",
        "src/hungry_crab/data/report.css",
    }
    missing = required - relative
    if missing:
        raise ValueError(f"sdist lacks required files: {sorted(missing)}")
    unexpected_tests = sorted(
        name for name in relative if name == "tests" or name.startswith("tests/")
    )
    if unexpected_tests:
        raise ValueError(
            f"sdist ships tests without their repository fixtures: {unexpected_tests[:5]}"
        )


def main() -> int:
    archives = sorted(Path("dist").glob("hungry_crab-*.tar.gz"))
    if len(archives) != 1:
        print(
            f"expected exactly one HungryCrab sdist in dist/, got {len(archives)}",
            file=sys.stderr,
        )
        return 1
    try:
        verify_sdist(archives[0])
    except (OSError, tarfile.TarError, ValueError) as exc:
        print(f"invalid HungryCrab source distribution: {exc}", file=sys.stderr)
        return 1
    print(f"sdist contents verified: {archives[0].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
