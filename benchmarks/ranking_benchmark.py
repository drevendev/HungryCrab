"""Synthetic ranking pressure complements the frozen human B1 golden set."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hungry_crab.compare import CompareOptions, compare_digests

ROOT = Path(__file__).parent / "ranking/pressure.json"


def run(*, scoring: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = json.loads(ROOT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="crab-ranking-") as temporary:
        root = Path(temporary)
        for name in ("prey", "maw"):
            path = root / name
            path.mkdir()
            (path / "traits.json").write_text(
                json.dumps({"traits": spec[f"{name}_traits"]}), encoding="utf-8"
            )
            (path / "license.json").write_text('{"spdx":"MIT"}', encoding="utf-8")
            (path / "manifest.json").write_text(
                json.dumps({"prey": {"label": f"synthetic-{name}", "sha": "synthetic-fixture"}}),
                encoding="utf-8",
            )
        comparison = compare_digests(
            root / "prey",
            root / "maw",
            options=CompareOptions(
                maw_license="MIT",
                allow_partial=True,
                top=spec["top"],
                scoring=scoring,
                now=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    keys = [c.key for c in comparison.candidates]
    positions = {key: keys.index(key) + 1 for key in spec["must"] if key in keys}
    missed = [key for key in spec["must"] if key not in keys[: spec["top"]]]
    return {
        "kind": spec["kind"],
        "candidates": len(keys),
        "top": spec["top"],
        "minimum_candidates": spec["minimum_candidates"],
        "positions": positions,
        "missed": missed,
        "passed": len(keys) >= spec["minimum_candidates"] and not missed,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)
