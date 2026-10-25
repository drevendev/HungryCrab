"""Committed maw-owned, content-bound exceptions to individual publication findings."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .errors import CrabError
from .fetch.git import GitRunner
from .publication_safety import PublicationFinding, scan_publication


def parse_publication_allow(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 100:
        raise CrabError("serve.publication_allow must be a list of at most 100 findings")
    identities: list[str] = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"finding", "reason"}:
            raise CrabError("each publication override requires only finding and reason")
        identity, reason = entry["finding"], entry["reason"]
        if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
            raise CrabError("publication override finding must be an exact SHA-256 identity")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 500
            or scan_publication("<override reason>", reason)
        ):
            raise CrabError(
                "publication override requires a non-secret reason of 1..500 characters"
            )
        if identity in identities:
            raise CrabError("duplicate publication override identity")
        identities.append(identity)
    return tuple(identities)


def current_publication_allow(root: Path) -> tuple[str, ...]:
    """Only overrides committed in this maw's HEAD and unchanged on disk are effective.

    This is supervised publication policy, not a sandbox against an agent with Git write access.
    Scheduled sessions still require independent host enforcement (#243).
    """
    from .maw import MawConfig

    current = MawConfig.load(root).serve.publication_allow
    if not current:
        return ()
    committed = GitRunner(root).try_run("--no-replace-objects", "show", "HEAD:.crab.yml")
    try:
        data = yaml.safe_load(committed or "")
        serve = data.get("serve", {}) if isinstance(data, dict) else {}
        approved = parse_publication_allow(serve.get("publication_allow", []))
    except (ValueError, yaml.YAMLError, AttributeError) as exc:
        raise CrabError("cannot verify committed publication overrides") from exc
    if current != approved:
        raise CrabError("publication overrides must match the current maw's committed policy")
    return current


def unapproved_findings(
    findings: list[PublicationFinding], allowed: tuple[str, ...]
) -> list[PublicationFinding]:
    return [finding for finding in findings if finding.identity not in allowed]
