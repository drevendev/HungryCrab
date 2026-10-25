from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_optional_installer_environment_has_no_inherited_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from install_smoke import installer_environment, run

    canaries = [
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "NPM_TOKEN",
        "NODE_OPTIONS",
        "NPM_CONFIG_USERCONFIG",
    ]
    for key in canaries:
        monkeypatch.setenv(key, "SYNTHETIC_CREDENTIAL_CANARY")
    environment = installer_environment(tmp_path)
    output = run(
        [sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"],
        cwd=tmp_path,
        env=environment,
    )
    received = json.loads(output)
    assert "SYNTHETIC_CREDENTIAL_CANARY" not in output
    assert all(key not in received for key in canaries if key != "NPM_CONFIG_USERCONFIG")
    assert Path(received["HOME"]).is_relative_to(tmp_path)
    assert Path(received["NPM_CONFIG_CACHE"]).is_relative_to(tmp_path)
    assert received["DO_NOT_TRACK"] == "1" and received["DISABLE_TELEMETRY"] == "1"
    assert received["NPM_CONFIG_IGNORE_SCRIPTS"] == "true"
    assert received["GIT_CONFIG_GLOBAL"] == os.devnull


def test_covered_executable_dependencies_are_pinned_and_check_rejects_regressions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from check_supply_chain import errors

    assert errors() == []
    shutil.copytree(ROOT / ".github" / "workflows", tmp_path / ".github" / "workflows")
    shutil.copyfile(ROOT / "action.yml", tmp_path / "action.yml")
    shutil.copytree(
        ROOT / "scripts" / "skills-installer", tmp_path / "scripts" / "skills-installer"
    )
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text(encoding="utf-8")
    text = text.replace(
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@master"
    )
    text = text.replace("persist-credentials: false", "persist-credentials: true")
    workflow.write_text(text, encoding="utf-8")
    assert any("commit SHA" in problem for problem in errors(tmp_path))
    assert any("persisted credentials" in problem for problem in errors(tmp_path))
