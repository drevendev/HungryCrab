"""Deterministic first leads from metadata and actual filenames, never utility verdicts."""

from __future__ import annotations

import os
import re
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from .attribution import GitSourceReader
from .cache import Slug
from .compare.candidates import _evidence_path
from .errors import CrabError
from .source_find import path_role
from .source_inspection import _commit_pin, body_excluded


def seed_leads(plan: dict[str, Any]) -> dict[str, Any]:
    """Save model planning spend without claiming a mechanism, licence or receiving gap."""
    needs, candidates, limit = plan.get("needs"), plan.get("candidates"), plan.get("limit", 3)
    file_budget = plan.get("source_file_budget", 64)
    cost_weighted = plan.get("cost_weighted", True)
    if (
        not isinstance(needs, list)
        or not 1 <= len(needs) <= 12
        or not isinstance(candidates, list)
        or not 1 <= len(candidates) <= 50
        or type(limit) is not int
        or not 1 <= limit <= 6
        or type(file_budget) is not int
        or not 1 <= file_budget <= 128
        or type(cost_weighted) is not bool
    ):
        raise CrabError("seed preparation requires bounded needs, candidate metadata and limit")
    parsed_needs = []
    seen_needs: set[str] = set()
    for need in needs:
        if not isinstance(need, dict):
            raise CrabError("seed need must be an object")
        identity, queries = need.get("id"), need.get("queries")
        if (
            not isinstance(identity, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", identity) is None
            or identity in seen_needs
            or not isinstance(queries, list)
            or not 1 <= len(queries) <= 4
            or any(not isinstance(q, str) or not 1 <= len(q) <= 256 for q in queries)
        ):
            raise CrabError("seed need requires unique identity and bounded query strings")
        terms = list(dict.fromkeys(re.findall(r"[a-z_][a-z0-9_]{2,47}", " ".join(queries).lower())))
        if not terms:
            raise CrabError("seed query needs at least one bounded literal token")
        seen_needs.add(identity)
        parsed_needs.append((identity, terms[:32]))
    deadline = time.monotonic() + 60
    rows: list[dict[str, Any]] = []
    omissions = []
    seen: set[str] = set()
    for row in candidates:
        if not isinstance(row, dict):
            raise CrabError("seed candidate must be an object")
        repository, sha, repo = row.get("repository"), row.get("sha"), row.get("repo")
        description = row.get("description", "")
        if (
            not isinstance(repository, str)
            or not isinstance(sha, str)
            or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", sha) is None
            or not isinstance(repo, str)
            or not isinstance(description, str)
            or len(description) > 2000
        ):
            raise CrabError("seed candidate needs repository, immutable pin and owned object path")
        if "/" not in repository and repository == Path(repo).name:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", repository) is None:
                raise CrabError("local seed identity must be a bounded repository basename")
            key = "local:" + os.path.normcase(str(Path(repo).resolve()))
        else:
            key = str(Slug.parse(repository)).casefold()
        if key in seen:
            raise CrabError("seed candidate repository identities must be unique")
        seen.add(key)
        reader = GitSourceReader(Path(repo), deadline=deadline)
        raw = (
            reader._bounded_read(
                "--no-replace-objects", "ls-tree", "-r", "-z", sha, stdout_limit=1024 * 1024
            )
            if _commit_pin(reader, sha)
            else None
        )
        if raw is None:
            omissions.append({"repository": repository, "reason": "pin/tree unavailable or limit"})
            continue
        paths = []
        excluded = 0
        for entry in raw.split("\0"):
            if not entry:
                continue
            header, separator, path = entry.partition("\t")
            if (
                not separator
                or header.split(" ", 1)[0] not in {"100644", "100755"}
                or not _evidence_path(path)
                or body_excluded(path)
            ):
                excluded += 1
                continue
            paths.append(path)
        rows.append(
            {
                "repository": repository,
                "sha": sha,
                "description": description,
                "paths": sorted(paths)[:4096],
                "searchable_files_observed": len(paths),
                "paths_not_retained": max(0, len(paths) - 4096),
                "excluded_paths": excluded,
            }
        )
    results = []
    for identity, terms in parsed_needs:
        ranked = []
        for row in rows:
            paths = sorted(
                row["paths"],
                key=lambda p: (
                    path_role(p) == "editor-metadata",
                    path_role(p) != "source",
                    -sum(t in p.casefold() for t in terms),
                    p,
                ),
            )
            scores = {t: sum(t in p.casefold() for p in paths[:24]) for t in terms}
            selected_terms = sorted(terms, key=lambda t: (-scores[t], -len(t), t))[:5]
            metadata_hits = sum(t in row["description"].casefold() for t in terms)
            path_hits = sum(any(t in p.casefold() for p in paths[:8]) for t in terms)
            association = metadata_hits + path_hits
            observed = row["searchable_files_observed"]
            ranked.append(
                {
                    "repository": row["repository"],
                    "sha": row["sha"],
                    "terms": selected_terms,
                    "paths": paths[:8],
                    "literal_association": association,
                    "bounded_read_association": {
                        "numerator": association * min(file_budget, observed),
                        "denominator": max(1, observed),
                        "file_budget": file_budget,
                        "searchable_files_observed": observed,
                        "interpretation": "Coverage/cost heuristic, not behavior or usefulness",
                    },
                    "paths_not_retained": row["paths_not_retained"],
                    "excluded_paths": row["excluded_paths"],
                    "reason": "Literal metadata/filename association; inspect actual behavior next",
                }
            )
        ranked.sort(
            key=lambda r: (
                -Fraction(
                    r["bounded_read_association"]["numerator"],
                    r["bounded_read_association"]["denominator"],
                )
                if cost_weighted
                else -Fraction(r["literal_association"]),
                -r["literal_association"],
                r["repository"],
                r["sha"],
            )
        )
        results.append(
            {"need": identity, "leads": ranked[:limit], "not_selected": len(ranked[limit:])}
        )
    return {
        "schema": "hungry-crab.discovery-seeds/1",
        "needs": results,
        "omissions": omissions,
        "provider_requests": 0,
        "model_calls": 0,
        "source_bodies_read": False,
        "usefulness": "unknown; no hypothesis or novelty verdict",
        "license_permission": "not-established",
        "identity_scope": "Available objects; provider/repository binding needs verification",
        "selection": (
            "Literal association weighted by the bounded-read fraction of observed eligible "
            "files, then raw association and identity. A large tree is not a usefulness verdict; "
            "unselected candidates remain unreviewed and raw inputs remain necessary."
        )
        if cost_weighted
        else "Owner-selected raw literal association, then repository identity",
        "cost_weighted": cost_weighted,
        "limits": {"seconds": 60, "tree_bytes_each": 1024 * 1024, "paths_each": 4096},
    }
