"""Trusted isolated bootstrap recording calls into explicitly selected receiving code."""

from __future__ import annotations

import hashlib  # noqa: F401 -- preload stdlib before explicit receiving import paths
import json
import runpy
import sys
from pathlib import Path
from types import FrameType


def main() -> None:
    root, script, output, requirements, imports = sys.argv[1:]
    receiving = Path(root).resolve()
    required = json.loads(requirements)
    expected = {(str((receiving / r["path"]).resolve()), r["qualified_name"]) for r in required}
    observed: set[tuple[str, str]] = set()
    filenames: dict[str, str] = {}

    def profile(frame: FrameType, event: str, arg: object) -> None:
        if event != "call":
            return
        name = frame.f_code.co_filename
        if name not in filenames:
            filenames[name] = str(Path(name).resolve())
        key = (filenames[name], frame.f_code.co_qualname)
        if key in expected:
            observed.add(key)

    if imports == "1":
        sys.path[:0] = [str(receiving), str(receiving / "src")]
    sys.argv = [script]
    sys.setprofile(profile)
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        sys.setprofile(None)
        Path(output).write_text(
            json.dumps(
                [
                    r
                    for r in required
                    if (str((receiving / r["path"]).resolve()), r["qualified_name"]) in observed
                ]
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
