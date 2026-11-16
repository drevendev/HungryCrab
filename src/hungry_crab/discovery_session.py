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
from typing import Any, TypeVar

from .analysis_context import fingerprint, installed_source_identity, receiving_context
from .artifact_read import read_object
from .attribution import GitSourceReader
from .config_edit import atomic_text
from .digest import worktree_fingerprint
from .discovery_host import active_operations, operation_scope, require_meter
from .errors import CrabError
from .exploration import report as exploration_report
from .fetch.git import GitRunner
from .file_lock import state_lock
from .ledger import Ledger
from .maw import MawConfig

SCHEMA = "hungry-crab.discovery-session/1"
METERING_SCHEMA = "hungry-crab.host-usage-capability/1"
METERING_FIELDS = frozenset(
    {"schema", "route", "actual_model_usage", "reporting_source", "preflight_confirmed"}
)
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
ACQUISITION_DEFERRALS = frozenset({"unavailable", "license-held", "unsupported", "budget-deferred"})
T = TypeVar("T")


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


def _require_host_metering(plan: object) -> None:
    """Fail closed on absent or malformed strict-host metering declarations.

    This validates a plan declaration, never its native provider API. A live
    trusted adapter is required separately before admission. Supervised
    fixed-pair inspections deliberately do not open this discovery controller.
    """
    capability = plan.get("host_metering") if isinstance(plan, dict) else None
    if not isinstance(capability, dict) or set(capability) != METERING_FIELDS:
        raise CrabError("discovery requires explicit strict host metering preflight")
    if (
        capability.get("schema") != METERING_SCHEMA
        or capability.get("route") != "strict-metered"
        or capability.get("actual_model_usage") != "per-operation"
        or capability.get("preflight_confirmed") is not True
    ):
        raise CrabError("discovery host metering preflight is unsupported or unconfirmed")
    source = capability.get("reporting_source")
    if not isinstance(source, str) or not source.strip() or len(source) > 200:
        raise CrabError("discovery host metering needs a named actual usage source")
    if source.strip().casefold() in {"unknown", "unavailable", "none", "actual_host_meter"}:
        raise CrabError("discovery host metering cannot use an unknown source")


def _require_live_meter(value: dict[str, Any]) -> None:
    _require_host_metering(value.get("plan"))
    if not isinstance(value.get("meter_identity"), dict):
        raise CrabError("legacy host metering is unqualified; reconcile original session")
    require_meter(value["meter_identity"])


def initialize(
    directory: Path, maw: Path, plan: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    instant = now or datetime.now(UTC)
    if len(json.dumps(plan, ensure_ascii=False).encode()) > 256_000:
        raise CrabError("discovery plan exceeds its byte budget")
    _require_host_metering(plan)
    _, meter_identity = require_meter()
    if meter_identity["source"] != plan["host_metering"]["reporting_source"]:
        raise CrabError("plan host metering source differs from trusted live adapter")
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
        if need.get("verification_mode", "runtime") not in {"runtime", "design"}:
            raise CrabError("need.verification_mode must be runtime or design")
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
        "meter_identity": meter_identity,
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
    # Late accounting may change the cost verdict, but never the original
    # terminal reason or duration. Legacy terminal sessions remain unchanged.
    if value["finished"]:
        return
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
    needs: list[dict[str, Any]] = value["needs"]
    pending = [row for row in needs if row["status"] == "pending"]
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
        or (action == "acquire" and result.get("status") in ACQUISITION_DEFERRALS)
        or (action == "search" and (active is None or active["phase"] == "search"))
    )
    if not donor_boundary:
        return active or pending[0]
    previous_index = next(index for index, row in enumerate(needs) if row["id"] == previous["need"])
    for advance in range(1, len(needs) + 1):
        row = needs[(previous_index + advance) % len(needs)]
        if row["status"] == "pending":
            return row
    return None


def next_action(directory: Path, *, now: datetime | None = None) -> dict[str, Any]:
    instant = now or datetime.now(UTC)
    with state_lock(directory / "session.json"):
        value = _load(directory)
        if value["finished"]:
            return summary(value)
        try:
            _require_live_meter(value)
        except CrabError:
            if not isinstance(value.get("lease"), dict):
                raise
            # Preserve the original uncertain attempt for settlement, not replay.
            pending = dict(value["lease"])
            pending["action"] = "reconcile"
            pending["reason"] = "host metering not qualified; reconcile original attempt only"
            return pending
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
            if _pending_operations(value) or instant >= datetime.fromisoformat(lease["expires_at"]):
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
                "verification_mode": need.get("verification_mode", "runtime"),
                **{
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
        reconciliation = False
        for event in value["events"]:
            row = event["record"]
            if (
                row.get("kind") not in {"model-usage", "model-usage-reconciled"}
                or row["receipt"]["operation"] != operation
            ):
                continue
            previous = row["receipt"]
            if previous == receipt:
                return summary(value)
            if (
                previous["model_tokens"] is None
                and tokens is not None
                and {**previous, "model_tokens": tokens} == receipt
            ):
                reconciliation = True
                continue
            raise CrabError("model usage operation already exists with a different receipt")
        if not any(
            event["record"].get("kind") == "reserved-action"
            and event["record"].get("attempt") == attempt
            for event in value["events"]
        ):
            raise CrabError("model usage does not belong to a known discovery attempt")
        if tokens is None:
            _finish(value, "usage-unavailable", instant)
            value["model_usage_complete"] = False
            value.setdefault("unresolved_usage", []).append(operation)
        else:
            value["spent"]["model_tokens"] += tokens
            if reconciliation:
                unresolved = value.get("unresolved_usage")
                if isinstance(unresolved, list) and operation in unresolved:
                    unresolved.remove(operation)
                    value["model_usage_complete"] = not unresolved and not value.get(
                        "unattributed_usage_unknown", False
                    )
            reason = _exhausted(value, instant)
            if value["spent"]["model_tokens"] >= value["limits"]["model_tokens"]:
                _finish(value, "model-token-budget", instant)
            elif not value["finished"] and reason:
                _finish(value, reason, instant)
        _event(
            value,
            {
                "kind": "model-usage-reconciled" if reconciliation else "model-usage",
                "receipt": receipt,
                "at": instant.isoformat(),
            },
        )
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
        if value["finished"]:
            # A stopped lease survives for late cost settlement only. Never
            # accept a new semantic result from that original attempt.
            raise CrabError("finished discovery session cannot accept semantic results")
        # A callback may return after its dispatch permit or the session budget
        # expires. Refuse its semantic effect even when dispatch was in time;
        # actual provider/model costs still reconcile through record_usage().
        reason = _exhausted(value, instant)
        if reason:
            _finish(value, reason, instant)
            _save(directory, value)
            raise CrabError("discovery budget expired before semantic receipt; reconcile usage only")
        if instant >= datetime.fromisoformat(lease["expires_at"]):
            raise CrabError("discovery attempt expired before semantic receipt; reconcile original attempt")
        _require_live_meter(value)
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
        # A host callback is not an external provider permit. For non-search
        # phases, reconcile claimed provider calls with their original
        # pre-effect authorization events. Local-only callbacks may omit the
        # field; an authorized operation cannot disappear from the receipt.
        if action != "search":
            authorized_calls = sum(
                event["record"].get("kind") == "provider-request-authorized"
                and event["record"].get("attempt") == lease["attempt"]
                for event in value["events"]
            )
            reported_calls = result.get("requests", 0)
            if type(reported_calls) is not int or reported_calls != authorized_calls:
                raise CrabError(
                    "non-search provider request count lacks matching prior authorization"
                )
        if action == "search":
            if type(result.get("requests", 1)) is not int or result.get("requests", 1) not in {
                0,
                1,
            }:
                raise CrabError("one leased search may consume at most one provider request")
            # A prepaid search reservation is NOT proof that the host actually
            # authorized an external provider operation before its effect.
            authorized = [
                event["record"]
                for event in value["events"]
                if event["record"].get("kind") == "provider-request-authorized"
                and event["record"].get("attempt") == lease["attempt"]
            ]
            if result.get("requests", 1) == 1 and len(authorized) != 1:
                raise CrabError("search provider request lacks prior operation authorization")
            if result.get("requests", 1) == 0 and authorized:
                raise CrabError("authorized provider search cannot be reported as zero requests")
        _require_settled_operations(value, lease["attempt"])
        if tokens != 0:
            raise CrabError(
                "semantic receipts must charge zero; settle actual host model usage separately"
            )
        if action == "search":
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

            if result.get("status") in ACQUISITION_DEFERRALS:
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
    try:
        _require_host_metering(value.get("plan"))
    except CrabError:
        metering_admission = "legacy-unqualified"
    else:
        # The declaration is not independent native provider-meter proof.
        metering_admission = (
            "runtime-admitted-" + value["meter_identity"]["scope"]
            if isinstance(value.get("meter_identity"), dict)
            else "declared-unverified"
        )
    # Arithmetic is observational; a caller-controlled host claim can never
    # independently qualify a native provider meter or certify strict budgets.
    known_overrun = (
        value.get("meter_cap_violated", False)
        or wall_compliant is False
        or any(
            value["spent"][name] > value["limits"][name]
            for name in ("requests", "donors", "model_tokens")
        )
    )
    numerical_budget_within_limits: bool | None
    if known_overrun:
        numerical_budget_within_limits = False
    elif not value.get("model_usage_complete", True):
        numerical_budget_within_limits = None
    else:
        numerical_budget_within_limits = True
    # Proven numeric overruns are failures; otherwise compliance is UNKNOWN
    # until a separately qualified native metering source is implemented.
    strict_budget_compliance: bool | None = False if known_overrun else None
    return {
        "schema": SCHEMA,
        "context": value["context"],
        "tool": value.get("tool"),
        "limits": value["limits"],
        "spent": value["spent"],
        "model_usage_complete": value.get("model_usage_complete", True),
        "host_metering_admission": metering_admission,
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
        "budget_arithmetic_within_limits": numerical_budget_within_limits,
        "budget_within_limits": strict_budget_compliance,
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
                value["unattributed_usage_unknown"] = True
            _event(value, {"kind": "explicit-stop", "reason": reason})
            _save(directory, value)
        return summary(value)


def _pending_operations(value: dict[str, Any]) -> list[dict[str, Any]]:
    records = [event["record"] for event in value["events"]]
    settled = {row["operation"] for row in records if row.get("kind") == "operation-settled"}
    return [
        row
        for row in records
        if row.get("kind") in {"provider-request-authorized", "operation-started"}
        and row["operation"] not in settled
    ]


def _require_settled_operations(value: dict[str, Any], attempt: str) -> None:
    if any(row.get("attempt") == attempt for row in _pending_operations(value)):
        raise CrabError(
            "operation lacks trusted host settlement; reconcile before semantic recording"
        )


def execute_operation(
    directory: Path,
    attempt: str,
    operation: str,
    invoke: Callable[[int], T],
    *,
    kind: str = "provider",
    model_token_limit: int = 0,
    now: datetime | None = None,
) -> T:
    """Admit the real host port before calling it and settle before returning.

    Trusted hosts must wrap every external port, including nested operations.
    The allowance is enforced by the meter's native adapter, not semantic JSON.
    An interrupted/failed/late call retains its original lease and usage. There
    is deliberately no automatic retry, new lease, route fallback or model SDK.
    """
    _text(operation, "operation", 100)
    if kind not in {"provider", "model"} or type(model_token_limit) is not int:
        raise CrabError("invalid host operation kind or token allowance")
    if (kind == "provider" and model_token_limit != 0) or (
        kind == "model" and not 1 <= model_token_limit <= LIMITS["model_tokens"]
    ):
        raise CrabError("model operations require a bounded positive native token allowance")
    instant = now or datetime.now(UTC)
    identity = attempt + ":" + operation
    with state_lock(directory / "session.json"):
        value = _load(directory)
        _require_live_meter(value)
        meter, _ = require_meter(value["meter_identity"])
        lease = value.get("lease")
        if value["finished"]:
            raise CrabError("finished discovery session cannot dispatch host operations")
        if not isinstance(lease, dict) or lease["attempt"] != attempt:
            raise CrabError("host operation does not own an active discovery attempt")
        if _exhausted(value, instant) or instant >= datetime.fromisoformat(lease["expires_at"]):
            raise CrabError("discovery attempt expired before host operation")
        records = [event["record"] for event in value["events"]]
        if any(row.get("operation") == identity for row in records):
            raise CrabError("host operation already admitted; reconcile before retrying")
        session_key = str(directory.resolve()) + ":"
        if any(
            session_key + row["operation"] not in active_operations()
            for row in _pending_operations(value)
        ):
            raise CrabError("uncertain original host operation must reconcile before new effects")
        settled = {row["operation"] for row in records if row.get("kind") == "operation-settled"}
        pending = [
            row
            for row in records
            if row.get("kind") == "operation-started" and row["operation"] not in settled
        ]
        reserved = sum(row["model_token_limit"] for row in pending)
        if (
            value["spent"]["model_tokens"] + reserved + model_token_limit
            > value["limits"]["model_tokens"]
        ):
            raise CrabError("shared discovery model token allowance exhausted")
        # Leave room for all in-flight terminal usage/settlement records, even
        # if nested calls or a host crash occur after admission.
        if len(value["events"]) + 4 * (len(pending) + 1) + 2 > 500:
            raise CrabError("discovery event budget exhausted before host operation")
        if kind == "provider":
            _authorize_provider(value, attempt, identity, instant)
        _event(
            value,
            {
                "kind": "operation-started",
                "attempt": attempt,
                "operation": identity,
                "operation_kind": kind,
                "model_token_limit": model_token_limit,
                "at": instant.isoformat(),
            },
        )
        _save(directory, value)
    with operation_scope(session_key + identity):
        ticket: object = None
        tokens: int | None = 0 if kind == "provider" else None
        try:
            if kind == "model":
                ticket = meter.begin(identity, model_token_limit)
            return invoke(model_token_limit)
        finally:
            if kind == "model" and ticket is not None:
                try:
                    tokens = meter.finish(ticket)
                except Exception:
                    tokens = None
            if tokens is not None and (type(tokens) is not int or tokens < 0):
                tokens = None
            _settle_operation(directory, attempt, identity, tokens, now=now)


def _settle_operation(
    directory: Path,
    attempt: str,
    operation: str,
    tokens: int | None,
    *,
    now: datetime | None = None,
) -> None:
    """The broker's terminal path; semantic JSON cannot create this event."""
    # Accounting intentionally tolerates expired/stopped/stale receiving state.
    record_usage(
        directory,
        {
            "attempt": attempt,
            "operation": operation,
            "author": "trusted host operation broker",
            "tool": "native meter boundary",
            "model_tokens": tokens,
        },
        now=now,
    )
    with state_lock(directory / "session.json"):
        value = _load(directory, require_current=False)
        _event(
            value,
            {
                "kind": "operation-settled",
                "attempt": attempt,
                "operation": operation,
                "usage_available": tokens is not None,
            },
        )
        if tokens is not None:
            started = next(
                event["record"]
                for event in value["events"]
                if event["record"].get("kind") == "operation-started"
                and event["record"].get("operation") == operation
            )
            if tokens > started["model_token_limit"]:
                value["meter_cap_violated"] = True
                _finish(value, "model-token-budget", now or datetime.now(UTC))
        _save(directory, value)


def _authorize_provider(
    value: dict[str, Any], attempt: str, operation: str, instant: datetime
) -> None:
    lease = value["lease"]
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
            "at": instant.isoformat(),
        },
    )


def authorize_request(
    directory: Path, attempt: str, operation: str, *, now: datetime | None = None
) -> None:
    """Charge before a provider call; replay of an uncertain operation must reconcile."""
    if not operation or len(operation) > 200:
        raise CrabError("provider operation identity must be bounded")
    instant = now or datetime.now(UTC)
    with state_lock(directory / "session.json"):
        value = _load(directory)
        if value["finished"]:
            raise CrabError("finished discovery session cannot authorize new provider requests")
        _require_live_meter(value)
        lease = value.get("lease")
        if not isinstance(lease, dict) or lease["attempt"] != attempt:
            raise CrabError("provider request does not own the active discovery attempt")
        if _exhausted(value, instant) or instant >= datetime.fromisoformat(lease["expires_at"]):
            raise CrabError("discovery attempt expired before the provider effect")
        _authorize_provider(value, attempt, operation, instant)
        _save(directory, value)


def authorize_host_dispatch(directory: Path, attempt: str, phase: str) -> None:
    """Persist a no-replay callback permit without inventing provider usage.

    A host phase is not necessarily an external provider request. The host must
    separately call authorize_request() for each real provider operation.
    """
    with state_lock(directory / "session.json"):
        value = _load(directory)
        if value["finished"]:
            raise CrabError("finished discovery session cannot dispatch host callbacks")
        _require_live_meter(value)
        lease = value.get("lease")
        if not isinstance(lease, dict) or lease["attempt"] != attempt or lease["action"] != phase:
            raise CrabError("host callback does not own the active discovery attempt")
        if any(
            event["record"].get("attempt") == attempt
            and event["record"].get("kind")
            in {"host-handler-dispatch-authorized", "provider-request-authorized"}
            for event in value["events"]
        ):
            raise CrabError("host handler already dispatched; reconcile before retrying")
        instant = datetime.now(UTC)
        if _exhausted(value, instant) or instant >= datetime.fromisoformat(lease["expires_at"]):
            raise CrabError("discovery attempt expired before host callback")
        _event(
            value,
            {
                "kind": "host-handler-dispatch-authorized",
                "attempt": attempt,
                "phase": phase,
                "at": instant.isoformat(),
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
        # A reconcile-only lease is not an executable provider action. Host
        # settlement must use record_usage() for the original attempt instead.
        if action["action"] == "reconcile":
            return {"status": "reconciliation-required", "pending": action}
        handler = handlers.get(action["action"])
        if handler is None:
            return {"status": "host-handler-required", "pending": action}
        # Reserve only the callback invocation, not a fictitious provider request.
        # A failed handler must reconcile; its actual provider calls require
        # separate operation-specific authorize_request() receipts.
        authorize_host_dispatch(directory, action["attempt"], action["action"])
        receipt = handler(action)
        record(directory, receipt)
    return {"status": "host-action-limit", "report": report(directory)}
