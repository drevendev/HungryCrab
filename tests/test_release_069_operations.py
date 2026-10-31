"""Public launchers, strict scope projection and audited read controls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab.cli import main
from hungry_crab.compare import CompareOptions, compare_digests
from hungry_crab.dependency_scope import exclusions
from hungry_crab.discovery_history import history_packet
from hungry_crab.errors import CrabError
from hungry_crab.launcher import install_launcher
from hungry_crab.miners.ruby_lock import ruby_lock
from hungry_crab.nutrients import Evidence
from hungry_crab.prey_guard import guard_reason


def test_portable_meal_dry_run_never_resolves_or_acquires_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hungry_crab import cli
    from hungry_crab.nutrients import Candidate

    maw = repository(tmp_path / "maw", {"app.py": "VALUE = 1\n", ".crab.yml": "ledger: none\n"})
    meal = tmp_path / "portable"
    meal.mkdir()
    card = Candidate("ci", "ci.cache", "Cache", "Metadata", origin="licensed")
    (meal / "menu.json").write_text(json.dumps({"candidates": [card.to_dict()]}))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("portable preview must not resolve a donor or initialize a provider")

    monkeypatch.setattr(cli, "resolve_target", forbidden)
    monkeypatch.setattr(cli, "GhIssueClient", forbidden)
    assert (
        main(["serve", "--meal-dir", str(meal), "--maw", str(maw), "--as", "dry-run", "--top", "1"])
        == 0
    )
    assert "dry run:" in capsys.readouterr().out


def test_scope_projection_preserves_raw_dependencies_and_original_seeds(tmp_path: Path) -> None:
    roots = {name: tmp_path / name for name in ("prey", "maw")}
    for name, root in roots.items():
        root.mkdir()
        for filename, value in {
            "manifest.json": {"prey": {"label": name, "sha": "a" * 40}},
            "license.json": {"spdx": "MIT"},
            "traits.json": {"traits": {"ecosystems": ["python"]}},
        }.items():
            (root / filename).write_text(json.dumps(value))
    packages = [
        {
            "ecosystem": "python",
            "name": name,
            "kind": kind,
            "manifest": "pyproject.toml",
            "spec": "==1.0",
            "pinned": True,
        }
        for name, kind in (
            ("rich", "runtime"),
            ("rich", "group:docs"),
            ("sphinx", "group:docs"),
        )
    ]
    source = json.dumps({"packages": packages})
    (roots["prey"] / "deps.json").write_text(source)
    rule = {
        "ecosystem": "python",
        "scope": "group:docs",
        "reason": "docs tooling is out of this receiver's scope",
    }
    result = compare_digests(
        roots["prey"],
        roots["maw"],
        options=CompareOptions(
            allow_partial=True, maw_license="MIT", dependency_scope_exclusions=[rule]
        ),
    )
    assert any(c.key == "deps.python.rich" for c in result.candidates)
    assert len(result.menu["recommendation_scope_exclusions"]) == 2
    assert all(
        r["reason"] == rule["reason"] for r in result.menu["recommendation_scope_exclusions"]
    )
    assert len(result.menu["initial_candidates"]) > len(result.candidates)
    assert (roots["prey"] / "deps.json").read_text() == source
    with pytest.raises(CrabError, match="unknown dependency"):
        exclusions([{**rule, "scope": "group:*"}])


def test_owned_unicode_launcher_runs_without_console_executable(tmp_path: Path) -> None:
    path = tmp_path / "краб route.py"
    install_launcher(path)
    install_launcher(path)
    result = subprocess.run(
        [sys.executable, str(path), "--version"], capture_output=True, timeout=20, check=False
    )
    assert result.returncode == 0 and b"crab " in result.stdout
    path.write_bytes(path.read_bytes() + b"# locally edited\n")
    with pytest.raises(CrabError, match="edited"):
        install_launcher(path)
    unrelated = tmp_path / "unowned.py"
    unrelated.write_text("# someone else's script")
    with pytest.raises(CrabError, match="unowned"):
        install_launcher(unrelated)


@pytest.mark.parametrize("tail", ["sort", "sort -n -r | uniq -c", "sort --unique | uniq"])
def test_audited_inert_sort_filters(tmp_path: Path, tail: str) -> None:
    cache = tmp_path / "cache"
    assert guard_reason(f"cat {cache}/prey/code.py | {tail}", root=cache, cwd=tmp_path) is None


@pytest.mark.parametrize(
    "tail",
    [
        "sort -o /tmp/out",
        "sort --compress-program=python",
        "sort --r",
        "sort -nr",
        "sort --temporary-directory=/tmp",
        "uniq IN OUT",
        "uniq --group=prepend",
    ],
)
def test_sort_filters_never_write_execute_or_guess_options(tmp_path: Path, tail: str) -> None:
    cache = tmp_path / "cache"
    assert guard_reason(f"cat {cache}/prey/code.py | {tail}", root=cache, cwd=tmp_path)


def test_native_powershell_git_uses_exact_read_model(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    clone = cache / "github" / "owner" / "tool" / "repo"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "config").write_text("")
    command = f"git -C '{clone}' show HEAD:code.py"
    assert guard_reason(command, root=cache, cwd=tmp_path, shell="powershell") is None
    assert guard_reason(
        command + " --output=/tmp/out", root=cache, cwd=tmp_path, shell="powershell"
    )
    assert guard_reason(command.replace("'", '"'), root=cache, cwd=tmp_path, shell="powershell")


def test_ambiguous_ruby_versions_are_not_invented_and_remote_secrets_are_removed() -> None:
    rows, meta = ruby_lock(
        "GEM\n  remote: https://user:secret@rubygems.org/?token=secret\n"
        "  specs:\n    owned (1.0.0)\n    owned (2.0.0)\nDEPENDENCIES\n  owned\n",
        "Gemfile.lock",
    )
    assert not rows and "multiple-platform-versions" in meta["unsupported"]
    assert meta["source"] == "https://rubygems.org" and "secret" not in json.dumps(meta)


def test_invalid_evidence_role_is_a_controlled_error() -> None:
    with pytest.raises(CrabError, match="evidence role"):
        Evidence.from_dict({"path": "owned.py", "role": []})
    raw = {
        "path": "owned.py",
        "url": None,
        "note": "",
        "role": "primary",
        "message": "why this range matters",
    }
    assert Evidence.from_dict(raw).to_dict() == raw


def test_history_escalation_is_pinned_bounded_and_donor_stays_inert(tmp_path: Path) -> None:
    repo = repository(
        tmp_path / "source", {"app.py": 'raise RuntimeError("never execute donor")\n'}
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    packet = history_packet(
        repo, sha, ["app.py"], hypothesis="cleanup", reason="verify its change history", commits=1
    )
    assert packet["commits"][0]["sha"] == sha
    assert "never execute donor" in packet["commits"][0]["patch"]
    assert not packet["donor_executed"] and not packet["reviewed"]
    with pytest.raises(CrabError, match="instruction bodies"):
        history_packet(repo, sha, ["README.md"], hypothesis="cleanup", reason="irrelevant")
