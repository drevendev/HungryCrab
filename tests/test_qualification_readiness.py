"""Fresh inference must fail before effects when full-host proof is absent."""

from __future__ import annotations

from pathlib import Path

import pytest
from qualification_readiness import check_control, require_readiness, verify_native_meter
from research_host import identity, write


def test_bare_export_is_refused_even_as_engineering_control(tmp_path: Path) -> None:
    profile = {
        "purpose": "engineering-control",
        "control_data": "owned-fixtures",
        "pool": [{"view": str(tmp_path)}],
    }
    with pytest.raises(ValueError, match="compatible inert"):
        require_readiness(profile, tmp_path)


def test_missing_certificate_and_component_pass_cannot_start_fresh_inference(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="full-host readiness"):
        require_readiness({"purpose": "qualification", "pool": []}, tmp_path)
    file = tmp_path / "component.json"
    write(
        file,
        {
            "phase": "complete",
            "usage_complete": True,
            "model_budget_valid": True,
            "seconds": 1,
            "actual_model_tokens": 7000,
            "host_fingerprint": "owned",
            "trial": "owned-baseline-1",
            "outcomes": [],
        },
    )
    import hashlib

    with pytest.raises(ValueError, match="no completed useful"):
        check_control(
            {"path": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()},
            arm="baseline",
            mode="runtime",
            fingerprint="owned",
        )


def test_unknown_purpose_cannot_make_an_old_protocol_fresh_again(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit"):
        require_readiness({"pool": []}, tmp_path)


def test_native_usage_is_reconciled_including_cached_input(tmp_path: Path) -> None:
    request = {"model": "gpt-6.1-sol", "effort": "medium", "prompt": "owned test"}
    write(tmp_path / "step.request.json", request)
    (tmp_path / "step").mkdir()
    result = {
        "status": "completed",
        "usage_complete": True,
        "budget_valid": True,
        "tool_calls": 0,
        "usage": {
            "inputTokens": 7000,
            "outputTokens": 500,
            "cachedInputTokens": 6000,
            "totalTokens": 7500,
        },
    }
    write(tmp_path / "step/result.json", result)
    write(
        tmp_path / "meter.json",
        {
            "usage_complete": True,
            "tokens": 7500,
            "stages": [
                {
                    "name": "step",
                    "state": "settled",
                    "signature": identity(request),
                    "actual_tokens": 7500,
                }
            ],
        },
    )
    assert verify_native_meter(tmp_path, model="gpt-6.1-sol", effort="medium") == 7500
    result["usage"]["totalTokens"] = 1500
    write(tmp_path / "step/result.json", result)
    with pytest.raises(ValueError, match="actual token receipt"):
        verify_native_meter(tmp_path, model="gpt-6.1-sol", effort="medium")


def test_dirty_public_digest_is_rejected_before_engineering_inference(tmp_path: Path) -> None:
    view = tmp_path / "view"
    (view / ".git").mkdir(parents=True)
    digest = tmp_path / "digest"
    digest.mkdir()
    write(
        digest / "manifest.json",
        {
            "prey": {
                "label": "owned/source",
                "url": "https://github.com/owned/source",
                "sha": "a" * 40,
                "worktree": "dirty-hash",
            }
        },
    )
    with pytest.raises(ValueError, match="digest is dirty"):
        require_readiness(
            {
                "purpose": "engineering-control",
                "control_data": "owned-fixtures",
                "pool": [
                    {
                        "view": str(view),
                        "repository": "owned/source",
                        "sha": "a" * 40,
                        "arm_digests": {"baseline": str(digest), "candidate": str(digest)},
                    }
                ],
            },
            tmp_path,
        )
