"""Hypothesis-directed bounded escalation of inert Git/issue evidence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .analysis_context import fingerprint
from .cache import Slug
from .compare.candidates import _evidence_path
from .discovery_search import SearchBudget, SearchClient
from .errors import CrabError
from .fetch.git import GitRunner
from .fetch.issues import slim_issue
from .source_inspection import body_excluded
from .typeutil import as_dict


def history_packet(
    repo: Path,
    sha: str,
    paths: list[str],
    *,
    hypothesis: str,
    reason: str,
    commits: int = 8,
    byte_budget: int = 131_072,
) -> dict[str, Any]:
    if (
        re.fullmatch(r"[a-f0-9]{40,64}", sha) is None
        or not 1 <= len(paths) <= 8
        or any(not _evidence_path(p) for p in paths)
        or type(commits) is not int
        or not 1 <= commits <= 32
        or type(byte_budget) is not int
        or not 1024 <= byte_budget <= 262_144
        or not hypothesis
        or len(hypothesis) > 200
        or not reason
        or len(reason) > 1000
    ):
        raise CrabError("history escalation requires a bounded hypothesis, reason, pin and paths")
    if any(body_excluded(path) for path in paths):
        raise CrabError("history packets exclude README and agent-instruction bodies")
    git = GitRunner(repo)
    ancestry = git.try_run(
        "--no-replace-objects",
        "log",
        "--format=%H",
        f"-n{commits + 1}",
        sha,
        "--",
        *paths,
        timeout=10,
        stdout_limit=4096,
    )
    omissions: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    used = 0
    if ancestry is None:
        omissions.append({"reason": "history-unavailable"})
    else:
        revisions = ancestry.splitlines()
        if len(revisions) > commits:
            omissions.append({"reason": "commit-limit"})
        for revision in revisions[:commits]:
            if re.fullmatch(r"[a-f0-9]{40,64}", revision) is None:
                raise CrabError("history returned an invalid revision identity")
            patch = git.try_run(
                "--no-replace-objects",
                "show",
                "--format=",
                "--no-renames",
                "--no-color",
                "--unified=3",
                revision,
                "--",
                *paths,
                timeout=10,
                stdout_limit=min(65_536, byte_budget - used),
            )
            if patch is None:
                omissions.append({"sha": revision, "reason": "diff-unavailable-or-byte-limit"})
                continue
            size = len(patch.encode("utf-8"))
            used += size
            rows.append(
                {"sha": revision, "patch": patch, "decoded_text_sha256": fingerprint(patch)}
            )
            if byte_budget - used < 1024:
                omissions.append({"reason": "whole-history-byte-limit"})
                break
    result = {
        "schema": "hungry-crab.history-packet/1",
        "hypothesis": hypothesis,
        "reason": reason,
        "source_sha": sha,
        "paths": paths,
        "commits": rows,
        "shallow": git.is_shallow(),
        "omissions": omissions,
        "bytes": used,
        "coverage": "bounded-reachable-history-only",
        "origin": "unknown",
        "license_scope": "resolve-each-selected-path-at-its-historical-revision",
        "untrusted_data": True,
        "donor_executed": False,
        "reviewed": False,
    }
    result["snapshot_sha256"] = fingerprint(result)
    return result


def targeted_issues(
    slug: Slug,
    numbers: list[int],
    *,
    hypothesis: str,
    reason: str,
    budget: SearchBudget,
    client: SearchClient,
) -> dict[str, Any]:
    """Use the existing issue projection, with selected IDs instead of a broad sweep."""
    if (
        slug.host != "github.com"
        or not 1 <= len(numbers) <= 5
        or any(type(n) is not int or n <= 0 for n in numbers)
        or not hypothesis
        or len(hypothesis) > 200
        or not reason
        or len(reason) > 1000
    ):
        raise CrabError("targeted issue acquisition needs 1..5 GitHub IDs and a bounded reason")
    rows, omissions = [], []
    before = budget.used
    for number in dict.fromkeys(numbers):
        budget.charge()
        try:
            raw = as_dict(client.get(f"repos/{slug}/issues/{number}"))
            if raw.get("number") != number:
                raise CrabError("issue identity differs from requested issue")
            rows.append(
                {
                    **slim_issue(raw, via="targeted-hypothesis"),
                    "origin": "commenter",
                    "license_ceiling": "IDEAS_ONLY",
                }
            )
        except CrabError:
            omissions.append({"number": number, "reason": "issue-channel-unavailable"})
    result: dict[str, Any] = {
        "schema": "hungry-crab.targeted-issues/1",
        "hypothesis": hypothesis,
        "reason": reason,
        "repository": str(slug),
        "issues": rows,
        "as_of": datetime.now(UTC).isoformat(),
        "requests": budget.used - before,
        "omissions": omissions,
        "comments": "not-acquired",
        "discussions": "not-acquired",
        "source_commit_scope": "provider-as-of-not-pinned-source",
        "untrusted_data": True,
        "license_ceiling": "IDEAS_ONLY",
        "reviewed": False,
    }
    result["snapshot_sha256"] = fingerprint(result)
    return result
