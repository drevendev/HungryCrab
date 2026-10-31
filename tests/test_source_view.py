"""Token-bounded evidence views retain raw authority, code ranges and continuations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.source_inspection import source_packet
from hungry_crab.source_view import evidence_page
from hungry_crab.tokens import estimate_tokens


def test_source_pages_prioritize_code_and_preserve_large_notice_data(tmp_path: Path) -> None:
    repo = repository(tmp_path / "donor", {"cache.py": "def refresh(value):\n    return value\n"})
    sha = git(repo, "rev-parse", "HEAD").strip()
    payload = source_packet(repo, sha, ["cache.py"], [], need="source", related=False)
    notice = "Original test-only notice row.\n" * 300
    payload["retained_notice"] = notice
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    page = evidence_page(payload, budget=512)
    all_rows = []
    for _ in range(200):
        assert page["estimated_tokens"] <= 512
        assert (
            estimate_tokens(json.dumps(page, ensure_ascii=False, indent=2))
            == page["estimated_tokens"]
        )
        assert page["payload_sha256"] == hashlib.sha256(canonical).hexdigest()
        assert page["raw_artifact_required"] and not page["permission_from_view"]
        all_rows.extend(page["records"])
        if page["next_cursor"] is None:
            break
        page = evidence_page(payload, budget=512, cursor=page["next_cursor"])
    else:
        pytest.fail("bounded evidence did not terminate its continuation")
    chunks = [row for row in all_rows if row["kind"] == "source"]
    assert chunks and chunks[0]["start"] == 1
    assert "".join(row["content"] for row in chunks) == payload["files"][0]["ranges"][0]["content"]
    assert (
        "".join(row["value"] for row in all_rows if row.get("pointer") == "/retained_notice")
        == notice
    )
    assert page["end_of_projection"] and page["semantic_review"] == "not-established"


def test_cursor_cannot_silently_switch_payload_or_budget() -> None:
    payload = {"schema": "hungry-crab.source-index/1", "files": [], "note": "x" * 5000}
    first = evidence_page(payload, budget=512)
    assert first["next_cursor"] is not None
    with pytest.raises(CrabError, match="cursor"):
        evidence_page({**payload, "note": "changed"}, budget=512, cursor=first["next_cursor"])
    with pytest.raises(CrabError, match="cursor"):
        evidence_page(payload, budget=1024, cursor=first["next_cursor"])
    reordered = dict(reversed(list(payload.items())))
    assert evidence_page(reordered, budget=512, cursor=first["next_cursor"]) == evidence_page(
        payload, budget=512, cursor=first["next_cursor"]
    )


def test_trailing_blank_source_lines_keep_their_actual_bounds() -> None:
    payload = {
        "schema": "hungry-crab.source-packet/1",
        "files": [
            {
                "path": "app.py",
                "sha": "a" * 40,
                "ranges": [{"start": 10, "end": 11, "content": "value = 1\n"}],
            }
        ],
    }
    result = evidence_page(payload)
    chunk = next(row for row in result["records"] if row["kind"] == "source")
    assert (chunk["start"], chunk["end"]) == (10, 11)
    assert chunk["content"] == "value = 1\n"


def test_projection_keeps_instruction_bodies_out_and_overlong_lines_explicit() -> None:
    payload = {
        "schema": "hungry-crab.source-packet/1",
        "files": [
            {
                "path": "README.md",
                "sha": "a" * 40,
                "ranges": [{"start": 1, "end": 1, "content": "FORGED_README_SENTINEL"}],
            },
            {
                "path": "app.py",
                "sha": "a" * 40,
                "ranges": [{"start": 7, "end": 7, "content": "x" * 10000}],
            },
        ],
    }
    page = evidence_page(payload)
    assert "FORGED_README_SENTINEL" not in json.dumps(page)
    assert page["this_page_has_omissions"]
    reasons = [row.get("reason") for row in page["records"]]
    assert "instruction-readme-body-excluded" in reasons
    assert "source-line-page-budget" in reasons


def test_index_selected_packets_and_public_existing_artifact_view(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repository(
        tmp_path / "donor",
        {
            "pkg/cache.py": "# padding\n" * 80 + "def cache_refresh():\n    return 1\n",
        },
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    plan = tmp_path / "packet-plan.json"
    plan.write_text(
        json.dumps(
            {"repo": str(repo), "sha": sha, "need": "cache", "terms": ["cache"], "related": False}
        )
    )
    assert main(["discover", "packet", "--input", str(plan)]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["navigation"]["files"][0]["path"] == "pkg/cache.py"
    assert packet["files"][0]["ranges"][0]["start"] == 81
    artifact = tmp_path / "packet.json"
    artifact.write_text(json.dumps(packet))
    view = tmp_path / "view.json"
    view.write_text(json.dumps({"artifact": str(artifact), "budget": 1800}))
    assert main(["discover", "view", "--input", str(view)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert any(row.get("content", "").startswith("def cache_refresh") for row in result["records"])
    assert json.loads(artifact.read_text()) == packet


def test_truncated_declaration_window_does_not_hide_its_real_end(tmp_path: Path) -> None:
    repo = repository(tmp_path / "donor", {"app.py": "def long_body():\n" + "    x = 1\n" * 100})
    sha = git(repo, "rev-parse", "HEAD").strip()
    packet = source_packet(
        repo, sha, ["app.py"], [{"kind": "function"}], need="bounded", related=False
    )
    window = packet["files"][0]["ranges"][0]
    assert window["end"] == 60 and window["node_end"] == 101 and window["range_truncated"]
    view = evidence_page(packet)
    assert any(row.get("window_truncated") is True for row in view["records"])
    continued = source_packet(
        repo,
        sha,
        ["app.py"],
        [],
        need="continuation",
        related=False,
        ranges=[{"path": "app.py", "start": 61, "end": 90, "kind": "forged-syntax"}],
    )
    excerpt = continued["files"][0]["ranges"][0]
    assert excerpt["start"] == 61 and excerpt["end"] == 90 and "kind" not in excerpt
    assert continued["files"][0]["analysis"] == "not-run-explicit-source-ranges"
    unavailable = source_packet(
        repo,
        sha,
        ["app.py"],
        [],
        need="range limit",
        related=False,
        ranges=[{"path": "app.py", "start": 200, "end": 210}],
    )
    assert not unavailable["files"][0]["ranges"] and unavailable["omissions"]


def test_projection_rejects_non_json_numeric_constants() -> None:
    with pytest.raises(CrabError, match="JSON"):
        evidence_page({"schema": "hungry-crab.source-index/1", "files": [], "value": float("nan")})


@pytest.mark.parametrize(
    "record",
    [None, {}, {"path": "x.py", "sha": "a", "ranges": [{}]}],
)
def test_projection_refuses_malformed_file_records(record: object) -> None:
    with pytest.raises(CrabError, match="structured source"):
        evidence_page({"schema": "hungry-crab.source-packet/1", "files": [record]})


@pytest.mark.parametrize("budget", [True, 0, 3510])
def test_invalid_projection_budget_is_refused(budget: int) -> None:
    with pytest.raises(CrabError):
        evidence_page({"schema": "hungry-crab.source-index/1", "files": []}, budget=budget)
