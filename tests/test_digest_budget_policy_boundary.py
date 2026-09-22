from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hungry_crab.cli import main


@pytest.mark.parametrize("policy", ["enforce", "off"])
def test_local_prey_cannot_supply_digest_budget_policy(
    npm_app: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    policy: str,
) -> None:
    prey = tmp_path / f"prey-{policy}"
    shutil.copytree(npm_app, prey)
    (prey / ".crab.yml").write_text(
        f"budget:\n  policy: {policy}\n",
        encoding="utf-8",
    )
    cache = tmp_path / f"cache-{policy}"
    out = tmp_path / f"digest-{policy}"

    code = main(
        [
            "-q",
            "--cache-dir",
            str(cache),
            "digest",
            str(prey),
            "--out",
            str(out),
            "--json",
        ]
    )

    assert code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["budget"]["policy"] == "warn"
