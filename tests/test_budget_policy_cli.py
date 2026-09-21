from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hungry_crab.cli import main


@pytest.mark.parametrize("policy", ["enforce", "off"])
def test_budget_policy_flows_from_maw_config_to_digest_and_compare(
    npm_app: Path,
    pyproject_cli: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    policy: str,
) -> None:
    maw = tmp_path / f"maw-{policy}"
    shutil.copytree(pyproject_cli, maw)
    (maw / ".crab.yml").write_text(
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
            str(npm_app),
            "--maw",
            str(maw),
            "--out",
            str(out),
            "--json",
        ]
    )
    assert code == 0
    digest_manifest = json.loads(capsys.readouterr().out)
    assert digest_manifest["budget"]["policy"] == policy

    code = main(
        [
            "-q",
            "--cache-dir",
            str(cache),
            "compare",
            str(npm_app),
            "--maw",
            str(maw),
            "--no-issues",
            "--json",
        ]
    )
    assert code == 0
    capsys.readouterr()

    manifests = [
        json.loads(path.read_text(encoding="utf-8")) for path in cache.rglob("manifest.json")
    ]
    assert len(manifests) >= 2
    assert {manifest["budget"]["policy"] for manifest in manifests} == {policy}
