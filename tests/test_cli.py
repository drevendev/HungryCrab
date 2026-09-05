from __future__ import annotations

import json
from pathlib import Path

import pytest

from hungry_crab import __version__
from hungry_crab.cli import build_parser, detect_host_license, main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"crab {__version__}"


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "sniff" in capsys.readouterr().out


def test_every_subcommand_has_help() -> None:
    parser = build_parser()
    text = parser.format_help()
    for command in ("sniff", "catch", "digest", "cache", "version"):
        assert command in text


def test_bad_repository_reference_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = main(["--cache-dir", str(tmp_path), "catch", "not a slug"])
    assert code == 2
    err = capsys.readouterr().err
    assert "crab: error:" in err
    assert "crab: hint:" in err


def test_cache_path_and_empty_listing(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["--cache-dir", str(tmp_path), "cache", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path)
    assert main(["--cache-dir", str(tmp_path), "cache", "ls"]) == 0
    assert "cache is empty" in capsys.readouterr().out
    assert main(["--cache-dir", str(tmp_path), "cache", "rm", "owner/repo"]) == 0
    assert "not cached" in capsys.readouterr().out


def test_digest_of_local_fixture_prints_summary(
    npm_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "digest"
    code = main(["-q", "digest", str(npm_app), "--out", str(out), "--host-license", "MIT"])
    assert code == 0
    stdout = capsys.readouterr().out
    assert "Digest of npm-app@" in stdout
    assert "inventory.md" in stdout
    assert "miners: 10 ok, 0 failed" in stdout
    assert (out / "manifest.json").is_file()


def test_digest_json_output(
    npm_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "digest"
    code = main(
        ["-q", "digest", str(npm_app), "--out", str(out), "--json", "--miners", "inventory"]
    )
    assert code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["prey"]["label"] == "npm-app"
    assert [m["name"] for m in manifest["miners"]] == ["inventory"]


def test_unknown_miner_is_reported(
    npm_app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["-q", "digest", str(npm_app), "--out", str(tmp_path / "d"), "--miners", "bogus"])
    assert code == 1
    assert "unknown miner" in capsys.readouterr().err


def test_detect_host_license(npm_app: Path, pyproject_cli: Path) -> None:
    assert detect_host_license(npm_app) == "MIT"
    assert detect_host_license(pyproject_cli) == "Apache-2.0"
