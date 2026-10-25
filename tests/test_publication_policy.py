from __future__ import annotations

import json
import traceback
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fixture_builder import git
from test_publication_safety import GITHUB

from hungry_crab.errors import CrabError
from hungry_crab.pr_publication import (
    CleanroomImplementationReceipt,
    GeneratedFile,
    HandoffFile,
    PreparedPullRequest,
    PublicationHandoff,
    generated_files_from_handoff,
    load_cleanroom_implementation_receipt,
    load_publication_handoff,
    publication_handoff_from_receipt,
    publish_prepared_pull_request,
)
from hungry_crab.publication_policy import parse_publication_allow
from hungry_crab.publication_safety import scan_publication


@pytest.mark.parametrize(
    "failure",
    ["missing", "changed", "duplicate", "invalid-sha", "receipt-duplicate", "duplicate-key"],
)
def test_secret_filenames_never_escape_early_receipt_or_handoff_errors(failure: str) -> None:
    path = f"generated/{GITHUB}.txt"
    handoff = {
        "version": 1,
        "nutrient_id": "crab:tests:unit",
        "files": [{"path": path, "sha256": "a" * 64}],
    }
    with pytest.raises(CrabError) as raised:
        if failure == "missing":
            publication_handoff_from_receipt(
                CleanroomImplementationReceipt("crab:tests:unit", (path,), "summary", ("pytest",)),
                {},
            )
        elif failure == "changed":
            generated_files_from_handoff(
                "crab:tests:unit",
                PublicationHandoff("crab:tests:unit", (HandoffFile(path, "a" * 64),)),
                lambda _: "changed",
            )
        elif failure == "receipt-duplicate":
            load_cleanroom_implementation_receipt(
                json.dumps(
                    {
                        "version": 1,
                        "nutrient_id": "crab:tests:unit",
                        "changed_paths": [path, path],
                        "summary": "implemented from a specification, "
                        "without access to the prey source",
                        "checks": ["pytest"],
                    }
                )
            )
        elif failure == "duplicate-key":
            load_publication_handoff(json.dumps({path: 1})[:-1] + f',"{path}":2}}')
        else:
            if failure == "duplicate":
                handoff["files"] *= 2
            else:
                handoff["files"][0]["sha256"] = "invalid"
            load_publication_handoff(json.dumps(handoff))
    rendered = str(raised.value) + (raised.value.hint or "")
    assert GITHUB not in rendered and "redacted" in rendered
    assert GITHUB not in "".join(traceback.format_exception(raised.value))


def _policy(root: Path, finding: str) -> None:
    (root / ".crab.yml").write_text(
        yaml.safe_dump(
            {
                "serve": {
                    "publication_allow": [
                        {
                            "finding": finding,
                            "reason": "Reviewed synthetic credential for an isolated fixture",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_override_is_exact_current_content_bound_and_maw_owned(tmp_path: Path) -> None:
    root = tmp_path / "maw"
    root.mkdir()
    content = f"credential={GITHUB}\n"
    finding = scan_publication("fixture.txt", content)[0]
    _policy(root, finding.identity)
    prepared = PreparedPullRequest(
        "Fixture", "Review fixture", (GeneratedFile("fixture.txt", content),), root
    )
    effects: list[str] = []

    def callback(_: PreparedPullRequest) -> None:
        effects.append("published")

    with pytest.raises(CrabError, match="committed policy"):
        publish_prepared_pull_request(prepared, callback)
    assert not effects
    git(root, "init", "-b", "main")
    git(root, "add", ".crab.yml")
    git(root, "commit", "-m", "test: owner-reviewed exact finding")
    publish_prepared_pull_request(prepared, callback)
    assert effects == ["published"]
    with pytest.raises(CrabError, match="possible secret"):
        publish_prepared_pull_request(
            replace(prepared, files=(GeneratedFile("fixture.txt", content + "changed\n"),)),
            callback,
        )
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(CrabError, match="possible secret"):
        publish_prepared_pull_request(replace(prepared, policy_root=other), callback)
    (root / ".crab.yml").write_text("serve: {}\n", encoding="utf-8")
    with pytest.raises(CrabError, match="possible secret"):
        publish_prepared_pull_request(prepared, callback)
    assert effects == ["published"]


@pytest.mark.parametrize(
    "entry",
    [
        {"finding": "*", "reason": "all"},
        {"finding": "a" * 64, "reason": ""},
        {"path": "*", "reason": "all"},
        {"finding": "a" * 64, "reason": GITHUB},
    ],
)
def test_no_wildcard_blank_reason_or_secret_override(entry: dict[str, str]) -> None:
    with pytest.raises(CrabError):
        parse_publication_allow([entry])
