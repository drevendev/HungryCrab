"""Menu depth and source-specific coverage diagnostics retain fail-closed behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from hungry_crab.errors import CrabError, UsageError
from hungry_crab.mcp_service import AnalysisService, _require_evidence


def test_deep_menu_can_cover_a_normal_truncated_source(
    npm_app: Path, pyproject_cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        __import__("hungry_crab.miners.inventory", fromlist=["MAX_FILES"]).MAX_FILES, "normal", 5
    )
    service = AnalysisService([npm_app, pyproject_cli], tmp_path / "cache")
    with pytest.raises(CrabError, match=r"prey|truncated") as error:
        service.menu(str(npm_app), str(pyproject_cli))
    assert "truncated" in str(error.value) or "truncated" in (error.value.hint or "")
    result = service.menu(str(npm_app), str(pyproject_cli), depth="deep", limit=1)
    assert result["schema"] == "hungry-crab.mcp-menu/1"
    assert not result["side_effects"]["ledger_written"]
    with pytest.raises(CrabError, match="snapshot"):
        service.menu(str(npm_app), str(pyproject_cli), depth="normal", offset=1)
    with pytest.raises(UsageError, match="depth"):
        service.menu(str(npm_app), str(pyproject_cli), depth="unknown")  # type: ignore[arg-type]


@pytest.mark.parametrize("side", ["prey", "maw"])
def test_refusal_identifies_side_and_visibility_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: str
) -> None:
    monkeypatch.setattr("hungry_crab.mcp_service.digest_integrity_errors", lambda *_: [])
    manifest = {
        "miners": [{"name": "inventory", "ok": True}],
        "coverage": {"healthy": False, "loss": {"truncated": True, "max_files": 5}},
    }
    with pytest.raises(CrabError, match=side) as error:
        _require_evidence(tmp_path, manifest, side=side, depth="normal", kind="menu source")
    assert "truncated at 5" in str(error.value)
    assert 'depth="deep"' in (error.value.hint or "")


def test_refusal_does_not_suggest_depth_for_producer_or_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hungry_crab.mcp_service.digest_integrity_errors", lambda *_: ["damaged artifact"]
    )
    manifest = {"miners": [{"name": "architecture", "ok": False}], "coverage": {"healthy": True}}
    with pytest.raises(CrabError, match=r"architecture.*integrity") as error:
        _require_evidence(tmp_path, manifest, side="maw", depth="deep", kind="menu source")
    assert 'depth="deep"' not in (error.value.hint or "")
