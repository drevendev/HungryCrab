"""Persisted bounded discovery rounds. Semantic work belongs to the invoked host.

This owns the queue/budget, not a second acceptance ledger. Reviewed decisions
and behavior cards remain in the existing per-meal exploration transactions.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .analysis_context import fingerprint, installed_source_identity, receiving_context
from .artifact_read import read_object
from .attribution import GitSourceReader
from .config_edit import atomic_text
from .digest import worktree_fingerprint
from .errors import CrabError
from .exploration import report as exploration_report
from .fetch.git import GitRunner
from .file_lock import state_lock
from .ledger import Ledger
from .maw import MawConfig

SCHEMA = "hungry-crab.discovery-session/1"
LIMITS = {
    "requests": 16,
    "donors": 6,
    "hypotheses": 12,
    "replacement_rounds": 2,
    "model_tokens": 30_000,
    "seconds": 2700,
}
OUTCOMES = {
    "verified-gap",
    "already-present",
    "duplicate",
    "not-useful",
    "license-unresolved",
    "unsupported",
    "budget-deferred",
}


def _text(value: object, field: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CrabError(f"discovery {field} must be bounded nonempty text")
    return value


def live_context(maw: Path) -> dict[str, Any]:
    config = MawConfig.load(maw)
    git = GitRunner(config.root)
    if not git.is_repo_root() or not git.has_commits():
        raise CrabError("discovery requires a receiving Git root with commits")
    worktree = worktree_fingerprint(git, config.root)
    if worktree == "unknown":
        raise CrabError("discovery receiving worktree is unknown")
    ledger = Ledger.load(config.ledger_path(), maw=config.root.name)
    return receiving_context(
        config.root,
        {"prey": {"sha": git.head_sha(), "worktree": worktree}},
        config.raw,
        ledger.to_dict(),
    )


def _references(refs: object, context: dict[str, Any]) -> None:
    if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
        raise CrabError("receiving need requires individual source/test references")
    reader = GitSourceReader(Path(context["root"]))
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("sha") != context["sha"]:
            raise CrabError("receiving need reference has a different revision")
        path = ref.get("path")
        start, end = ref.get("start"), ref.get("end")
        if (
            not isinstance(path, str)
            or any(p in {".", "..", ".git", ""} for p in path.split("/"))
            or "\\" in path
            or ":" in path
        ):
            raise CrabError("unsafe receiving need evidence path")
        text = reader.read(context["sha"], path)
        if (
            type(start) is not int
            or type(end) is not int
            or text is None
            or not 1 <= start <= end <= min(len(text.splitlines()), start + 1000)
        ):
            raise CrabError("receiving need range is unavailable")


def initialize(
    directory: Path, maw: Path, plan: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    instant = now or datetime.now(UTC)
    if len(json.dumps(plan, ensure_ascii=False).encode()) > 256_000:
        raise CrabError("discovery plan exceeds its byte budget")
    context = live_context(maw)
    if directory.resolve().is_relative_to(maw.resolve()):
        raise CrabError("discovery state must be outside the receiving worktree")
    if plan.get("receiving_context") != context:
        raise CrabError("discovery plan must match the current receiving context")
    _text(plan.get("goal"), "goal")
    limits = dict(LIMITS)
    requested = plan.get("limits", {})
    if not isinstance(requested, dict) or set(requested) - set(limits):
        raise CrabError("unknown discovery limits")
    for name, limit_value in requested.items():
        if type(limit_value) is not int or not 1 <= limit_value <= LIMITS[name]:
            raise CrabError(f"discovery {name} exceeds its hard limit")
        limits[name] = limit_value
    needs = plan.get("needs")
    if not isinstance(needs, list) or not 1 <= len(needs) <= limits["hypotheses"]:
        raise CrabError("discovery needs exceed hypothesis bounds")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for need in needs:
        if not isinstance(need, dict):
            raise CrabError("discovery need must be an object")
        identity = _text(need.get("id"), "need.id", 100)
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", identity) is None or identity in seen:
            raise CrabError("receiving need identity must be unique and stable")
        seen.add(identity)
        for key in ("flow", "expected", "observed", "impact", "constraints"):
            _text(need.get(key), "need." + key)
        _references(need.get("maw_evidence"), context)
        backlog = need.get("backlog")
        if not isinstance(backlog, dict) or backlog.get("status") not in {
            "checked",
            "partial",
            "unavailable",
        }:
            raise CrabError("receiving need requires explicit current/open-work check coverage")
        _text(backlog.get("as_of"), "backlog.as_of", 50)
        if backlog["status"] == "checked":
            for key in ("default_ref", "default_sha", "method"):
                _text(backlog.get(key), "backlog." + key, 500)
            if not isinstance(backlog.get("pending"), list) or len(backlog["pending"]) > 50:
                raise CrabError("checked backlog needs a bounded explicit pending-work list")
        queries = need.get("queries")
        if (
            not isinstance(queries, list)
            or not 1 <= len(queries) <= 4
            or not all(isinstance(q, str) and 0 < len(q) <= 256 for q in queries)
        ):
            raise CrabError("receiving need requires 1..4 bounded search phrases")
        rows.append(
            {
                **need,
                "phase": "search",
                "round": 0,
                "query_index": 0,
                "leads": [],
                "outcomes": [],
                "status": "pending",
                "consumed_leads": [],
            }
        )
    value: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": installed_source_identity(),
        "created_at": instant.isoformat(),
        "context": context,
        "plan": plan,
        "limits": limits,
        "needs": rows,
        "spent": {"requests": 0, "donors": 0, "model_tokens": 0},
        "lease": None,
        "events": [],
        "finished": False,
        "finished_at": None,
        "wall_time_seconds": None,
        "stop_reason": None,
    }
    directory.mkdir(parents=True, exist_ok=False)
    _save(directory, value)
    return summary(value)


def _load(directory: Path, *, require_current: bool = True) -> dict[str, Any]:
    value = read_object(directory / "session.json")
    if value.get("schema") != SCHEMA or not isinstance(value.get("events"), list):
        raise CrabError("invalid discovery session")
    if len(value["events"]) > 500:
        raise CrabError("discovery event budget exceeded")
    expected = value.pop("state_sha256", None)
    if expected != fingerprint(value):
        raise CrabError("discovery state identity is inconsistent")
    previous = None
    for index, event in enumerate(value["events"]):
        row = {key: item for key, item in event.items() if key != "sha256"}
        if (
            row.get("index") != index
            or row.get("previous") != previous
            or event.get("sha256") != fingerprint(row)
        ):
            raise CrabError("discovery event chain is inconsistent")
        previous = event["sha256"]
    # The immutable receiving context is rechecked before leasing any new action.
    if require_current and value.get("tool") != installed_source_identity():
        raise CrabError("discovery tool changed; replan this session")
    if require_current and live_context(Path(value["context"]["root"])) != value["context"]:
        raise CrabError("receiving context changed; replan this discovery session")
    return value


def _save(directory: Path, value: dict[str, Any]) -> None:
    value.pop("state_sha256", None)
    value["state_sha256"] = fingerprint(value)
    encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if len(encoded.encode()) > 7 * 1024 * 1024:
        raise CrabError("discovery state byte budget exhausted; retain the pending attempt")
    atomic_text(directory / "session.json", encoded)


def _event(value: dict[str, Any], entry: dict[str, Any]) -> None:
    if len(value["events"]) >= 500:
        raise CrabError("discovery event budget exhausted; retain the pending attempt")
    previous = value["events"][-1]["sha256"] if value["events"] else None
    row = {"previous": previous, "index": len(value["events"]), "record": entry}
    value["events"].append({**row, "sha256": fingerprint(row)})


def _exhausted(value: dict[str, Any], now: datetime) -> str | None:
    elapsed = (now - datetime.fromisoformat(value["created_at"])).total_seconds()
    if elapsed < 0 or elapsed >= value["limits"]["seconds"]:
        return "wall-time-budget-or-clock-invalid"
    if value["spent"]["model_tokens"] >= value["limits"]["model_tokens"]:
        return "model-token-budget"
    return None


def _finish(value: dict[str, Any], reason: str, instant: datetime) -> None:
    # Late accounting can change the cost verdict, never the duration of a
    # finished investigation. Legacy terminal sessions retain unknown duration.
    if not value["finished"]:
        value["finished_at"] = instant.isoformat()
        value["wall_time_seconds"] = (
            instant - datetime.fromisoformat(value["created_at"])
        ).total_seconds()
    value.update(finished=True, stop_reason=reason)


def _pending_need(value: dict[str, Any]) -> dict[str, Any] | None:
    """Finish an admitted donor before rotating to another pending receiving need.

    A serial first-need queue can exhaust the whole shared budget before any
    later need is investigated. The reservation/result event chain is the
    scheduler cursor: no second mutable progress counter is required.
    """
    pending = [row for row in value["needs"] if row["status"] == "pending"]
    if not pending:
        return None
    previous = next(
        (
            event["record"]
            for event in reversed(value["events"])
            if event["record"].get("kind") == "reserved-action"
        ),
        None,
    )
    if previous is None:
        return pending[0]
    active = next((row for row in pending if row["id"] == previous["need"]), None)
    completed = next(
        (
            event["record"]
            for event in reversed(value["events"])
            if event["record"].get("kind") == "action-result"
            and event["record"]["receipt"].get("attempt") == previous["attempt"]
        ),
        None,
    )
    if completed is None:
        return active or pending[0]
    action = previous["action"]
    result = completed["receipt"]["result"]
    donor_boundary = (
        action == "decide"
        or action == "acquire"
        and result.get("status") in {"unavailable", "license-held", "unsupported"}
        or action == "search"
        and (active is None or active["phase"] == "search")
    )
    if not donor_boundary:
        return active or pending[0]
    previous_index = next(
        index for index, row in enumerate(value["needs"]) if row["id"] == previous["need"]
    )
    for advance in range(1, len(value["needs"]) + 1):
        row = value["needs"][(previous_index + advance) % len(value["needs"])]
        if row["status"] == "pending":
            return row
    return None


def next_action(directory: Path, *, now: datetime | None = None) -> dict[str, Any]:
    instant = now or datetime.now(UTC)
    with state_lock(directory / "session.json"):
        value = _load(directory)
        if value["finished"]:
            return summary(value)
        reason = _exhausted(value, instant)
        if reason:
            _finish(value, reason, instant)
            _save(directory, value)
            return summary(value)
        deadline = datetime.fromisoformat(value["created_at"]) + timedelta(
            seconds=value["limits"]["seconds"]
        )
        if value["lease"]:
            lease = dict(value["lease"])
            lease["remaining_model_tokens"] = max(
                0, value["limits"]["model_tokens"] - value["spent"]["model_tokens"]
            )
            lease["remaining_seconds"] = (deadline - instant).total_seconds()
            if instant >= datetime.fromisoformat(lease["expires_at"]):
                lease["action"] = "reconcile"
                lease["reason"] = (
                    "previous attempt may have consumed provider effects; do not repeat blindly"
                )
            return lease
        need = _pending_need(value)
        if need is None:
            reason = (
                "scoped-goals-met"
                if all(row["status"] == "found" for row in value["needs"])
                else "supported-frontier-exhausted"
            )
            _finish(value, reason, instant)
            _save(directory, value)
            return summary(value)
        phase = need["phase"]
        charge = "requests" if phase == "search" else "donors" if phase == "acquire" else None
        if charge:
            if value["spent"][charge] >= value["limits"][charge]:
                _finish(value, charge + "-budget", instant)
                _save(directory, value)
                return summary(value)
            value["spent"][charge] += 1
        identity = fingerprint(
            {
                "context": value["context"],
                "index": len(value["events"]),
                "need": need["id"],
                "phase": phase,
            }
        )
        lease = {
            "attempt": identity,
            "need": need["id"],
            "action": phase,
            "expires_at": min(instant + timedelta(minutes=10), deadline).isoformat(),
            "remaining_seconds": (deadline - instant).total_seconds(),
            "remaining_model_tokens": value["limits"]["model_tokens"]
            - value["spent"]["model_tokens"],
            "query": need["queries"][need["query_index"] % len(need["queries"])],
            "lead": need.get("lead"),
            "context": value["context"],
            "need_record": {
                key: need[key]
                for key in (
                    "id",
                    "flow",
                    "expected",
                    "observed",
                    "impact",
                    "constraints",
                    "maw_evidence",
                    "backlog",
                )
            },
        }
        value["lease"] = lease
        _event(value, {"kind": "reserved-action", **lease, "charge": charge})
        _save(directory, value)
        return lease


def record_usage(
    directory: Path, receipt: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Settle actual host use without accepting a semantic result or advancing work.

    A known attempt may be expired, refused, stopped or receiving-stale: its cost
    still exists. These receipts never grant new actions or refresh a lease.
    """
    instant = now or datetime.now(UTC)
    fields = {"operation", "attempt", "author", "tool", "model_tokens"}
    if set(receipt) != fields or len(json.dumps(receipt).encode()) > 16_000:
        raise CrabError("model usage requires only bounded operation/attempt/author/tool/tokens")
    operation = _text(receipt["operation"], "usage.operation", 200)
    attempt = _text(receipt["attempt"], "usage.attempt", 200)
    _text(receipt["author"], "usage.author", 200)
    _text(receipt["tool"], "usage.tool", 200)
    tokens = receipt["model_tokens"]
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        raise CrabError("usage needs actual nonnegative model_tokens or null for unknown use")
    with state_lock(directory / "session.json"):
        value = _load(directory, require_current=False)
        for event in value["events"]:
            row = event["record"]
            if row.get("kind") == "model-usage" and row["receipt"]["operation"] == operation:
                if row["receipt"] != receipt:
                    raise CrabError("model usage operation already exists with a different receipt")
                return summary(value)
        if not any(
            event["record"].get("kind") == "reserved-action"
            and event["record"].get("attempt") == attempt
            for event in value["events"]
        ):
            raise CrabError("model usage does not belong to a known discovery attempt")
        if tokens is None:
            _finish(value, "usage-unavailable", instant)
            value["model_usage_complete"] = False
        else:
            value["spent"]["model_tokens"] += tokens
            reason = _exhausted(value, instant)
            if value["spent"]["model_tokens"] >= value["limits"]["model_tokens"]:
                _finish(value, "model-token-budget", instant)
            elif not value["finished"] and reason:
                _finish(value, reason, instant)
        _event(value, {"kind": "model-usage", "receipt": receipt, "at": instant.isoformat()})
        _save(directory, value)
        return summary(value)


def record(
    directory: Path, receipt: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    instant = now or datetime.now(UTC)
    if len(json.dumps(receipt, ensure_ascii=False).encode()) > 256_000:
        raise CrabError("discovery receipt exceeds its byte budget")
    with state_lock(directory / "session.json"):
        value = _load(directory)
        lease = value["lease"]
        if not isinstance(lease, dict) or receipt.get("attempt") != lease["attempt"]:
            # An exact retry after a successful record returns the current report.
            if any(
                event["record"].get("kind") == "action-result"
                and event["record"].get("receipt") == receipt
                for event in value["events"]
            ):
                return summary(value)
            raise CrabError("discovery receipt does not own the pending attempt")
        _text(receipt.get("author"), "receipt.author", 200)
        _text(receipt.get("tool"), "receipt.tool", 200)
        tokens = receipt.get("model_tokens")
        if type(tokens) is not int or tokens < 0:
            raise CrabError("actual nonnegative model_tokens are required for budget enforcement")
        need = next(row for row in value["needs"] if row["id"] == lease["need"])
        action = lease["action"]
        result = receipt.get("result")
        if not isinstance(result, dict):
            raise CrabError("discovery result must be an object")
        if action == "search":
            if type(result.get("requests", 1)) is not int or result.get("requests", 1) not in {
                0,
                1,
            }:
                raise CrabError("one leased search may consume at most one provider request")
            candidates = result.get("candidates", [])
            if not isinstance(candidates, list) or len(candidates) > 100:
                raise CrabError("search result candidate limit exceeded")
            need["leads"] = [
                row
                for row in candidates
                if isinstance(row, dict)
                and isinstance(row.get("repository"), str)
                and 0 < len(row["repository"]) <= 500
                and row["repository"].casefold() not in need["consumed_leads"]
            ][:20]
            unique: dict[str, dict[str, Any]] = {}
            for lead in need["leads"]:
                unique.setdefault(lead["repository"].casefold(), lead)
            need["leads"] = list(unique.values())
            if need["leads"]:
                need["lead"] = need["leads"].pop(0)
                need["phase"] = "acquire"
            else:
                _replace(need, value)
        elif action == "acquire":
            from .compare import load_menu

            if result.get("status") in {"unavailable", "license-held", "unsupported"}:
                need["outcomes"].append({**result, "lead": need["lead"], "reviewed": False})
                _replace(need, value)
                return _finish_record(directory, value, receipt, tokens, instant)
            meal = Path(_text(result.get("meal_dir"), "meal_dir", 2000))
            menu = load_menu(meal)
            if menu is None or not _same_context(menu.get("receiving_context"), value["context"]):
                raise CrabError("acquisition requires an actual fresh receiving-bound meal")
            prey = menu.get("prey", {})
            if need["lead"]["repository"].casefold() not in {
                str(prey.get("label", "")).casefold(),
                str(prey.get("url", "")).casefold(),
            }:
                raise CrabError("acquired donor differs from the reserved lead")
            need["meal_dir"] = str(meal)
            need["phase"] = "inspect"
        elif action == "inspect":
            exploration = Path(_text(result.get("exploration"), "exploration", 2000))
            if exploration.resolve().parent.parent != Path(need["meal_dir"]).resolve():
                raise CrabError("inspection exploration does not belong to the acquired meal")
            child = exploration_report(exploration)
            if not _same_context(child.get("receiving_context"), value["context"]):
                raise CrabError("inspection exploration belongs to a different receiving context")
            need["exploration"] = str(exploration)
            need["phase"] = "verify"
        elif action == "verify":
            tier = result.get("evidence_level")
            if tier not in {"source-supported", "behavior-verified", "design-proposal", "unrun"}:
                raise CrabError("verification needs an explicit evidence level")
            if tier == "behavior-verified":
                for key in (
                    "expected",
                    "observed",
                    "probe_sha256",
                    "receiving_sha",
                    "execution_receipt",
                ):
                    _text(result.get(key), "verification." + key)
                if (
                    result["receiving_sha"] != value["context"]["sha"]
                    or result.get("probe_owner") != "receiving"
                ):
                    raise CrabError(
                        "probe must be original receiving-owned evidence at the selected SHA"
                    )
                if re.fullmatch(r"[a-f0-9]{64}", result["probe_sha256"]) is None:
                    raise CrabError("invalid original probe identity")
                from .receiving_probe import verify_receipt

                verify_receipt(
                    Path(result["execution_receipt"]), value["context"], result["probe_sha256"]
                )
            elif tier == "design-proposal":
                from .receiving_probe import verify_design_artifact

                verify_design_artifact(result.get("acceptance_artifact"), value["context"])
            need["verification"] = result
            need["phase"] = "decide"
        elif action == "decide":
            outcome = result.get("disposition")
            if outcome not in OUTCOMES:
                raise CrabError("unsupported discovery outcome")
            _text(result.get("reason"), "decision.reason")
            hypothesis = _text(result.get("hypothesis"), "decision.hypothesis")
            child = exploration_report(Path(need["exploration"]))
            decisions = [
                row
                for row in child["decisions"]
                if row.get("disposition") == outcome and row.get("hypothesis") == hypothesis
            ]
            if not decisions:
                raise CrabError("decision needs a matching actual exploration observation")
            if outcome == "verified-gap" and not any(row.get("reviewed") for row in decisions):
                raise CrabError("a proposed gap is not a reviewed finding")
            if outcome == "verified-gap":
                for key in ("adaptation", "maintenance", "novelty"):
                    _text(result.get(key), "decision." + key)
                if need["verification"]["evidence_level"] == "unrun":
                    raise CrabError("an unrun hypothesis cannot complete a receiving need")
            need["outcomes"].append(
                {
                    **result,
                    "verification": need.get("verification"),
                    "exploration": need["exploration"],
                }
            )
            if outcome == "verified-gap":
                need["status"] = "found"
            else:
                _replace(need, value)
        else:
            raise CrabError("unknown discovery phase")
        return _finish_record(directory, value, receipt, tokens, instant)


def _same_context(actual: object, expected: dict[str, Any]) -> bool:
    return isinstance(actual, dict) and all(
        actual.get(key) == expected.get(key)
        for key in ("root", "sha", "selected_ref", "worktree", "policy", "ledger")
    )


def _finish_record(
    directory: Path, value: dict[str, Any], receipt: dict[str, Any], tokens: int, instant: datetime
) -> dict[str, Any]:
    value["spent"]["model_tokens"] += tokens
    if value["spent"]["model_tokens"] >= value["limits"]["model_tokens"]:
        _finish(value, "model-token-budget", instant)
    elif not value["finished"]:
        reason = _exhausted(value, instant)
        if reason:
            _finish(value, reason, instant)
    value["lease"] = None
    _event(value, {"kind": "action-result", "receipt": receipt, "at": instant.isoformat()})
    _save(directory, value)
    return summary(value)


def _replace(need: dict[str, Any], value: dict[str, Any]) -> None:
    if need.get("lead"):
        need["consumed_leads"].append(need["lead"]["repository"].casefold())
    if need["leads"]:
        need["lead"] = need["leads"].pop(0)
        need["phase"] = "acquire"
    elif need["round"] < value["limits"]["replacement_rounds"]:
        need["round"] += 1
        need["query_index"] += 1
        need["phase"] = "search"
        need.pop("lead", None)
    else:
        need["status"] = "frontier-exhausted"


def summary(value: dict[str, Any]) -> dict[str, Any]:
    elapsed = value.get("wall_time_seconds")
    wall_compliant: bool | None = None
    if isinstance(elapsed, int | float) and not isinstance(elapsed, bool):
        wall_compliant = 0 <= elapsed <= value["limits"]["seconds"]
    elif value["stop_reason"] == "wall-time-budget-or-clock-invalid":
        wall_compliant = False
    return {
        "schema": SCHEMA,
        "context": value["context"],
        "tool": value.get("tool"),
        "limits": value["limits"],
        "spent": value["spent"],
        "model_usage_complete": value.get("model_usage_complete", True),
        "actual_model_tokens": (
            value["spent"]["model_tokens"] if value.get("model_usage_complete", True) else None
        ),
        "finished": value["finished"],
        "stop_reason": value["stop_reason"],
        "finished_at": value.get("finished_at"),
        "wall_time_seconds": elapsed,
        "wall_time_within_limits": wall_compliant,
        "needs": [
            {
                "id": row["id"],
                "status": row["status"],
                "phase": row["phase"],
                "rounds": row["round"],
                "outcomes": row["outcomes"],
                "novelty_coverage": row["backlog"]["status"],
                "receiving_flow": row["flow"],
            }
            for row in value["needs"]
        ],
        "scope_complete": all(row["status"] == "found" for row in value["needs"]),
        "structural_seed_completion": "see-individual-explore-reports",
        "published": False,
        "publication_scope": "controller-has-no-publication-effect; consult-per-meal-phase-records",
        "acceptance_learning_written": False,
        "budget_within_limits": wall_compliant is not False
        and value.get("model_usage_complete", True)
        and all(
            value["spent"][name] <= value["limits"][name]
            for name in ("requests", "donors", "model_tokens")
        ),
    }


def report(directory: Path) -> dict[str, Any]:
    value = _load(directory, require_current=False)
    result = summary(value)
    result["tool_status"] = (
        "current" if value.get("tool") == installed_source_identity() else "stale"
    )
    try:
        result["context_status"] = (
            "current"
            if live_context(Path(value["context"]["root"])) == value["context"]
            else "stale"
        )
    except CrabError:
        result["context_status"] = "unavailable"
    outcomes = [outcome for need in value["needs"] for outcome in need["outcomes"]]
    gaps = [row for row in outcomes if row.get("disposition") == "verified-gap"]
    result["evidence_counts"] = {
        "reviewed_gap_proposals": len(gaps),
        "behavior_verified": sum(
            row.get("verification", {}).get("evidence_level") == "behavior-verified" for row in gaps
        ),
        "source_supported": sum(
            row.get("verification", {}).get("evidence_level") == "source-supported" for row in gaps
        ),
        "design_proposals": sum(
            row.get("verification", {}).get("evidence_level") == "design-proposal" for row in gaps
        ),
        "novelty_check_unknown_needs": sum(
            row["backlog"]["status"] != "checked" for row in value["needs"]
        ),
        "independent_acceptance": None,
    }
    return result


def stop(directory: Path, reason: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Stop without losing an uncertain lease or inventing missing model usage."""
    if reason not in {
        "usage-unavailable",
        "external-dependency",
        "policy-or-legal-dependency",
        "model-budget-reserve",
    }:
        raise CrabError("unsupported explicit discovery stop reason")
    with state_lock(directory / "session.json"):
        value = _load(directory, require_current=False)
        if not value["finished"]:
            _finish(value, reason, now or datetime.now(UTC))
            if reason == "usage-unavailable":
                value["model_usage_complete"] = False
            _event(value, {"kind": "explicit-stop", "reason": reason})
            _save(directory, value)
        return summary(value)


def authorize_request(directory: Path, attempt: str, operation: str) -> None:
    """Charge before a provider call; replay of an uncertain operation must reconcile."""
    if not operation or len(operation) > 200:
        raise CrabError("provider operation identity must be bounded")
    with state_lock(directory / "session.json"):
        value = _load(directory)
        lease = value.get("lease")
        if not isinstance(lease, dict) or lease["attempt"] != attempt:
            raise CrabError("provider request does not own the active discovery attempt")
        calls = [
            event["record"]
            for event in value["events"]
            if event["record"].get("kind") == "provider-request-authorized"
            and event["record"].get("attempt") == attempt
        ]
        if any(row["operation"] == operation for row in calls):
            raise CrabError("provider operation already charged; reconcile before retrying")
        if lease["action"] == "search" and calls:
            raise CrabError("one leased search permits only one provider request")
        if _exhausted(value, datetime.now(UTC)) or datetime.now(UTC) >= datetime.fromisoformat(
            lease["expires_at"]
        ):
            raise CrabError("discovery attempt expired before the provider effect")
        prepaid = lease["action"] == "search" and not calls
        if not prepaid:
            if value["spent"]["requests"] >= value["limits"]["requests"]:
                raise CrabError("global discovery request budget exhausted")
            value["spent"]["requests"] += 1
        _event(
            value,
            {
                "kind": "provider-request-authorized",
                "attempt": attempt,
                "operation": operation,
                "prepaid": prepaid,
                "at": datetime.now(UTC).isoformat(),
            },
        )
        _save(directory, value)


def drive(
    directory: Path,
    handlers: Mapping[str, Callable[[dict[str, Any]], dict[str, Any]]],
    *,
    max_actions: int = 64,
) -> dict[str, Any]:
    """Consume actions in the invoked trusted host; never import a donor/plugin.

    Handlers return actual tool/usage receipts, not suggested next commands.
    A failed/interrupted handler leaves its charged attempt pending for reconciliation.
    Semantic/model work and tool authorization remain with the invoking host.
    """
    if type(max_actions) is not int or not 1 <= max_actions <= 128:
        raise CrabError("host action budget must be in 1..128")
    for _ in range(max_actions):
        action = next_action(directory)
        if action.get("finished"):
            return action
        handler = handlers.get(action["action"])
        if handler is None:
            return {"status": "host-handler-required", "pending": action}
        receipt = handler(action)
        record(directory, receipt)
    return {"status": "host-action-limit", "report": report(directory)}
