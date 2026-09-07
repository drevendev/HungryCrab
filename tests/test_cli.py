from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import copy_repo

from hungry_crab import __version__
from hungry_crab.cli import build_parser, detect_maw_license, main
from hungry_crab.licensing.matrix import Relationship


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
    code = main(["-q", "digest", str(npm_app), "--out", str(out), "--maw-license", "MIT"])
    assert code == 0
    stdout = capsys.readouterr().out
    assert "Digest of npm-app@" in stdout
    assert "inventory.md" in stdout
    assert "miners: 12 ok, 0 failed" in stdout
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


def test_compare_and_menu_commands(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = str(tmp_path / "cache")
    maw = copy_repo(pyproject_cli, tmp_path / "maw")
    code = main(
        ["-q", "--cache-dir", cache, "compare", str(npm_app), "--maw", str(maw), "--no-issues"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("Menu: npm-app@")
    assert "crab:tooling:tooling.dependabot" in out
    assert "meal written to" in out
    assert (maw / ".crab" / "ledger.json").is_file(), "ledger mode repo by default"

    code = main(
        [
            "-q",
            "--cache-dir",
            cache,
            "menu",
            str(npm_app),
            "--maw",
            str(maw),
            "--top",
            "3",
            "--category",
            "ci",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip() and line.strip()[0].isdigit()]
    assert 1 <= len(lines) <= 3
    assert all("crab:ci:" in line for line in lines)

    code = main(
        [
            "-q",
            "--cache-dir",
            cache,
            "menu",
            str(npm_app),
            "--maw",
            str(maw),
            "--json",
            "--top",
            "2",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "hungry-crab.menu/1"
    assert len(payload["candidates"]) == 2


def test_init_ledger_serve_and_tune_commands(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = str(tmp_path / "cache")
    maw = copy_repo(pyproject_cli, tmp_path / "maw")
    assert main(["init", "--maw", str(maw)]) == 0
    assert "wrote" in capsys.readouterr().out
    assert main(["init", "--maw", str(maw)]) == 1, "refuses to overwrite"
    capsys.readouterr()
    assert (
        main(
            [
                "-q",
                "--cache-dir",
                cache,
                "compare",
                str(npm_app),
                "--maw",
                str(maw),
                "--no-issues",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["ledger", "--maw", str(maw)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Ledger for maw:")
    assert "proposed" in out and "crab:ci:ci.cache" in out
    assert (
        main(
            [
                "ledger",
                "--maw",
                str(maw),
                "mark",
                "crab:ci:ci.cache",
                "rejected",
                "--reason",
                "no",
            ]
        )
        == 0
    )
    assert "crab:ci:ci.cache: rejected (no)" in capsys.readouterr().out
    assert main(["ledger", "--maw", str(maw), "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    statuses = {e["id"]: e["status"] for e in payload["entries"]}
    assert statuses["crab:ci:ci.cache"] == "rejected"

    code = main(
        ["-q", "--cache-dir", cache, "serve", str(npm_app), "--maw", str(maw), "--top", "2"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "dry run: 1 issue(s) would be created" in out, "the rejected one is skipped"
    assert "skipped crab:ci:ci.cache: ledger: rejected" in out
    assert "<!-- crab:" in out
    code = main(
        [
            "-q",
            "--cache-dir",
            cache,
            "serve",
            str(npm_app),
            "--maw",
            str(maw),
            "--ids",
            "crab:ci:ci.cache",
        ]
    )
    assert code == 0
    assert "skipped crab:ci:ci.cache: ledger: rejected" in capsys.readouterr().out
    code = main(
        [
            "-q",
            "--cache-dir",
            cache,
            "serve",
            str(npm_app),
            "--maw",
            str(maw),
            "--top",
            "1",
            "--as",
            "pr-branch",
        ]
    )
    assert code == 1
    assert "0.3" in capsys.readouterr().err

    assert main(["tune", "--maw", str(maw)]) == 0
    out = capsys.readouterr().out
    assert "1 decisions in the ledger" in out
    assert main(["tune", "--maw", str(maw), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decisions"] == 1 and payload["written"] is None


def test_menu_before_compare_is_an_error(
    dotnet_lib: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["-q", "--cache-dir", str(tmp_path / "cache"), "menu", str(dotnet_lib)])
    assert code == 1
    err = capsys.readouterr().err
    assert "no menu" in err and "crab compare" in err


def test_detect_maw_license(npm_app: Path, pyproject_cli: Path) -> None:
    assert detect_maw_license(npm_app) == "MIT"
    assert detect_maw_license(pyproject_cli) == "Apache-2.0"


def _stub_sniff(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]) -> None:
    """Record what ``cmd_sniff`` asks for, and keep the GitHub API out of the test."""
    from hungry_crab import cli
    from hungry_crab.sniff import build_report

    repo = {
        "description": "A prey repository",
        "default_branch": "main",
        "size": 20_000,
        "pushed_at": "2025-05-20T10:00:00Z",
        "created_at": "2020-01-01T00:00:00Z",
        "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
    }

    def fake_sniff(slug: object, **kwargs: object) -> object:
        captured.update(kwargs)
        captured["slug"] = slug
        return build_report(
            slug,  # type: ignore[arg-type]
            repo,
            {},
            maw_license=kwargs.get("maw_license"),  # type: ignore[arg-type]
            relationship=kwargs.get("relationship", Relationship.FOREIGN),  # type: ignore[arg-type]
            now=datetime(2025, 6, 1, tzinfo=UTC),
        )

    monkeypatch.setattr(cli, "sniff", fake_sniff)
    monkeypatch.setattr(cli, "GitHubClient", lambda **_: object())


def test_sniff_needs_no_maw(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `crab sniff owner/repo` is the first command of the eat protocol and the plainest form the
    # design documents show. It once crashed on `Path(None)` because --maw defaults to None here
    # and nowhere else, and every invocation the project ships passes `--maw .`.
    captured: dict[str, object] = {}
    _stub_sniff(monkeypatch, captured)
    assert main(["sniff", "example/prey"]) == 0
    assert captured["maw_license"] is None
    assert captured["relationship"] == Relationship.FOREIGN
    assert "verdict" in capsys.readouterr().out


def test_sniff_with_only_a_maw_license(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # --maw-license is documented as overriding --maw detection, so it has to work alone.
    captured: dict[str, object] = {}
    _stub_sniff(monkeypatch, captured)
    assert main(["sniff", "example/prey", "--maw-license", "GPL-3.0-only"]) == 0
    assert captured["maw_license"] == "GPL-3.0-only"
    assert captured["relationship"] == Relationship.FOREIGN
    assert "GPL-3.0-only" in capsys.readouterr().out


def test_sniff_with_a_maw_directory_detects_its_license(
    monkeypatch: pytest.MonkeyPatch, pyproject_cli: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}
    _stub_sniff(monkeypatch, captured)
    assert main(["sniff", "example/prey", "--maw", str(pyproject_cli)]) == 0
    assert captured["maw_license"] == "Apache-2.0"
    capsys.readouterr()


def test_sniff_with_a_missing_maw_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}
    _stub_sniff(monkeypatch, captured)
    assert main(["sniff", "example/prey", "--maw", str(tmp_path / "nowhere")]) == 2
    assert "is not a directory" in capsys.readouterr().err
