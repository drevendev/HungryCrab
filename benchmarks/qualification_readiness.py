"""Fail-closed evidence gate for fresh discovery qualification inference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

BASELINE = "915779769747908a14b1a0f7fdecdd79d16dce43"


def host_fingerprint(root: Path) -> str:
    """Bind actual executor, transport, readers and matching candidate skills."""
    files = [*root.joinpath("benchmarks").glob("qualification*.py")]
    files += [
        root / "benchmarks" / name
        for name in (
            "research_host.py",
            "semantic_json.py",
            "review_transport.py",
        )
    ]
    for folder in [root / "src/hungry_crab", root / "skills"]:
        files += [
            p
            for p in folder.rglob("*")
            if p.is_file() and p.suffix in {".py", ".yml", ".css", ".md"}
        ]
    values = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(files))
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def verified(reference: dict[str, str]) -> dict[str, Any]:
    file = Path(reference["path"])
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError("readiness evidence changed: " + str(file))
    return dict(json.loads(data))


def verify_native_meter(root: Path, *, model: str, effort: str) -> int:
    """Reconcile each actual native result with its original sealed request."""
    from research_host import identity, read

    meter = read(root / "meter.json")
    if meter.get("usage_complete") is not True or not meter.get("stages"):
        raise ValueError("native meter is empty or incomplete")
    total = 0
    names: set[str] = set()
    for stage in meter["stages"]:
        name = stage["name"]
        if name in names or Path(name).name != name:
            raise ValueError("native stages must have distinct local names")
        names.add(name)
        request = read(root / (name + ".request.json"))
        result = read(root / name / "result.json")
        if (
            identity(request) != stage["signature"]
            or request["model"] != model
            or request["effort"] != effort
            or stage["state"] != "settled"
            or result.get("status") != "completed"
            or result.get("usage_complete") is not True
            or result.get("budget_valid") is not True
            or result.get("tool_calls") != 0
        ):
            raise ValueError("native control request/result/model binding is incomplete")
        usage = result["usage"]
        # Native inputTokens already includes cached input; do not double count it.
        if (
            usage["totalTokens"] != usage["inputTokens"] + usage["outputTokens"]
            or usage["cachedInputTokens"] > usage["inputTokens"]
            or stage["actual_tokens"] != usage["totalTokens"]
        ):
            raise ValueError("native control actual token receipt is inconsistent")
        total += usage["totalTokens"]
    if meter["tokens"] != total or not 0 < total <= 30000:
        raise ValueError("native control cumulative actual usage is out of cap")
    return total


def check_control(
    reference: dict[str, str],
    *,
    arm: str,
    mode: str,
    fingerprint: str,
    model: str = "gpt-6.1-sol",
    effort: str = "medium",
) -> None:
    state = verified(reference)
    if (
        state.get("host_fingerprint") != fingerprint
        or state.get("phase") != "complete"
        or state.get("usage_complete") is not True
        or state.get("model_budget_valid") is not True
        or state.get("seconds", 2701) > 2700
        or not 0 < state.get("actual_model_tokens", 0) <= 30000
    ):
        raise ValueError("full-host control is stale, incomplete or out of cap")
    if state.get("trial", "").split("-")[1:2] != [arm]:
        raise ValueError("control arm does not match its actual executor")
    outcomes = state.get("outcomes", [])
    positive = [
        index
        for index, row in enumerate(outcomes)
        if row["decision"]["disposition"] == "verified-gap"
        and row["verification"]["evidence_level"]
        == ("behavior-verified" if mode == "runtime" else "design-proposal")
    ]
    if not positive:
        raise ValueError("control has no completed useful " + mode + " investigation")
    if not any(
        row["decision"]["disposition"] in {"already-present", "duplicate", "not-useful"}
        for row in outcomes[: positive[0]]
    ):
        raise ValueError("control does not demonstrate real same-budget continuation")
    total = verify_native_meter(
        Path(reference["path"]).parent / "native", model=model, effort=effort
    )
    if total != state["actual_model_tokens"] or state.get("effects"):
        raise ValueError("control state differs from actual native receipts or has effects")
    if any(row.get("reviewed") is not True for row in (outcomes[0], outcomes[positive[0]])):
        raise ValueError("continuation requires actual CLI-validated evidence on both outcomes")
    if mode == "runtime":
        cases = [
            case for value in state["case_results"].values() for case in value.get("cases", [])
        ]
        if not any(c["role"] == "target" and c["target_called"] and not c["passed"] for c in cases):
            raise ValueError("control lacks actual receiving target failure")
        if not any(
            c["role"] == "positive-control" and c["target_called"] and c["passed"] for c in cases
        ):
            raise ValueError("control lacks executed positive control")
        from hungry_crab.receiving_probe import verify_receipt

        for value in state["case_results"].values():
            if value.get("status") != "executed":
                raise ValueError("runtime control contains an unavailable original receiving probe")
            receipt = Path(value["receipt"])
            context = json.loads(receipt.read_text(encoding="utf-8"))["context"]
            verify_receipt(receipt, context, value["probe_sha256"])
    else:
        artifact = outcomes[positive[0]]["verification"]["acceptance_artifact"]
        file = Path(artifact["path"])
        if hashlib.sha256(file.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError("original control acceptance artifact changed")


def require_readiness(plan: dict[str, Any], root: Path) -> None:
    """No fresh native call can use a component-only or self-declared PASS."""
    purpose = plan.get("purpose")
    if purpose not in {"engineering-control", "qualification"}:
        raise ValueError("execution requires explicit control/qualification purpose")
    for row in plan["pool"]:
        if row.get("acquisition_status", "shared-facts-acquired") != "shared-facts-acquired":
            continue
        view = Path(row["view"])
        if not (view / ".git").is_dir():
            raise ValueError("both arms require compatible inert working-tree source views")
        digests = row.get("arm_digests", {})
        for arm in ("baseline", "candidate"):
            if arm not in digests:
                raise ValueError("both actual arm digests are required before inference")
            manifest = json.loads(
                (Path(digests[arm]) / "manifest.json").read_text(encoding="utf-8")
            )
            source = manifest.get("prey", {})
            if (
                source.get("worktree") not in {None, "", "clean"}
                or source.get("sha") != row.get("sha")
                or source.get("label") != row.get("repository")
                or source.get("url") != "https://github.com/" + row["repository"]
            ):
                raise ValueError("arm digest is dirty or does not bind the exact public source")
    if purpose == "engineering-control":
        if plan.get("control_data") not in {"owned-fixtures", "registered-exposed-sources"}:
            raise ValueError("engineering controls require explicit non-fresh data provenance")
        if "execution_host_fingerprint" in plan and plan[
            "execution_host_fingerprint"
        ] != host_fingerprint(root):
            raise ValueError("engineering control host changed after its freeze")
        return
    reference = plan.get("readiness_certificate")
    if not isinstance(reference, dict):
        raise ValueError("fresh qualification requires verified full-host readiness evidence")
    certificate = verified(reference)
    fingerprint = host_fingerprint(root)
    if (
        certificate.get("status") != "passed"
        or certificate.get("host_fingerprint") != fingerprint
        or certificate.get("baseline_commit") != BASELINE
        or certificate.get("model") != plan["host"]["model"]
        or certificate.get("effort") != plan["host"]["effort"]
    ):
        raise ValueError("readiness certificate does not bind the current exact host/arms/model")
    expected = {(arm, mode) for arm in ("baseline", "candidate") for mode in ("runtime", "design")}
    controls = certificate.get("controls", [])
    if len(controls) != 4 or {(c["arm"], c["mode"]) for c in controls} != expected:
        raise ValueError("readiness needs actual runtime/design controls on both arms")
    for control in controls:
        check_control(
            control["state"],
            arm=control["arm"],
            mode=control["mode"],
            fingerprint=fingerprint,
            model=plan["host"]["model"],
            effort=plan["host"]["effort"],
        )
        profile = verified(control["profile"])
        if (
            profile.get("purpose") != "engineering-control"
            or profile.get("execution_host_fingerprint") != fingerprint
            or profile["host"] != plan["host"]
            or profile["arm_python"] != plan["arm_python"]
            or profile["matching_skills"] != plan["matching_skills"]
            or profile["candidate"] != plan["candidate"]
            or profile["baseline"] != plan["baseline"]
        ):
            raise ValueError("control profile does not bind the actual qualification arms/host")
    cli = verified(certificate["cli_roundtrip"])
    if not all(
        cli["arms"].get(arm, {}).get("recorded") is True
        and cli["arms"][arm].get("source_fallback_exercised") is True
        and cli["arms"][arm].get("route") == "portable-digest-and-objects"
        for arm in ("baseline", "candidate")
    ):
        raise ValueError("actual published/current CLI source-fallback round trips are missing")
    if cli["arms"]["baseline"].get("reader", {}).get("version") != "0.6.8":
        raise ValueError("CLI interoperability used a substituted baseline")
    native = verified(certificate["independent_review_native"])
    review = verified(certificate["independent_review"])
    request = verified(certificate["independent_review_request"])
    if (
        native.get("status") != "completed"
        or native.get("usage_complete") is not True
        or native.get("budget_valid") is not True
        or review.get("verdict") != "supported"
        or request["model"] != plan["host"]["model"]
        or request["effort"] != plan["host"]["effort"]
        or native.get("tool_calls") != 0
        or not 0 < native.get("usage", {}).get("totalTokens", 0) <= 30000
        or len(review.get("controls", [])) != 4
        or not all(
            row.get(key) is True
            for row in review.get("controls", [])
            for key in (
                "useful_investigation_complete",
                "same_budget_continuation",
                "original_verification_valid",
            )
        )
    ):
        raise ValueError("full-host controls lack adequate independent metered review")
    from semantic_json import parse_answer

    if parse_answer(native["messages"][-1], request["schema"]) != review:
        raise ValueError("independent review differs from its actual native output")
