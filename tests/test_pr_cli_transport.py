from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from hungry_crab.errors import CrabError, UsageError
from hungry_crab.serve import load_cleanroom_receipts

TRACE = "implemented from a specification, without access to the prey source"


def _receipt(nutrient_id: str, path: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "nutrient_id": nutrient_id,
            "changed_paths": [path],
            "summary": TRACE,
            "checks": ["pytest -q"],
        }
    )


def test_receipt_stream_binds_multiple_documents_by_nutrient_id() -> None:
    first = _receipt("crab:ci:ci.cache", ".github/workflows/ci.yml")
    second = _receipt("crab:tests:tests.smoke", "tests/test_smoke.py")

    loaded = load_cleanroom_receipts(f"\n{first}\n\n{second}\n")

    assert set(loaded) == {"crab:ci:ci.cache", "crab:tests:tests.smoke"}
    assert json.loads(loaded["crab:ci:ci.cache"])["changed_paths"] == [".github/workflows/ci.yml"]


def test_receipt_stream_rejects_duplicate_nutrient_receipts() -> None:
    receipt = _receipt("crab:ci:ci.cache", ".github/workflows/ci.yml")

    with pytest.raises(UsageError, match="duplicate receipt"):
        load_cleanroom_receipts(f"{receipt}\n{receipt}")


def test_receipt_stream_preserves_strict_receipt_validation() -> None:
    ambiguous = (
        '{"version":1,"nutrient_id":"crab:ci:ci.cache",'
        '"nutrient_id":"crab:ci:other","changed_paths":["ci.yml"],'
        f'"summary":{json.dumps(TRACE)},"checks":["pytest -q"]}}'
    )

    with pytest.raises(CrabError, match="invalid clean-room implementation receipt"):
        load_cleanroom_receipts(ambiguous)


def test_empty_receipt_stream_fails_closed() -> None:
    with pytest.raises(CrabError, match="receipts on stdin"):
        load_cleanroom_receipts(" \n\t")


def test_pr_branch_cli_threads_configured_provider_identity(monkeypatch, tmp_path) -> None:
    from hungry_crab import cli

    token_env = "CRAB_APP_TOKEN"
    config = SimpleNamespace(
        serve=SimpleNamespace(token_env=token_env),
        ledger_path=lambda _cache_dir: tmp_path / "ledger.json",
    )
    ledger = object()
    captured: dict[str, object] = {}

    prey_clone = tmp_path / "prey"
    monkeypatch.setattr(
        cli, "resolve_target", lambda _prey: SimpleNamespace(path=prey_clone, slug=None)
    )
    monkeypatch.setattr(cli.MawConfig, "load", lambda _maw: config)
    monkeypatch.setattr(cli.Ledger, "load", lambda *_args, **_kwargs: ledger)
    monkeypatch.setattr(cli, "meal_for", lambda *_args, **_kwargs: tmp_path / "meal")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    class RecordingClient:
        def __init__(self, *, token_env: str = "") -> None:
            captured["token_env"] = token_env

    monkeypatch.setattr(cli, "GhIssueClient", RecordingClient)

    def recording_serve(
        _meal_dir,
        _maw,
        options,
        *,
        config,
        ledger,
        client,
        log,
        prey_repo,
    ):
        del config, ledger, log
        captured["mode"] = options.mode
        captured["client"] = client
        captured["prey_repo"] = prey_repo
        return cli.ServeReport(mode=options.mode, maw=str(tmp_path))

    monkeypatch.setattr(cli, "serve", recording_serve)

    args = argparse.Namespace(
        prey="owner/prey",
        maw=tmp_path,
        cache_dir=None,
        ids="crab:ci:ci.cache",
        top=None,
        mode="pr-branch",
        notes=None,
        json=True,
    )

    assert cli.cmd_serve(args, lambda _message: None) == 0
    assert captured["mode"] == "pr-branch"
    assert captured["token_env"] == token_env
    assert isinstance(captured["client"], RecordingClient)
    # a COPY receipt's source paths are checked against the prey's own clone
    assert captured["prey_repo"] == prey_clone
