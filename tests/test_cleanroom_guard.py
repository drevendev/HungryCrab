from __future__ import annotations

import json
import tomllib
from pathlib import Path

from hungry_crab.cleanroom_guard import CLEANROOM_AGENT_TYPE, cleanroom_guard_reason

ROOT = Path(__file__).resolve().parents[1]


def _event(
    tool_input: object,
    *,
    agent_type: str = CLEANROOM_AGENT_TYPE,
    cwd: Path | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {"agent_type": agent_type, "tool_input": tool_input}
    if cwd is not None:
        event["cwd"] = str(cwd)
    return event


def test_cleanroom_agent_denies_default_cache_paths() -> None:
    assert cleanroom_guard_reason(
        _event({"file_path": "~/.cache/hungry-crab/github/acme/prey/repo/src/core.py"})
    )
    assert cleanroom_guard_reason(
        _event({"path": r"C:\Users\alex\.cache\hungry-crab\github\acme\prey\repo"})
    )


def test_cleanroom_agent_denies_default_cache_traversal() -> None:
    path = Path.home() / ".cache" / "tmp" / ".." / "hungry-crab" / "github" / "acme" / "prey"
    assert cleanroom_guard_reason(_event({"file_path": str(path)}))


def test_cleanroom_agent_denies_literal_cache_environment_references() -> None:
    for command in (
        'cat "$CRAB_CACHE_DIR/github/acme/prey/repo/x.py"',
        "rg token ${CRAB_CACHE_DIR}/github/acme/prey/repo",
        r"type %CRAB_CACHE_DIR%\github\acme\prey\repo\x.py",
        r"Get-Content $env:CRAB_CACHE_DIR\github\acme\prey\repo\x.py",
    ):
        assert cleanroom_guard_reason(_event({"command": command}))


def test_cleanroom_agent_denies_configured_cache_root(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    assert cleanroom_guard_reason(
        _event({"query": "needle", "path": str(cache / "github" / "acme" / "prey" / "repo")})
    )


def test_cleanroom_agent_denies_configured_cache_traversal(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    traversed = cache.parent / "other" / ".." / cache.name / "github" / "acme" / "prey"
    assert cleanroom_guard_reason(_event({"path": str(traversed)}))


def test_cleanroom_agent_denies_relative_traversal_against_hook_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache" / "crab-prey"
    cwd = tmp_path / "maw"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    relative = Path("..") / "cache" / "other" / ".." / "crab-prey" / "github" / "acme"
    assert cleanroom_guard_reason(_event({"path": str(relative)}, cwd=cwd))


def test_cleanroom_agent_allows_sibling_with_cache_prefix(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "crab-prey"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    sibling = cache.parent / f"{cache.name}-not-cache" / "README.md"
    assert cleanroom_guard_reason(_event({"file_path": str(sibling)})) is None


def test_cleanroom_hook_runs_the_packaged_guard_through_the_launcher() -> None:
    """The clean-room hook runs this module from the plugin's own ``src/``.

    ``hooks/guard.py`` maps ``cleanroom`` to the module the ``crab-cleanroom-guard`` console
    script names, so the hook and the manual command are one guard; the launcher itself is
    covered in ``tests/test_hooks.py``.
    """
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    handler = hooks["hooks"]["PreToolUse"][0]["hooks"][0]
    assert '"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"' in handler["command"]
    assert '"$g" cleanroom;' in handler["command"]
    assert "args" not in handler
    launcher = (ROOT / "hooks" / "guard.py").read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert '"cleanroom": "hungry_crab.cleanroom_guard"' in launcher
    assert scripts["crab-cleanroom-guard"] == "hungry_crab.cleanroom_guard:main"


def test_cleanroom_agent_may_use_maw_and_spec() -> None:
    assert (
        cleanroom_guard_reason(_event({"file_path": "/work/maw/.crab/specs/crab-tests-fixture.md"}))
        is None
    )


def test_guard_does_not_change_other_agents() -> None:
    assert (
        cleanroom_guard_reason(
            _event(
                {"file_path": "~/.cache/hungry-crab/github/acme/prey/repo/README.md"},
                agent_type="crab:crab-historian",
            )
        )
        is None
    )


def test_cleanroom_agent_fails_closed_when_tool_input_cannot_be_inspected() -> None:
    assert cleanroom_guard_reason(_event(None))
