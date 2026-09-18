from __future__ import annotations

from hungry_crab.cleanroom_guard import CLEANROOM_AGENT_TYPE, cleanroom_guard_reason


def _event(tool_input: object, *, agent_type: str = CLEANROOM_AGENT_TYPE) -> dict[str, object]:
    return {"agent_type": agent_type, "tool_input": tool_input}


def test_cleanroom_agent_denies_default_cache_paths() -> None:
    assert cleanroom_guard_reason(
        _event({"file_path": "~/.cache/hungry-crab/github/acme/prey/repo/src/core.py"})
    )
    assert cleanroom_guard_reason(
        _event({"path": r"C:\Users\alex\.cache\hungry-crab\github\acme\prey\repo"})
    )


def test_cleanroom_agent_denies_literal_cache_environment_references() -> None:
    for command in (
        'cat "$CRAB_CACHE_DIR/github/acme/prey/repo/x.py"',
        "rg token ${CRAB_CACHE_DIR}/github/acme/prey/repo",
        r"type %CRAB_CACHE_DIR%\github\acme\prey\repo\x.py",
        r"Get-Content $env:CRAB_CACHE_DIR\github\acme\prey\repo\x.py",
    ):
        assert cleanroom_guard_reason(_event({"command": command}))


def test_cleanroom_agent_denies_configured_cache_root(monkeypatch) -> None:
    monkeypatch.setenv("CRAB_CACHE_DIR", "/tmp/crab-prey")
    assert cleanroom_guard_reason(
        _event({"query": "needle", "path": "/tmp/crab-prey/github/acme/prey/repo"})
    )


def test_cleanroom_agent_may_use_maw_and_spec() -> None:
    assert (
        cleanroom_guard_reason(
            _event({"file_path": "/work/maw/.crab/specs/crab-tests-fixture.md"})
        )
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
