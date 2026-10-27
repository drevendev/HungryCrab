"""Freeze/import/report paired deterministic runs. Never launches an arm or acquires prey.

The operator executes the frozen schedule separately. Raw artifacts stay unchanged; receipts
seal their bytes and an explicit roots map permits later relocation without rewriting them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from hungry_crab.analysis_context import fingerprint, installed_source_identity, ledger_identity
from hungry_crab.artifact_read import read_bytes, read_object
from hungry_crab.digest import incomplete_miners
from hungry_crab.digest_integrity import digest_integrity_errors
from hungry_crab.errors import CrabError
from hungry_crab.fs import is_link_stat
from hungry_crab.meal_review import project_menu
from hungry_crab.typeutil import as_dict

MAX_BYTES = 256 * 1024 * 1024
METRICS = (
    "wall_seconds",
    "cpu_self_seconds",
    "cpu_children_seconds",
    "peak_process_rss_bytes",
    "peak_child_rss_bytes",
    "model_tokens_in",
    "model_tokens_out",
    "cost_usd",
    "acquisition_wall_seconds",
    "ci_setup_wall_seconds",
    "ci_queue_wall_seconds",
    "source_review_wall_seconds",
    "model_wall_seconds",
)
T = TypeVar("T")


def measure(function: Callable[[], T]) -> tuple[T, dict[str, Any]]:
    """Portable self CPU/wall measurements; unavailable metrics remain null."""
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        import resource
    except ImportError:
        resource = None  # type: ignore[assignment]
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    value = function()
    result: dict[str, Any] = dict.fromkeys(METRICS)
    result.update(
        wall_seconds=time.perf_counter() - wall,
        cpu_self_seconds=time.process_time() - cpu,
        installed_source=installed_source_identity(),
        platform=sys.platform,
        rss_scope="cumulative process/child peak, not a per-pair delta",
    )
    if resource is not None:
        own, child = (
            resource.getrusage(resource.RUSAGE_SELF),
            resource.getrusage(resource.RUSAGE_CHILDREN),
        )
        unit = 1 if sys.platform == "darwin" else 1024
        assert child_before is not None
        result.update(
            cpu_children_seconds=child.ru_utime
            + child.ru_stime
            - child_before.ru_utime
            - child_before.ru_stime,
            peak_process_rss_bytes=own.ru_maxrss * unit,
            peak_child_rss_bytes=child.ru_maxrss * unit,
        )
    return value, result


def _new(path: Path, value: object) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def _same_time(observed: object, expected: str) -> bool:
    if not isinstance(observed, str):
        return False
    try:
        actual, frozen = datetime.fromisoformat(observed), datetime.fromisoformat(expected)
        return actual.tzinfo is not None and actual == frozen
    except ValueError:
        return False


def _name(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value) is None:
        raise CrabError("paired run/arm/pair identity must be a bounded simple name")
    return value


def _relative(value: str) -> Path:
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or any(p in {"..", "."} for p in path.parts)
        or "\\" in value
        or ":" in value
    ):
        raise CrabError("paired artifact paths must be relative POSIX paths")
    return Path(*path.parts)


def token_estimates(manifest: dict[str, Any]) -> dict[str, int | None]:
    result = {name: manifest.get(name) for name in ("markdown_tokens_est", "total_tokens_est")}
    for value in result.values():
        if value is not None and (type(value) is not int or value < 0):
            raise CrabError("paired manifest token estimate is invalid")
    return result


def digest_input_identity(manifest: dict[str, Any]) -> str:
    """Pin effective acquired inputs and limits, excluding arm output and runner paths."""
    prey = as_dict(manifest.get("prey"))
    budget = as_dict(manifest.get("budget"))
    wiki = as_dict(manifest.get("wiki"))
    return fingerprint(
        {
            "repository": prey.get("url") or prey.get("label"),
            "source": {
                key: prey.get(key)
                for key in ("sha", "ref", "worktree", "shallow", "git_scope", "repository")
            },
            "api_sha256": as_dict(manifest.get("analysis_identity")).get("api_sha256"),
            "depth": manifest.get("depth"),
            "ignore": manifest.get("ignore"),
            "maw_license": manifest.get("maw_license"),
            "coverage": manifest.get("coverage"),
            "budget": {
                key: budget.get(key) for key in ("per_markdown_file", "markdown_total", "policy")
            },
            "wiki": {key: value for key, value in wiki.items() if key != "root"},
        }
    )


def _inventory(root: Path) -> dict[str, str]:
    if root.is_symlink() or is_link_stat(root.lstat()):
        raise CrabError("paired raw root must not be linked")
    result: dict[str, str] = {}
    total = 0
    pending = [root]
    visited = 0
    while pending:
        for path in sorted(pending.pop().iterdir()):
            visited += 1
            if visited > 10000:
                raise CrabError("paired artifact entry budget exceeded")
            if is_link_stat(path.lstat()):
                raise CrabError("paired raw artifacts must not contain links")
            if path.is_dir():
                pending.append(path)
            else:
                size = path.stat().st_size
                total += size
                if total > MAX_BYTES:
                    raise CrabError("paired artifact read budget exceeded")
                result[path.relative_to(root).as_posix()] = hashlib.sha256(
                    read_bytes(path, MAX_BYTES)
                ).hexdigest()
    return result


def freeze(plan_path: Path, output: Path) -> Path:
    plan = read_object(plan_path)
    arms, pairs, repeats = plan.get("arms"), plan.get("pairs"), plan.get("repeats")
    if not isinstance(arms, list) or len(arms) != 2 or len(set(map(_name, arms))) != 2:
        raise CrabError("paired reporting requires two distinct preregistered arms")
    if not isinstance(pairs, list) or not pairs or len(set(map(_name, pairs))) != len(pairs):
        raise CrabError("paired reporting requires distinct pair identities")
    if type(repeats) is not int or repeats < 2 or repeats % 2 or repeats > 20:
        raise CrabError("paired repeats must be an even integer in 2..20")
    shared = plan.get("shared_inputs")
    required = {
        "as_of",
        "receiving",
        "prey",
        "policy",
        "ledger",
        "backlog",
        "coverage",
        "limits",
        "held_out_protocol",
        "digest_inputs",
    }
    if not isinstance(shared, dict) or required - shared.keys():
        raise CrabError("paired shared inputs lack pins/policy/coverage/budget/held-out metadata")
    try:
        as_of = datetime.fromisoformat(shared["as_of"])
    except (ValueError, TypeError) as exc:
        raise CrabError("paired as-of time must be an ISO timestamp") from exc
    if as_of.tzinfo is None:
        raise CrabError("paired as-of time must include a timezone")
    for side in ("receiving", "prey"):
        pins = shared[side]
        if (
            not isinstance(pins, dict)
            or set(pins) != set(pairs)
            or any(
                not isinstance(pin, dict)
                or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", str(pin.get("sha", ""))) is None
                for pin in pins.values()
            )
        ):
            raise CrabError(f"paired {side} inputs need an immutable SHA for every pair")
    for field in ("policy", "ledger"):
        pins = shared[field]
        if (
            not isinstance(pins, dict)
            or set(pins) != set(pairs)
            or any(re.fullmatch(r"[a-f0-9]{64}", str(value)) is None for value in pins.values())
        ):
            raise CrabError(f"paired {field} inputs need frozen fingerprints for every pair")
    digests = shared["digest_inputs"]
    if (
        not isinstance(digests, dict)
        or set(digests) != set(pairs)
        or any(
            not isinstance(sides, dict)
            or set(sides) != {"prey", "maw"}
            or any(re.fullmatch(r"[a-f0-9]{64}", str(pin)) is None for pin in sides.values())
            for sides in digests.values()
        )
    ):
        raise CrabError("paired digest inputs need frozen prey/maw fingerprints for every pair")
    if any(
        not isinstance(pin.get("worktree"), str) or pin["worktree"] in {"", "unknown"}
        for pin in shared["receiving"].values()
    ):
        raise CrabError("paired receiving inputs need a known worktree identity")
    if any(
        "selected_ref" not in pin
        or (pin["selected_ref"] is not None and not isinstance(pin["selected_ref"], str))
        for pin in shared["receiving"].values()
    ):
        raise CrabError("paired receiving inputs need a selected ref or explicit detached null")
    arm_sources = plan.get("arm_sources")
    if not isinstance(arm_sources, dict) or set(arm_sources) != set(arms):
        raise CrabError("paired plan must pin each installed arm source/version")
    for source in arm_sources.values():
        if (
            not isinstance(source, dict)
            or not source.get("version")
            or re.fullmatch(r"[a-f0-9]{64}", str(source.get("source_sha256", ""))) is None
        ):
            raise CrabError("paired arm source pin is incomplete")
    schedule = [
        {
            "id": "run-" + fingerprint([pair, repeat, arm])[:20],
            "pair": pair,
            "repeat": repeat,
            "arm": arm,
        }
        for pair in pairs
        for repeat in range(1, repeats + 1)
        for arm in (arms if repeat % 2 else list(reversed(arms)))
    ]
    payload = {
        "plan": plan,
        "schedule": schedule,
        "frozen_at": datetime.now(UTC).isoformat(),
        "shared_inputs_sha256": fingerprint(shared),
    }
    payload["sha256"] = fingerprint(payload)
    output.mkdir(parents=True, exist_ok=False)
    _new(output / "experiment.json", payload)
    return output


def _experiment(root: Path) -> dict[str, Any]:
    data = read_object(root / "experiment.json")
    seal = data.pop("sha256", None)
    if fingerprint(data) != seal:
        raise CrabError("paired experiment plan changed after freezing")
    data["sha256"] = seal
    return data


def seal(root: Path, run_id: str, raw: Path, measurements: Path) -> Path:
    experiment = _experiment(root)
    run_id = _name(run_id)
    run = next((run for run in experiment["schedule"] if run["id"] == run_id), None)
    if run is None:
        raise CrabError("run is outside the preregistered paired schedule")
    costs = read_object(measurements)
    for key in METRICS:
        value = costs.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value < 0
        ):
            raise CrabError(f"paired measurement {key} must be finite/nonnegative or null")
    source = costs.get("installed_source")
    if (
        not isinstance(source, dict)
        or re.fullmatch(r"[a-f0-9]{64}", str(source.get("source_sha256", ""))) is None
        or not source.get("version")
    ):
        raise CrabError("paired run requires installed implementation identity")
    expected = experiment["plan"]["arm_sources"][run["arm"]]
    if any(source.get(key) != expected[key] for key in ("version", "source_sha256")):
        raise CrabError("paired installed arm differs from its preregistered source")
    receipt = {
        "run": run,
        "experiment": experiment["sha256"],
        "files": _inventory(raw),
        "measurements": costs,
        "shared_inputs": experiment["shared_inputs_sha256"],
    }
    receipt["sha256"] = fingerprint(receipt)
    path = root / (run_id + ".json")
    _new(path, receipt)
    return path


def _semantic(value: object) -> object:
    """Only declared transport/provenance fields are ignored; evidence and policy remain."""
    ignored = {"compared_at", "exploration_snapshot"}
    if isinstance(value, dict):
        return {key: _semantic(item) for key, item in value.items() if key not in ignored}
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def report(root: Path, roots: dict[str, str]) -> dict[str, Any]:
    experiment = _experiment(root)
    result = []
    raw_hashes: set[str] = set()
    ids_by_context: set[tuple[str, str]] = set()
    literal_ids: set[str] = set()
    semantics: dict[tuple[str, int], dict[str, str]] = {}
    for run in experiment["schedule"]:
        receipt = read_object(root / (run["id"] + ".json"))
        seal_hash = receipt.pop("sha256", None)
        if (
            fingerprint(receipt) != seal_hash
            or receipt.get("experiment") != experiment["sha256"]
            or receipt.get("run") != run
        ):
            raise CrabError("paired run receipt is stale or corrupt")
        if run["id"] not in roots:
            raise CrabError("paired report needs an explicit raw-artifact roots map")
        raw = Path(roots[run["id"]])
        files = _inventory(raw)
        if files != receipt["files"]:
            raise CrabError("paired raw artifacts changed after sealing")
        raw_hash = fingerprint(files)
        duplicate_content = raw_hash in raw_hashes
        raw_hashes.add(raw_hash)
        menu = read_object(raw / "menu.json")
        shared = experiment["plan"]["shared_inputs"]
        if not _same_time(menu.get("generated_at"), shared["as_of"]):
            raise CrabError("paired menu differs from the frozen as-of clock")
        for side, frozen in (("maw", "receiving"), ("prey", "prey")):
            if menu.get(side, {}).get("sha") != shared[frozen][run["pair"]]["sha"]:
                raise CrabError(f"paired {side} differs from the frozen receiving/source pin")
        inputs = menu.get("analysis_inputs", {})
        if (
            fingerprint(inputs.get("policy")) != shared["policy"][run["pair"]]
            or ledger_identity(inputs.get("ledger")) != shared["ledger"][run["pair"]]
        ):
            raise CrabError("paired policy/ledger differs from the preregistered inputs")
        tokens = {}
        digest_evidence = {}
        for side in ("prey", "maw"):
            relative = experiment["plan"].get("digest_paths", {}).get(side, side + "-digest")
            directory = raw / _relative(relative)
            manifest = read_object(directory / "manifest.json")
            if digest_input_identity(manifest) != shared["digest_inputs"][run["pair"]][side]:
                raise CrabError(
                    f"paired {side} effective digest inputs differ from the frozen plan"
                )
            if (
                digest_integrity_errors(directory, manifest)
                or incomplete_miners(manifest)
                or manifest.get("coverage", {}).get("healthy") is not True
            ):
                raise CrabError(f"paired {side} digest is incomplete or inconsistent")
            if manifest.get("prey", {}).get("sha") != menu.get(side, {}).get("sha"):
                raise CrabError(f"paired {side} digest/menu identity mismatch")
            expected_pin = shared["receiving" if side == "maw" else "prey"][run["pair"]]
            if manifest.get("prey", {}).get("worktree") != expected_pin.get("worktree", "clean"):
                raise CrabError(f"paired {side} worktree differs from the frozen inputs")
            if not _same_time(manifest.get("generated_at"), shared["as_of"]):
                raise CrabError(f"paired {side} digest differs from the frozen as-of clock")
            if side == "maw" and (
                "selected_ref" not in menu.get("receiving_context", {})
                or menu["receiving_context"]["selected_ref"] != expected_pin["selected_ref"]
            ):
                raise CrabError("paired selected receiving ref differs from the frozen target")
            tokens[side] = token_estimates(manifest)
            digest_evidence[side] = {
                "coverage": manifest.get("coverage"),
                "budget": manifest.get("budget"),
                "shallow": manifest.get("prey", {}).get("shallow"),
                "depth": manifest.get("depth"),
                "as_of": manifest.get("generated_at"),
            }
        context = menu.get("receiving_context") or {}
        receiving = fingerprint(
            {
                "repository": menu.get("maw", {}).get("url") or menu.get("maw", {}).get("label"),
                "sha": menu.get("maw", {}).get("sha"),
                **{
                    key: context.get(key)
                    for key in ("worktree", "policy", "ledger", "selected_ref")
                },
            }
        )
        for card in menu.get("candidates", []):
            literal_ids.add(card["id"])
            ids_by_context.add((receiving, card["id"]))
        projection = project_menu(menu, raw)
        semantic_review = {
            key: value for key, value in projection.items() if key in {"counts", "cards", "phases"}
        }
        semantic_review["explorations"] = [
            {
                key: value
                for key, value in observation.items()
                if key not in {"snapshot", "report_path"}
            }
            for observation in projection["explorations"]
        ]
        semantic = fingerprint(
            _semantic(
                {
                    "review": semantic_review,
                    **{
                        key: menu.get(key)
                        for key in (
                            "candidates",
                            "hidden",
                            "initial_candidates",
                            "discovered_candidates",
                            "scoring",
                            "verdict",
                            "hunger",
                            "mode",
                        )
                    },
                }
            )
        )
        semantics.setdefault((run["pair"], run["repeat"]), {})[run["arm"]] = semantic
        result.append(
            {
                **run,
                "raw_sha256": raw_hash,
                "duplicate_artifact_content": duplicate_content,
                "artifact_bytes": sum((raw / _relative(name)).stat().st_size for name in files),
                "tokens": tokens,
                "digest_evidence": digest_evidence,
                "costs": {key: receipt["measurements"].get(key) for key in METRICS},
                "installed_source": receipt["measurements"]["installed_source"],
                "review": projection,
                "semantic_sha256": semantic,
            }
        )
    return {
        "experiment": experiment["sha256"],
        "runs": result,
        "distinct_literal_ids": len(literal_ids),
        "receiving_scoped_ids": len(ids_by_context),
        "unique_artifact_bundles": len(raw_hashes),
        "independent_semantic_acceptance_verified": False,
        "semantic_equal_pairs": [
            {"pair": pair, "repeat": repeat, "equal": len(set(arms.values())) == 1}
            for (pair, repeat), arms in semantics.items()
        ],
        "relocation": roots,
        "model_costs_are_not_artifact_token_estimates": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freezing = commands.add_parser("freeze")
    freezing.add_argument("plan", type=Path)
    freezing.add_argument("output", type=Path)
    sealing = commands.add_parser("seal")
    sealing.add_argument("experiment", type=Path)
    sealing.add_argument("run_id")
    sealing.add_argument("raw", type=Path)
    sealing.add_argument("measurements", type=Path)
    rendering = commands.add_parser("report")
    rendering.add_argument("experiment", type=Path)
    rendering.add_argument("roots", type=Path)
    rendering.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            freeze(args.plan, args.output)
        elif args.command == "seal":
            seal(args.experiment, args.run_id, args.raw, args.measurements)
        else:
            _new(args.output, report(args.experiment, read_object(args.roots)))
    except (CrabError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"paired report: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
