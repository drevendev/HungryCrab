"""Evidence-backed supervised exploration; no models, donor execution or provider effects."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .artifact_read import read_bytes, read_object
from .attribution import (
    GitSourceReader,
    MaterializationReceipt,
    SourceRef,
    TakenFile,
    carried_texts,
    check_taken_licences,
    verify_root_grant,
)
from .compare import apply_hunger, load_menu, menu_candidates
from .compare.candidates import Side, _evidence_path
from .compare.license_policy import covered_exceptions
from .compare.render import menu_doc
from .compare.scoring import Scoring
from .config_edit import atomic_text
from .digest import worktree_fingerprint
from .digest_integrity import digest_integrity_errors
from .errors import CrabError
from .fetch.git import GitRunner
from .file_lock import state_lock
from .licensing.matrix import decide
from .licensing.policy import apply_maw_policy
from .maw import MawConfig
from .nutrients import Candidate, require_nutrient_id
from .typeutil import as_dict, as_list

SCHEMA = "hungry-crab.exploration/1"
FACETS = (
    "behavior",
    "algorithms",
    "recovery-resources-concurrency",
    "performance-streaming",
    "testing",
    "architecture",
    "packaging-operations",
    "documentation-contributors",
)
FACET_STATES = {
    "examined",
    "inapplicable",
    "not-acquired",
    "unsupported",
    "budget-deferred",
    "policy-blocked",
    "unknown",
    "unexplored",
}
DISPOSITIONS = {
    "verified-gap",
    "already-present",
    "not-useful",
    "duplicate",
    "out-of-scope",
    "not-investigated",
    "unsupported",
    "budget-deferred",
    "policy-blocked",
    "license-unresolved",
    "license-excluded",
    "NO_GAP",
}
PHASES = (
    "acquisition",
    "comparison",
    "exploration",
    "implementation",
    "review-ci",
    "publication",
    "default-acceptance",
)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _text(data: dict[str, Any], key: str, *, limit: int = 4000) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CrabError(f"exploration {key} must be nonempty text of at most {limit} characters")
    return value


def _write_new(path: Path, data: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _tool_identity() -> dict[str, str]:
    root = Path(__file__).parent
    sources = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.py"))
    }
    return {"version": __version__, "source_sha256": fingerprint(sources)}


def _digest_identity(directory: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    artifacts = {}
    total = 0
    for item in as_list(manifest.get("files")):
        name = as_dict(item).get("name")
        if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name:
            raise CrabError("digest identity contains an invalid artifact path")
        payload = read_bytes(directory / name, 50_000_000)
        total += len(payload)
        if total > 256 * 1024 * 1024:
            raise CrabError("digest identity exceeds the exploration read budget")
        artifacts[name] = hashlib.sha256(payload).hexdigest()
    return {"manifest": fingerprint(manifest), "artifacts": artifacts}


def initialize(meal_dir: Path, plan: dict[str, Any], *, maw: Path) -> Path:
    """Freeze the actual inputs and original denominator before any semantic judgement."""
    menu = load_menu(meal_dir)
    if menu is None:
        raise CrabError("exploration requires a readable fresh meal")
    for key in ("scope", "goals", "flows", "constraints", "author", "attempt"):
        _text(plan, key)
    facets = plan.get("facets")
    if (
        not isinstance(facets, list)
        or not facets
        or len(set(facets)) != len(facets)
        or any(f not in FACETS for f in facets)
    ):
        raise CrabError("exploration plan needs a unique nonempty list of supported facets")
    info = read_object(meal_dir / "meal.json")
    manifests: dict[str, Any] = {}
    file_evidence: dict[str, Any] = {}
    digest_identities: dict[str, Any] = {}
    for side in ("prey", "maw"):
        directory = info.get(f"{side}_digest")
        if not isinstance(directory, str):
            raise CrabError(f"exploration has no {side} digest location")
        directory_path = Path(directory)
        manifest = read_object(directory_path / "manifest.json")
        if digest_integrity_errors(directory_path, manifest):
            raise CrabError(f"exploration {side} digest is inconsistent")
        if as_dict(manifest.get("prey")).get("sha") != as_dict(menu.get(side)).get("sha"):
            raise CrabError(f"exploration {side} digest identity differs from the meal")
        manifests[side] = manifest
        digest_identities[side] = _digest_identity(directory_path, manifest)
        inventory = read_object(directory_path / "inventory.json")
        file_evidence[side] = inventory.get("evidence_lines", {})
    current = GitRunner(maw)
    observed = worktree_fingerprint(current if current.is_repo_root() else None, maw)
    expected = as_dict(as_dict(manifests["maw"]).get("prey"))
    if current.is_repo_root() and (
        current.head_sha() != expected.get("sha")
        or observed != menu.get("post_comparison_worktree", expected.get("worktree"))
        or observed == "unknown"
    ):
        raise CrabError("maw changed after comparison; regenerate the meal before exploration")
    config = MawConfig.load(maw)
    ledger_path = config.ledger_path(None)
    ledger = as_dict(menu.get("analysis_inputs")).get("ledger")
    if ledger is None and ledger_path is not None and ledger_path.is_file():
        ledger = read_object(ledger_path)
    identities = {
        "tool": _tool_identity(),
        "menu": fingerprint(menu),
        "digests": digest_identities,
        "policy": hashlib.sha256(config.path.read_bytes()).hexdigest()
        if config.path.is_file()
        else fingerprint(config.raw),
        "ledger": fingerprint(ledger),
        "maw_worktree": observed,
    }
    initial = menu.get("initial_candidates", menu.get("candidates", []))
    data = {
        "schema": SCHEMA,
        "identities": identities,
        "plan": plan,
        "menu": menu,
        "manifests": manifests,
        "file_evidence": file_evidence,
        "initial_candidates": initial,
        "denominator": len(initial),
        "legacy_denominator_unknown": "initial_candidates" not in menu,
        "ledger_snapshot": ledger,
        "maw_root": str(maw.resolve()),
        "channels": {
            "shallow": as_dict(manifests["prey"].get("prey")).get("shallow"),
            "coverage": manifests["prey"].get("coverage"),
            "wiki": manifests["prey"].get("wiki"),
        },
        "learning_audit": {
            "actual_learning_input_ids": [
                e.get("id")
                for e in as_list(as_dict(ledger).get("entries"))
                if isinstance(e, dict)
                and (e.get("feedback") or e.get("status")) in {"rejected", "accepted", "merged"}
            ],
            "unverified_legacy_feedback": [
                e.get("id")
                for e in as_list(as_dict(ledger).get("entries"))
                if isinstance(e, dict)
                and (e.get("feedback") or e.get("status")) in {"rejected", "accepted", "merged"}
                and not e.get("exploration_review")
            ],
            "memory": menu.get("memory"),
            "exploration_writes_learning": False,
        },
    }
    identity = fingerprint(data)
    destination = meal_dir / "exploration" / identity
    with state_lock(destination / "snapshot.json"):
        path = destination / "snapshot.json"
        if path.exists():
            if read_object(path) != data:
                raise CrabError("existing exploration snapshot differs from its identity")
        else:
            _write_new(path, data)
    return destination


def _snapshot(directory: Path) -> dict[str, Any]:
    data = read_object(directory / "snapshot.json")
    if data.get("schema") != SCHEMA or fingerprint(data) != directory.name:
        raise CrabError("exploration snapshot identity is inconsistent")
    return data


def events(directory: Path) -> list[dict[str, Any]]:
    _snapshot(directory)
    paths = sorted(directory.glob("event-*.json"))
    if len(paths) > 10_000:
        raise CrabError("exploration event budget exceeded")
    previous = directory.name
    result = []
    for index, path in enumerate(paths, start=1):
        event = read_object(path)
        identity = event.pop("sha256", None)
        if (
            event.get("previous") != previous
            or event.get("sequence") != index
            or fingerprint(event) != identity
        ):
            raise CrabError("exploration event chain is inconsistent")
        event["sha256"] = identity
        result.append(event)
        previous = str(identity)
    return result


def _references(
    value: object, side: dict[str, Any], files: dict[str, Any], *, objects: str | None = None
) -> bool:
    if not isinstance(value, list) or not value or len(value) > 20:
        return False
    for reference in value:
        if not isinstance(reference, dict):
            return False
        path, start, end = reference.get("path"), reference.get("start"), reference.get("end")
        if (
            not isinstance(path, str)
            or not _evidence_path(path)
            or reference.get("sha") != side.get("sha")
        ):
            return False
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= start + 1000:
            return False
        lines = files.get(path)
        if lines is None and objects is not None:
            text = GitSourceReader(Path(objects)).read(str(side.get("sha", "")), path)
            lines = len(text.splitlines()) if text is not None else None
        if type(lines) is not int or end > lines:
            return False
    return True


def _validate_record(data: dict[str, Any], record: dict[str, Any]) -> None:
    for key in ("author", "tool", "attempt", "reason"):
        _text(record, key)
    kind = record.get("kind")
    menu = data["menu"]
    source = _references(
        record.get("source_evidence"),
        menu["prey"],
        data["file_evidence"]["prey"],
        objects=menu["prey"].get("objects"),
    )
    receiving = _references(record.get("maw_evidence"), menu["maw"], data["file_evidence"]["maw"])
    if kind == "facet":
        if (
            record.get("facet") not in data["plan"]["facets"]
            or record.get("state") not in FACET_STATES
        ):
            raise CrabError("invalid facet or exploration state")
        if record["state"] in {"examined", "inapplicable"} and not (source and receiving):
            raise CrabError("examined/inapplicable facets require donor and maw evidence")
        if record["state"] == "examined":
            _text(record, "hypothesis")
    elif kind == "decision":
        _text(record, "hypothesis")
        if (
            record.get("disposition") not in DISPOSITIONS
            or type(record.get("reviewed")) is not bool
        ):
            raise CrabError("invalid disposition or reviewed flag")
        candidate = record.get("candidate_id")
        if candidate is not None and candidate not in {c["id"] for c in data["initial_candidates"]}:
            raise CrabError("decision does not name an original candidate")
        if record["reviewed"] and not (source and receiving):
            raise CrabError(
                "individual review requires inspected donor and receiving-project evidence"
            )
        if record["disposition"] == "NO_GAP" and (
            not record["reviewed"] or not record.get("examined_scope")
        ):
            raise CrabError("NO_GAP requires an examined scope and evidenced receiving behavior")
        if record["reviewed"] and record["disposition"] in {
            "not-investigated",
            "unsupported",
            "budget-deferred",
            "policy-blocked",
            "license-unresolved",
        }:
            raise CrabError("an unresolved disposition cannot claim individual semantic review")
    elif kind == "phase":
        if record.get("phase") not in PHASES or record.get("state") not in {
            "PASS",
            "FAIL",
            "UNKNOWN",
            "NOT_RUN",
        }:
            raise CrabError("invalid phase observation")
        # A phase observation describes its own artifact, never the whole goal.
        for key in ("repository", "artifact", "method"):
            _text(record, key)
        if record["phase"] == "review-ci":
            for key in ("workflow", "job", "runner"):
                _text(record, key)
            if not isinstance(record.get("executed_steps"), list) or not record["executed_steps"]:
                raise CrabError("CI observations require actually executed steps")
    elif kind == "registration":
        raise CrabError("use explore register for validated nutrient registration")
    else:
        raise CrabError("unsupported exploration event kind")


def record(directory: Path, payload: dict[str, Any]) -> dict[str, Any]:
    with state_lock(directory / "events.json"):
        snapshot = _snapshot(directory)
        _validate_record(snapshot, payload)
        return _append(directory, payload)


def _append(directory: Path, payload: dict[str, Any]) -> dict[str, Any]:
    previous = events(directory)
    for event in previous:
        if event["record"] == payload:
            return event
    event = {
        "sequence": len(previous) + 1,
        "previous": previous[-1]["sha256"] if previous else directory.name,
        "record": payload,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "actual_tool": _tool_identity(),
    }
    event["sha256"] = fingerprint(event)
    if len(json.dumps(event)) > 64_000:
        raise CrabError("exploration event exceeds its byte budget")
    _write_new(directory / f"event-{len(previous) + 1:05d}.json", event)
    return event


def report(directory: Path) -> dict[str, Any]:
    data = _snapshot(directory)
    history = events(directory)
    facets = dict.fromkeys(data["plan"]["facets"], "unexplored")
    decisions: dict[str, dict[str, Any]] = {}
    hypotheses: dict[str, dict[str, Any]] = {}
    observed_hypotheses: dict[str, None] = {}
    phases: dict[str, Any] = dict.fromkeys(PHASES, "NOT_RUN")
    for event in history:
        entry = event["record"]
        if entry["kind"] == "facet":
            facets[entry["facet"]] = entry["state"]
            if entry["state"] == "examined":
                observed_hypotheses.setdefault(entry["hypothesis"], None)
        elif entry["kind"] == "decision":
            observed_hypotheses.setdefault(entry["hypothesis"], None)
            hypotheses[entry["hypothesis"]] = entry
            if entry.get("candidate_id"):
                decisions[entry["candidate_id"]] = entry
        elif entry["kind"] == "phase":
            if phases[entry["phase"]] == "NOT_RUN":
                phases[entry["phase"]] = []
            phases[entry["phase"]].append(entry)
    pending = [
        facet for facet, state in facets.items() if state not in {"examined", "inapplicable"}
    ]
    unreviewed = [
        c["id"]
        for c in data["initial_candidates"]
        if not decisions.get(c["id"], {}).get("reviewed")
    ]
    # Facet transitions and newer hypotheses must not hide earlier unresolved work.
    # A later explicit review of the same hypothesis is the only resolution.
    unresolved_hypotheses = [
        hypothesis
        for hypothesis in observed_hypotheses
        if not hypotheses.get(hypothesis, {}).get("reviewed")
    ]
    return {
        "schema": SCHEMA,
        "snapshot": directory.name,
        "scope": data["plan"]["scope"],
        "facets": facets,
        "pending_facets": pending,
        "denominator": data["denominator"],
        "unreviewed_candidates": unreviewed,
        "unresolved_hypotheses": unresolved_hypotheses,
        "legacy_denominator_unknown": data["legacy_denominator_unknown"],
        "exploration_complete": not pending
        and not unresolved_hypotheses
        and not unreviewed
        and not data["legacy_denominator_unknown"],
        "phases": phases,
        "learning_audit": data["learning_audit"],
        "events": len(history),
    }


def _registered_menu(data: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    menu: dict[str, Any] = json.loads(json.dumps(data["menu"]))
    for event in history:
        entry = event["record"]
        if entry["kind"] != "registration":
            continue
        card = entry["card"]
        menu.setdefault("discovered_candidates", []).append(card)
        if entry["policy_blocked"]:
            menu["hidden"].append(
                {"id": card["id"], "reason": "exploration finding blocked by current hunger"}
            )
        else:
            menu["candidates"].append(card)
    menu["candidates"].sort(key=lambda c: (-c["score"], c["category"], c["key"]))
    menu["counts"]["total"] = len(menu["candidates"])
    menu["counts"]["hidden"] = len(menu["hidden"])
    menu["counts"]["by_category"] = {
        category: sum(c["category"] == category for c in menu["candidates"])
        for category in sorted({c["category"] for c in menu["candidates"]})
    }
    return menu


def register(directory: Path, finding: dict[str, Any], *, source_repo: Path) -> dict[str, Any]:
    """Validate a source-linked behavior nutrient and carry it into the guarded meal."""
    with state_lock(directory / "events.json"):
        data = _snapshot(directory)
        if finding.get("disposition") != "verified-gap":
            raise CrabError("only an evidenced verified gap can register a behavior nutrient")
        if finding.get("origin", "licensed") != "licensed":
            raise CrabError(
                "behavior registration requires licensed repository source; "
                "commenter and unknown origins cannot be widened to licensed"
            )
        for key in (
            "key",
            "title",
            "behavior",
            "gap",
            "adaptation",
            "maintenance",
            "acceptance",
            "author",
            "tool",
            "attempt",
            "reason",
        ):
            _text(finding, key)
        if re.fullmatch(r"code\.[a-z0-9][a-z0-9._-]{0,100}", finding["key"]) is None:
            raise CrabError("behavior key must be stable and maw-relative: code.<behavior>")
        source, receiving = finding.get("source_evidence"), finding.get("maw_evidence")
        menu = data["menu"]
        if not _references(
            source, menu["prey"], data["file_evidence"]["prey"], objects=str(source_repo)
        ) or not _references(receiving, menu["maw"], data["file_evidence"]["maw"]):
            raise CrabError("registration requires valid immutable donor and receiving file ranges")
        assert isinstance(source, list) and isinstance(receiving, list)
        prey_ref = SourceRef.from_dict(menu["prey"])
        if data["plan"].get("donor_policy") == "MIT-only" and prey_ref.license != "MIT":
            raise CrabError("exploration donor policy excludes this grant; retain a policy hold")
        reader = GitSourceReader(source_repo)
        source_side = Side(label=prey_ref.label, sha=prey_ref.sha, url=prey_ref.url, root=None)
        # Pin every range to the actual immutable file; never trust a recorded runner root.
        for ref in source:
            text = reader.read(prey_ref.sha, ref["path"])
            if text is None or ref["end"] > len(text.splitlines()):
                raise CrabError("registered donor range is unavailable at the pinned commit")
        receipt = MaterializationReceipt(
            nutrient_id=f"crab:code:{finding['key']}",
            source=prey_ref,
            taken=tuple(TakenFile(receiving[0]["path"], ref["path"], False) for ref in source),
            summary=finding["adaptation"],
            checks=(finding["acceptance"],),
        )
        require_nutrient_id(receipt.nutrient_id)
        # Registration never grants COPY from a label or a menu. Reuse the exact
        # immutable grant/path checks that later publication also applies.
        verify_root_grant(receipt, reader, carried_texts(prey_ref.sha, reader, prey_ref.license))
        check_taken_licences(receipt, reader)
        maw = Path(data["maw_root"])
        config = MawConfig.load(maw)
        policy = (
            hashlib.sha256(config.path.read_bytes()).hexdigest()
            if config.path.is_file()
            else fingerprint(config.raw)
        )
        if policy != data["identities"]["policy"]:
            raise CrabError("maw policy changed; regenerate the exploration snapshot")
        legal = decide(prey_ref.license, config.license or menu["maw"].get("license"))
        mode, reason = apply_maw_policy(legal.mode.value, policy=config.mode, material="code")
        paths = [ref["path"] for ref in source]
        if covered_exceptions(as_dict(data["manifests"]["prey"]).get("license", {}), paths):
            mode = "HUMAN"
        card = Candidate(
            category="code",
            key=finding["key"],
            title=finding["title"],
            what=finding["behavior"],
            origin="licensed",
            license_mode=mode,
            license_policy_reason=reason,
            prey_state=finding["behavior"],
            maw_state=finding["gap"],
            why=finding["gap"],
            how=(
                f"{finding['adaptation']}\nMaintenance: {finding['maintenance']}\n"
                f"Acceptance: {finding['acceptance']}"
            ),
            serve_as="pr",
            evidence=[
                source_side.evidence(ref["path"], f"lines {ref['start']}-{ref['end']}")
                for ref in source
            ],
            trace={
                "prey": prey_ref.label,
                "url": prey_ref.url,
                "sha": prey_ref.sha,
                "license": prey_ref.license,
                "maw": menu["maw"]["label"],
                "maw_sha": menu["maw"]["sha"],
                "maw_mode": config.mode,
                "material": "code",
                "license_relationship": "foreign",
                "license_review_required": legal.human_review,
                "exploration_snapshot": directory.name,
            },
        )
        kept, hidden = apply_hunger([card], config.hunger)
        card.score = Scoring.default().merged(config.scoring).score(card)
        history = events(directory)
        original_ids = {c["id"] for c in data["initial_candidates"]}
        if card.id in original_ids:
            raise CrabError("registered behavior identity collides with an original candidate")
        for event in history:
            previous = event["record"]
            if previous["kind"] == "registration" and previous["card"]["id"] == card.id:
                if previous["finding"] != finding:
                    raise CrabError(
                        "behavior identity is already registered with different evidence"
                    )
                break
        expected = _registered_menu(data, history)
        meal_dir = directory.parent.parent
        current = load_menu(meal_dir)
        allowed = {fingerprint(expected)}
        # Recover a crash between the append-only event and atomic meal replacement.
        for index in range(len(history) - 1, -1, -1):
            previous = history[index]["record"]
            if previous["kind"] == "registration":
                if previous["finding"] == finding:
                    allowed.add(fingerprint(_registered_menu(data, history[:index])))
                break
        if current is None or fingerprint(current) not in allowed:
            raise CrabError(
                "meal changed after exploration; refusing to overwrite another comparison"
            )
        payload = {
            "kind": "registration",
            "finding": finding,
            "card": card.to_dict(),
            "policy_blocked": not kept or bool(hidden),
        }
        event = _append(directory, payload)
        output = _registered_menu(data, events(directory))
        atomic_text(meal_dir / "menu.json", json.dumps(output, indent=2, ensure_ascii=False) + "\n")
        prey_side = Side(
            label=menu["prey"]["label"],
            sha=menu["prey"]["sha"],
            url=menu["prey"].get("url"),
            root=None,
        )
        maw_side = Side(label=menu["maw"]["label"], sha=menu["maw"]["sha"], url=None, root=None)
        atomic_text(
            meal_dir / "menu.md",
            menu_doc(
                prey_side,
                maw_side,
                menu_candidates(output),
                output["hidden"],
                output["verdict"],
                top=output["counts"]["top"],
                explain={},
            ).render(3500),
        )
        return event
