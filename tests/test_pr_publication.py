from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from hungry_crab.errors import CrabError
from hungry_crab.pr_publication import (
    GeneratedFile,
    PreparedPullRequest,
    PullRequestPublication,
    dump_publication_handoff,
    generated_files_from_handoff,
    load_cleanroom_implementation_receipt,
    load_publication_handoff,
    nutrient_branch_name,
    nutrient_spec_path,
    publication_handoff_from_receipt,
    publication_items,
    publish_prepared_pull_request,
    publish_prepared_transaction,
)


def _prepared(
    *, title: str = "feat: carry the nutrient", body: str = "<!-- crab:ci:cache -->"
) -> PreparedPullRequest:
    return PreparedPullRequest(
        title=title,
        body=body,
        files=(GeneratedFile("generated/cache.yml", "cache: true\n"),),
    )


def _handoff_payload(
    *,
    nutrient_id: str = "crab:ci:cache",
    files: list[dict[str, str]] | None = None,
) -> str:
    if files is None:
        content = "cache: true\n"
        files = [
            {
                "path": "generated/cache.yml",
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        ]
    return json.dumps({"version": 1, "nutrient_id": nutrient_id, "files": files})


def _receipt_payload(
    *,
    version: object = 1,
    nutrient_id: str = "crab:ci:cache",
    changed_paths: list[str] | None = None,
    summary: str = "implemented from a specification, without access to the prey source",
    checks: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "version": version,
            "nutrient_id": nutrient_id,
            "changed_paths": changed_paths or ["generated/cache.yml"],
            "summary": summary,
            "checks": checks or ["pytest -q"],
        }
    )


def test_cleanroom_receipt_builds_exact_handoff_without_dirty_tree_discovery() -> None:
    content = "cache: true\n"
    receipt = load_cleanroom_implementation_receipt(_receipt_payload())
    maw = {
        "generated/cache.yml": content,
        "unrelated/dirty.txt": "must never be inferred\n",
    }

    handoff = publication_handoff_from_receipt(receipt, maw.__getitem__)
    round_tripped = load_publication_handoff(dump_publication_handoff(handoff))
    generated = generated_files_from_handoff("crab:ci:cache", round_tripped, maw.__getitem__)

    assert [declared.path for declared in handoff.files] == ["generated/cache.yml"]
    assert handoff.files[0].sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert generated == (GeneratedFile("generated/cache.yml", content),)


@pytest.mark.parametrize("version", [True, 1.0], ids=["boolean", "float"])
def test_cleanroom_receipt_rejects_non_integer_version(version: object) -> None:
    with pytest.raises(CrabError, match="invalid clean-room implementation receipt") as raised:
        load_cleanroom_implementation_receipt(_receipt_payload(version=version))

    assert raised.value.hint == f"unsupported receipt version: {version!r}"


def test_cleanroom_receipt_rejects_duplicate_json_member() -> None:
    payload = (
        '{"version":1,"version":1,"nutrient_id":"crab:ci:cache",'
        '"changed_paths":["generated/cache.yml"],'
        '"summary":"implemented from a specification, without access to the prey source",'
        '"checks":["pytest -q"]}'
    )

    with pytest.raises(CrabError, match="invalid clean-room implementation receipt") as raised:
        load_cleanroom_implementation_receipt(payload)

    assert raised.value.hint == "duplicate JSON object member: version"


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "generated/../secret.txt",
        "/tmp/secret.txt",
        "C:/tmp/secret.txt",
        r"generated\secret.txt",
    ],
)
def test_cleanroom_receipt_rejects_noncanonical_or_outside_maw_paths(path: str) -> None:
    with pytest.raises(CrabError, match="invalid clean-room implementation receipt") as raised:
        load_cleanroom_implementation_receipt(_receipt_payload(changed_paths=[path]))

    assert raised.value.hint == "changed paths must be canonical maw-relative POSIX paths"


def test_cleanroom_receipt_rejects_duplicate_paths_and_missing_trace() -> None:
    with pytest.raises(CrabError) as duplicate:
        load_cleanroom_implementation_receipt(
            _receipt_payload(changed_paths=["generated/cache.yml", "generated/cache.yml"])
        )
    assert duplicate.value.hint == "duplicate changed path: generated/cache.yml"

    with pytest.raises(CrabError) as missing_trace:
        load_cleanroom_implementation_receipt(_receipt_payload(summary="implemented cleanly"))
    assert missing_trace.value.hint == "summary must contain the clean-room trace sentence"


def test_cleanroom_receipt_fails_closed_when_declared_file_is_missing() -> None:
    receipt = load_cleanroom_implementation_receipt(_receipt_payload())

    with pytest.raises(CrabError) as raised:
        publication_handoff_from_receipt(receipt, {}.__getitem__)

    assert raised.value.hint == "declared changed file is missing: generated/cache.yml"


def test_handoff_freezes_only_declared_files_with_matching_content_identity() -> None:
    content = "cache: true\n"
    handoff = load_publication_handoff(_handoff_payload())
    maw = {
        "generated/cache.yml": content,
        "unrelated/dirty.txt": "must never be inferred\n",
    }

    generated = generated_files_from_handoff("crab:ci:cache", handoff, maw.__getitem__)

    assert generated == (GeneratedFile("generated/cache.yml", content),)


def test_handoff_rejects_nutrient_mismatch_before_reading_maw() -> None:
    handoff = load_publication_handoff(_handoff_payload(nutrient_id="crab:ci:other"))
    reads: list[str] = []

    with pytest.raises(CrabError, match="invalid publication handoff") as raised:
        generated_files_from_handoff(
            "crab:ci:cache",
            handoff,
            lambda path: reads.append(path) or "cache: true\n",
        )

    assert reads == []
    assert raised.value.hint == "handoff nutrient_id does not match the publication nutrient"


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "generated/../secret.txt",
        "/tmp/secret.txt",
        "C:/tmp/secret.txt",
        r"generated\secret.txt",
    ],
)
def test_handoff_rejects_noncanonical_or_outside_maw_paths(path: str) -> None:
    payload = _handoff_payload(files=[{"path": path, "sha256": "0" * 64}])

    with pytest.raises(CrabError, match="invalid publication handoff") as raised:
        load_publication_handoff(payload)

    assert raised.value.hint == "file paths must be canonical maw-relative POSIX paths"


def test_handoff_rejects_duplicate_paths() -> None:
    declared = {"path": "generated/cache.yml", "sha256": "0" * 64}
    payload = _handoff_payload(files=[declared, declared.copy()])

    with pytest.raises(CrabError, match="invalid publication handoff") as raised:
        load_publication_handoff(payload)

    assert raised.value.hint == "duplicate handoff path: generated/cache.yml"


def test_handoff_rejects_duplicate_root_object_member() -> None:
    digest = "0" * 64
    payload = (
        '{"version":1,"version":1,"nutrient_id":"crab:ci:cache","files":['
        f'{{"path":"generated/cache.yml","sha256":"{digest}"}}]}}'
    )

    with pytest.raises(CrabError, match="invalid publication handoff") as raised:
        load_publication_handoff(payload)

    assert raised.value.hint == "duplicate JSON object member: version"


def test_handoff_rejects_duplicate_file_object_member() -> None:
    digest = "0" * 64
    payload = (
        '{"version":1,"nutrient_id":"crab:ci:cache","files":['
        '{"path":"generated/cache.yml","path":"generated/other.yml",'
        f'"sha256":"{digest}"}}]}}'
    )

    with pytest.raises(CrabError, match="invalid publication handoff") as raised:
        load_publication_handoff(payload)

    assert raised.value.hint == "duplicate JSON object member: path"


@pytest.mark.parametrize("version", [True, 1.0], ids=["boolean", "float"])
def test_handoff_rejects_non_integer_version(version: object) -> None:
    payload = json.dumps(
        {
            "version": version,
            "nutrient_id": "crab:ci:cache",
            "files": [{"path": "generated/cache.yml", "sha256": "0" * 64}],
        }
    )

    with pytest.raises(CrabError, match="invalid publication handoff") as raised:
        load_publication_handoff(payload)

    assert raised.value.hint == f"unsupported handoff version: {version!r}"


def test_handoff_rejects_missing_or_stale_declared_file() -> None:
    handoff = load_publication_handoff(_handoff_payload())

    with pytest.raises(CrabError) as missing:
        generated_files_from_handoff("crab:ci:cache", handoff, {}.__getitem__)
    assert missing.value.hint == "declared file is missing: generated/cache.yml"

    with pytest.raises(CrabError) as stale:
        generated_files_from_handoff("crab:ci:cache", handoff, lambda _path: "cache: changed\n")
    assert stale.value.hint == "declared file changed after handoff: generated/cache.yml"


def test_prepared_pull_request_scans_files_title_and_body() -> None:
    prepared = PreparedPullRequest(
        title="feat: safe title",
        body="<!-- crab:ci:cache -->\nSafe trace body.\n",
        files=(
            GeneratedFile("generated/cache.yml", "cache: true\n"),
            GeneratedFile("generated/tooling.toml", "enabled = true\n"),
        ),
    )

    assert list(publication_items(prepared)) == [
        ("generated/cache.yml", "cache: true\n"),
        ("generated/tooling.toml", "enabled = true\n"),
        ("PR_TITLE", "feat: safe title"),
        ("PR_BODY", "<!-- crab:ci:cache -->\nSafe trace body.\n"),
    ]


@pytest.mark.parametrize(
    ("prepared", "location"),
    [
        (
            PreparedPullRequest(
                title="feat: safe title",
                body="<!-- crab:ci:cache -->",
                files=(
                    GeneratedFile(
                        "generated/settings.yml",
                        "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n",
                    ),
                ),
            ),
            "generated/settings.yml:1 (github-token)",
        ),
        (
            _prepared(title="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
            "PR_TITLE:1 (github-token)",
        ),
        (
            _prepared(body="token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
            "PR_BODY:1 (github-token)",
        ),
    ],
)
def test_secret_hit_blocks_before_any_publication_effect(
    prepared: PreparedPullRequest, location: str
) -> None:
    effects: list[str] = []
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    def publish(_: PreparedPullRequest) -> str:
        effects.extend(["branch", "write", "push", "pull-request"])
        return "https://github.com/example/maw/pull/7"

    with pytest.raises(CrabError) as raised:
        publish_prepared_pull_request(prepared, publish)

    assert effects == []
    assert raised.value.hint == location
    assert secret not in raised.value.message
    assert secret not in (raised.value.hint or "")


def test_clean_prepared_pull_request_reaches_effect_once() -> None:
    prepared = _prepared(body="<!-- crab:ci:cache -->\nTrace: public metadata only.\n")
    effects: list[dict[str, Any]] = []

    def publish(payload: PreparedPullRequest) -> str:
        effects.append({"title": payload.title, "files": len(payload.files)})
        return "https://github.com/example/maw/pull/7"

    url = publish_prepared_pull_request(prepared, publish)

    assert url == "https://github.com/example/maw/pull/7"
    assert effects == [{"title": "feat: carry the nutrient", "files": 1}]


def test_nutrient_branch_name_is_stable_safe_and_distinct() -> None:
    branch = nutrient_branch_name("crab:ci:cache/key")

    assert branch == nutrient_branch_name("crab:ci:cache/key")
    assert branch.startswith("crab/ci-cache-key-")
    assert ":" not in branch
    assert "/" not in branch.removeprefix("crab/")
    assert branch != nutrient_branch_name("crab:ci:cache-key")


def test_nutrient_spec_path_is_legal_everywhere_and_stable(tmp_path: Path) -> None:
    """`.crab/specs/<nutrient-id>.md` had colons in it, which NTFS refuses (#131)."""
    path = nutrient_spec_path("crab:ci:ci.cache")

    assert path == nutrient_spec_path("crab:ci:ci.cache")
    assert path.startswith(".crab/specs/ci-ci.cache-") and path.endswith(".md")
    assert ":" not in path and "\\" not in path
    # the same readable part and hash as the branch, so the two are recognisably one nutrient
    slug = path.removeprefix(".crab/specs/").removesuffix(".md")
    assert slug == nutrient_branch_name("crab:ci:ci.cache").removeprefix("crab/")
    assert path != nutrient_spec_path("crab:ci:ci-cache")

    # creatable on this platform, whichever it is: the Windows leg of CI is the point
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("spec\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "spec\n"
    with pytest.raises(CrabError, match="crab nutrient id"):
        nutrient_spec_path("ci.cache")


def test_transaction_reconciles_existing_pr_without_publication_effects() -> None:
    prepared = _prepared(body="<!-- crab:ci:cache -->\nTrace: safe.\n")
    effects: list[str] = []

    def publish(_: str, __: PreparedPullRequest) -> str:
        effects.extend(["branch", "write", "push", "pull-request"])
        return "https://github.com/example/maw/pull/8"

    result = publish_prepared_transaction(
        "crab:ci:cache",
        prepared,
        lambda: {
            "crab:ci:cache": {
                "number": 7,
                "url": "https://github.com/example/maw/pull/7",
                "state": "open",
            }
        },
        publish,
    )

    assert result == PullRequestPublication(
        branch=nutrient_branch_name("crab:ci:cache"),
        url="https://github.com/example/maw/pull/7",
        created=False,
    )
    assert effects == []


def test_transaction_uses_deterministic_branch_for_new_pr() -> None:
    prepared = _prepared(body="<!-- crab:ci:cache -->\nTrace: safe.\n")
    effects: list[tuple[str, str]] = []

    def publish(branch: str, payload: PreparedPullRequest) -> str:
        effects.append((branch, payload.title))
        return "https://github.com/example/maw/pull/9"

    result = publish_prepared_transaction("crab:ci:cache", prepared, dict, publish)

    expected_branch = nutrient_branch_name("crab:ci:cache")
    assert result == PullRequestPublication(
        branch=expected_branch,
        url="https://github.com/example/maw/pull/9",
        created=True,
    )
    assert effects == [(expected_branch, "feat: carry the nutrient")]


def test_transaction_secret_hit_prevents_reconcile_and_all_effects() -> None:
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    prepared = _prepared(
        body="<!-- crab:ci:cache -->\nTrace: safe.\n",
        title=secret,
    )
    calls: list[str] = []

    def list_marked_prs() -> dict[str, dict[str, object]]:
        calls.append("reconcile")
        return {}

    def publish(_: str, __: PreparedPullRequest) -> str:
        calls.extend(["branch", "write", "push", "pull-request"])
        return "https://github.com/example/maw/pull/9"

    with pytest.raises(CrabError) as raised:
        publish_prepared_transaction("crab:ci:cache", prepared, list_marked_prs, publish)

    assert calls == []
    assert secret not in raised.value.message
    assert secret not in (raised.value.hint or "")


def test_transaction_requires_direct_marker_before_provider_read() -> None:
    prepared = _prepared(body="Trace only; marker is missing.\n")
    calls: list[str] = []

    def list_marked_prs() -> dict[str, dict[str, object]]:
        calls.append("reconcile")
        return {}

    with pytest.raises(CrabError, match="must open with its nutrient marker"):
        publish_prepared_transaction(
            "crab:ci:cache",
            prepared,
            list_marked_prs,
            lambda _branch, _payload: "https://github.com/example/maw/pull/9",
        )

    assert calls == []


def test_transaction_refuses_ambiguous_existing_pr_without_url() -> None:
    prepared = _prepared(body="<!-- crab:ci:cache -->\nTrace: safe.\n")
    effects: list[str] = []

    with pytest.raises(CrabError, match="refusing to create a duplicate"):
        publish_prepared_transaction(
            "crab:ci:cache",
            prepared,
            lambda: {"crab:ci:cache": {"number": 7, "url": None}},
            lambda _branch, _payload: effects.append("publish") or "unexpected",
        )

    assert effects == []
