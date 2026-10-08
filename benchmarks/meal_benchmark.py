"""B2 experiment ledger: freeze, record, blind, validate and report real agent runs.

This program never launches an agent, judges a card or estimates a bill. It imports outputs
from the same operator-selected harness for every arm. Missing runs/judgments are errors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import secrets
import statistics
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from hungry_crab.cache import Slug
from hungry_crab.fetch.git import GitRunner
from hungry_crab.fetch.providers import client_for
from hungry_crab.nutrients import CATEGORIES, EFFORTS, RISKS

SCHEMA = "hungry-crab.meal-benchmark/1"
MODES = ("COPY", "COPY_FILE", "REIMPLEMENT", "IDEAS_ONLY", "HUMAN")
CARD_FIELDS = (
    "category",
    "title",
    "what",
    "why",
    "how",
    "evidence",
    "license_mode",
    "effort",
    "risk",
)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
SHA = re.compile(r"[0-9a-f]{40}")


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def named(value: object) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f"invalid experiment identifier: {value!r}")
    return value


def pinned(value: object) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("all repository and crab commits must be full immutable SHA pins")
    return value


def freeze(spec_file: Path, out: Path) -> dict[str, Any]:
    spec = read(spec_file)
    if not isinstance(spec, dict) or not spec.get("arms") or not spec.get("prey"):
        raise ValueError("spec must declare maw, prey, arms, prompt and rubric")
    if out.exists():
        raise ValueError("choose a fresh sweep directory; an experiment is never overwritten")
    pinned(spec["maw"]["sha"])
    for prey in spec["prey"]:
        named(prey["id"])
        pinned(prey["sha"])
        if prey.get("license_mode") not in MODES or not isinstance(prey.get("must"), list):
            raise ValueError("each prey needs a frozen license ceiling and must golden ids")
    prey_ids = [p["id"] for p in spec["prey"]]
    if len(prey_ids) != len(set(prey_ids)):
        raise ValueError("duplicate prey ids")
    arms = [a["id"] for a in spec["arms"]]
    if len(arms) != len(set(arms)):
        raise ValueError("duplicate arm ids")
    assets: dict[str, bytes] = {}
    for arm in spec["arms"]:
        named(arm["id"])
        if not isinstance(arm.get("model"), str) or not arm["model"] or not arm.get("harness"):
            raise ValueError("each arm needs the exact model id and common harness configuration")
        ceiling = arm["harness"].get("token_ceiling")
        if type(ceiling) is not int or ceiling <= 0:
            raise ValueError("the common harness needs a positive token_ceiling")
        if arm.get("crab_sha") is not None:
            pinned(arm["crab_sha"])
        source = (spec_file.parent / arm["prompt"]).resolve()
        assets[f"prompt-{arm['id']}.md"] = source.read_bytes()
        arm["prompt"] = f"frozen/prompt-{arm['id']}.md"
        arm["prompt_sha256"] = digest(assets[f"prompt-{arm['id']}.md"])
    if len({json.dumps(a["harness"], sort_keys=True) for a in spec["arms"]}) != 1:
        raise ValueError("all arms must use the same harness; compare model/version separately")
    assets["rubric.md"] = (spec_file.parent / spec["rubric"]).resolve().read_bytes()
    spec["rubric_sha256"] = digest(assets["rubric.md"])
    spec["schema"] = SCHEMA
    repeats = spec.get("repeats", 2)
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 2:
        raise ValueError("a sweep requires at least two repeats per arm/prey")
    spec["runs"] = [
        {"id": f"{a['id']}.{p['id']}.{r}", "arm": a["id"], "prey": p["id"], "repeat": r}
        for a in spec["arms"]
        for p in spec["prey"]
        for r in range(1, repeats + 1)
    ]
    spec["manifest_sha256"] = digest(json.dumps(spec, sort_keys=True).encode())
    out.mkdir(parents=True)
    for name, body in assets.items():
        destination = out / "frozen" / name
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(body)
    write(out / "manifest.json", spec)
    return spec


def manifest(sweep: Path) -> dict[str, Any]:
    data = read(sweep / "manifest.json")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("not a B2 sweep")
    sealed = {k: v for k, v in data.items() if k != "manifest_sha256"}
    if digest(json.dumps(sealed, sort_keys=True).encode()) != data.get("manifest_sha256"):
        raise ValueError("the frozen setup changed")
    if digest((sweep / "frozen/rubric.md").read_bytes()) != data["rubric_sha256"]:
        raise ValueError("the frozen rubric changed")
    for arm in data["arms"]:
        if digest((sweep / arm["prompt"]).read_bytes()) != arm["prompt_sha256"]:
            raise ValueError("a frozen arm prompt changed")
    return data


def normalize(cards: object) -> list[dict[str, Any]]:
    if not isinstance(cards, list) or len(cards) > 1000:
        raise ValueError("cards must be a list of at most 1000 nutrient records")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in cards:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            raise ValueError("every card needs an original id")
        if raw["id"] in ids:
            raise ValueError("duplicate card ids in one run")
        ids.add(raw["id"])
        card = {key: raw.get(key) for key in CARD_FIELDS}
        for key in ("title", "what", "why", "how"):
            if not isinstance(card[key], str) or len(card[key]) > 10000:
                raise ValueError(f"invalid card field {key}")
            card[key] = " ".join(card[key].split())
        if (
            card["category"] not in CATEGORIES
            or card["license_mode"] not in MODES
            or card["effort"] not in EFFORTS
            or card["risk"] not in RISKS
        ):
            raise ValueError("invalid category, license mode, effort or risk")
        if not isinstance(card["evidence"], list) or len(card["evidence"]) > 100:
            raise ValueError("invalid evidence list")
        evidence = []
        for item in card["evidence"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("evidence requires a path")
            evidence.append({"path": item["path"], "url": item.get("url")})
        result.append({"id": raw["id"], **card, "evidence": evidence})
    return result


def record(sweep: Path, run_id: str, cards_file: Path, usage_file: Path, transcript: Path) -> None:
    setup = manifest(sweep)
    named(run_id)
    if run_id not in {r["id"] for r in setup["runs"]}:
        raise ValueError("run is not in the frozen experiment")
    out = sweep / "runs" / run_id
    if out.exists():
        raise ValueError("a recorded run cannot be overwritten")
    cards = normalize(read(cards_file))
    usage = read(usage_file)
    for key in ("wall_seconds", "tokens_in", "tokens_out", "cost_usd"):
        value = usage.get(key)
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"usage needs a measured nonnegative {key}")
    for key in ("tokens_in", "tokens_out"):
        if not isinstance(usage[key], int):
            raise ValueError("token counts must be integers")
    run = next(r for r in setup["runs"] if r["id"] == run_id)
    arm = next(a for a in setup["arms"] if a["id"] == run["arm"])
    if usage["tokens_in"] + usage["tokens_out"] > arm["harness"]["token_ceiling"]:
        raise ValueError("the measured run exceeded the frozen common token ceiling")
    body = transcript.read_bytes()
    if not body:
        raise ValueError("the actual harness transcript is required")
    write(out / "nutrients.json", cards)
    write(out / "usage.json", {**usage, "transcript_sha256": digest(body)})
    (out / "transcript.log").write_bytes(body)
    write(
        out / "receipt.json",
        {
            name: digest((out / name).read_bytes())
            for name in ("nutrients.json", "usage.json", "transcript.log")
        },
    )
    (out / "nutrients.md").write_text(
        "\n\n".join(f"## {c['title']}\n\n{c['what']}\n\n{c['why']}\n\n{c['how']}" for c in cards)
        + "\n",
        encoding="utf-8",
    )


def verify_run(out: Path) -> None:
    receipt = read(out / "receipt.json")
    for name in ("nutrients.json", "usage.json", "transcript.log"):
        if digest((out / name).read_bytes()) != receipt.get(name):
            raise ValueError("a recorded run changed")


def pool(sweep: Path) -> dict[str, Any]:
    setup = manifest(sweep)
    if (sweep / "blind").exists():
        raise ValueError("the blind batch is already frozen")
    private: dict[str, Any] = {}
    by_prey: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tokens = [
        str(a[key]) for a in setup["arms"] for key in ("id", "model", "crab_sha") if a.get(key)
    ]
    for run in setup["runs"]:
        verify_run(sweep / "runs" / run["id"])
        cards = normalize(read(sweep / "runs" / run["id"] / "nutrients.json"))
        for card in cards:
            blind_id = secrets.token_hex(12)
            private[blind_id] = {"run": run["id"], "prey": run["prey"], "original_id": card["id"]}
            clean = {k: v for k, v in card.items() if k not in {"id", "evidence", "license_mode"}}
            clean["evidence"] = [{"path": e["path"]} for e in card["evidence"]]
            for key in ("title", "what", "why", "how"):
                for token in tokens:
                    clean[key] = re.sub(
                        rf"(?<!\w){re.escape(token)}(?!\w)",
                        "[hidden]",
                        clean[key],
                        flags=re.IGNORECASE,
                    )
            by_prey[run["prey"]].append({"id": blind_id, **clean})
    audit_ids: list[str] = []
    for prey, cards in by_prey.items():
        random.SystemRandom().shuffle(cards)
        write(sweep / "blind" / f"{prey}.value.json", cards)
        audit_ids.extend(c["id"] for c in cards[: math.ceil(len(cards) * 0.2)])
        facts = []
        for card in cards:
            identity = private[card["id"]]
            originals = read(sweep / "runs" / identity["run"] / "nutrients.json")
            original = next(c for c in originals if c["id"] == identity["original_id"])
            facts.append(
                {
                    "id": card["id"],
                    "evidence": original["evidence"],
                    "license_mode": original["license_mode"],
                    "what": card["what"],
                }
            )
        write(sweep / "blind" / f"{prey}.facts.json", facts)
    write(sweep / "private/batch-map.json", private)
    write(sweep / "blind/audit-ids.json", audit_ids)
    write(
        sweep / "private/pool-receipt.json",
        {
            path.relative_to(sweep).as_posix(): digest(path.read_bytes())
            for path in [
                sweep / "private/batch-map.json",
                *sorted((sweep / "blind").glob("*.json")),
            ]
        },
    )
    return private


def judgments(path: Path, ids: set[str], *, value: bool) -> dict[str, dict[str, Any]]:
    raw = read(path)
    if not isinstance(raw, list) or len(raw) != len(ids):
        raise ValueError("judgments must cover every expected id exactly once")
    result = {}
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("id") not in ids or entry["id"] in result:
            raise ValueError("unknown or duplicate judged id")
        if value:
            if (
                type(entry.get("useful")) is not bool
                or type(entry.get("garbage")) is not bool
                or entry["useful"] == entry["garbage"]
                or type(entry.get("quality")) is not int
                or not 0 <= entry["quality"] <= 3
                or not isinstance(entry.get("reason"), str)
            ):
                raise ValueError(
                    "value judgments need useful XOR garbage, quality 0..3 and a reason"
                )
        elif (
            type(entry.get("evidence_ok")) is not bool or type(entry.get("license_ok")) is not bool
        ):
            raise ValueError("fact judgments need evidence_ok and license_ok")
        result[entry["id"]] = entry
    return result


def verify_path(root: Path, sha: str, path: str) -> bool:
    parts = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or "\0" in path
        or parts.is_absolute()
        or any(part in {"..", ".git"} for part in parts.parts)
        or ":" in path
    ):
        return False
    try:
        if GitRunner(root).run("cat-file", "-t", f"{pinned(sha)}:{path}").strip() != "blob":
            return False
    except Exception:
        return False
    return True


def fetch_evidence(sweep: Path, out: Path) -> dict[str, str]:
    """Fetch pinned Git objects only. Never check out, install, test or build a prey."""
    setup = manifest(sweep)
    if out.exists():
        raise ValueError("choose a fresh evidence directory")
    roots: dict[str, str] = {}
    for prey in setup["prey"]:
        slug = Slug.parse(prey["slug"])
        sha = pinned(prey["sha"])
        root = (out / named(prey["id"])).resolve()
        root.mkdir(parents=True)
        git = GitRunner(root, timeout=300, github_token=client_for(slug).token, auth_host=slug.host)
        git.run("-c", "init.templateDir=", "init", "--quiet")
        git.run("fetch", "--quiet", "--depth", "1", "--no-tags", slug.clone_url, sha)
        if git.run("rev-parse", "FETCH_HEAD").strip() != sha:
            raise ValueError("provider did not return the frozen prey commit")
        roots[prey["id"]] = str(root)
    write(out / "roots.json", roots)
    return roots


def report(sweep: Path, roots: dict[str, str]) -> dict[str, Any]:
    setup = manifest(sweep)
    receipt = read(sweep / "private/pool-receipt.json")
    for name, expected in receipt.items():
        if digest((sweep / name).read_bytes()) != expected:
            raise ValueError("a frozen blind batch or identity map changed")
    private = read(sweep / "private/batch-map.json")
    ids = set(private)
    value = judgments(sweep / "judged/value.json", ids, value=True)
    repeat = judgments(sweep / "judged/value-repeat.json", ids, value=True)
    facts = judgments(sweep / "judged/facts.json", ids, value=False)
    audit_ids = set(read(sweep / "blind/audit-ids.json"))
    audit = judgments(sweep / "judged/audit.json", audit_ids, value=True)
    disagreement = sum(value[i]["useful"] != audit[i]["useful"] for i in audit_ids) / max(
        1, len(audit_ids)
    )
    agreement = sum(value[i]["useful"] == repeat[i]["useful"] for i in ids) / max(1, len(ids))
    rows: list[dict[str, Any]] = []
    for run in setup["runs"]:
        verify_run(sweep / "runs" / run["id"])
        prey = next(p for p in setup["prey"] if p["id"] == run["prey"])
        if run["prey"] not in roots:
            raise ValueError("provide a local pinned prey checkout for every fact-check arm")
        cards = read(sweep / "runs" / run["id"] / "nutrients.json")
        usage = read(sweep / "runs" / run["id"] / "usage.json")
        body = (sweep / "runs" / run["id"] / "transcript.log").read_bytes()
        if digest(body) != usage["transcript_sha256"]:
            raise ValueError("a recorded transcript changed")
        aliases = {v["original_id"]: k for k, v in private.items() if v["run"] == run["id"]}
        useful = garbage = fabricated = license_errors = 0
        qualities: list[int] = []
        for card in cards:
            alias = aliases[card["id"]]
            grade, fact = value[alias], facts[alias]
            useful += grade["useful"]
            garbage += grade["garbage"]
            if grade["useful"]:
                qualities.append(grade["quality"])
            # Provider references require the factual judge. Git paths are independently
            # checked against git objects at the frozen commit, never the mutable worktree.
            paths_ok = all(
                e["path"].startswith("provider:")
                or verify_path(Path(roots[run["prey"]]), prey["sha"], e["path"])
                for e in card["evidence"]
            )
            fabricated += not fact["evidence_ok"] or not paths_ok
            license_errors += not fact["license_ok"] or MODES.index(
                card["license_mode"]
            ) < MODES.index(prey["license_mode"])
        must = set(prey["must"])
        recall = len(must & {c["id"] for c in cards}) / len(must) if must else 1.0
        precision = useful / max(1, useful + garbage)
        quality = statistics.mean(qualities) if qualities else 0.0
        tokens = usage["tokens_in"] + usage["tokens_out"]
        rows.append(
            {
                "run": run["id"],
                "arm": run["arm"],
                "prey": run["prey"],
                **usage,
                "n_proposed": len(cards),
                "n_useful": useful,
                "n_garbage": garbage,
                "n_disputed": sum(
                    value[k]["useful"] != repeat[k]["useful"] for k in aliases.values()
                ),
                "n_fabricated": fabricated,
                "n_license_errors": license_errors,
                "quality_mean": quality,
                "precision": precision,
                "recall_must": recall,
                "useful_per_100k": useful * 100000 / tokens if tokens else None,
                "meal_score": recall * precision * quality / 3,
            }
        )
    aggregates = []
    metrics = (
        "precision",
        "quality_mean",
        "recall_must",
        "useful_per_100k",
        "meal_score",
        "cost_usd",
        "wall_seconds",
    )
    for arm in setup["arms"]:
        for prey in setup["prey"]:
            group = [row for row in rows if row["arm"] == arm["id"] and row["prey"] == prey["id"]]
            if len(group) < 2:
                raise ValueError("single-run findings are forbidden")
            aggregates.append(
                {
                    "arm": arm["id"],
                    "prey": prey["id"],
                    "repeats": len(group),
                    "metrics": {
                        key: {
                            "median": statistics.median(values),
                            "min": min(values),
                            "max": max(values),
                        }
                        if (values := [r[key] for r in group if r[key] is not None])
                        else None
                        for key in metrics
                    },
                }
            )
    result = {
        "schema": SCHEMA,
        "rubric_suspect": disagreement > 0.15,
        "audit_disagreement": disagreement,
        "judge_self_agreement": agreement,
        "valid": disagreement <= 0.15
        and all(not r["n_fabricated"] and not r["n_license_errors"] for r in rows),
        "runs": rows,
        "aggregates": aggregates,
    }
    write(sweep / "report.json", result)
    lines = [
        "# B2 meal sweep",
        "",
        (
            f"Valid: {result['valid']}; audit disagreement: {disagreement:.1%}; "
            f"judge self-agreement: {agreement:.1%}."
        ),
        "",
        "| Arm | Prey | Repeats | Precision (median) | Quality (median) | Cost USD (median) |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {a['arm']} | {a['prey']} | {a['repeats']} | "
        f"{a['metrics']['precision']['median']:.3f} | "
        f"{a['metrics']['quality_mean']['median']:.3f} | "
        f"{a['metrics']['cost_usd']['median']:.4f} |"
        for a in aggregates
    )
    (sweep / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("freeze")
    prepare.add_argument("spec", type=Path)
    prepare.add_argument("out", type=Path)
    capture = sub.add_parser("record")
    capture.add_argument("sweep", type=Path)
    capture.add_argument("run")
    capture.add_argument("cards", type=Path)
    capture.add_argument("usage", type=Path)
    capture.add_argument("transcript", type=Path)
    blind = sub.add_parser("pool")
    blind.add_argument("sweep", type=Path)
    summarize = sub.add_parser("report")
    summarize.add_argument("sweep", type=Path)
    summarize.add_argument("roots", type=Path, help="JSON mapping prey ids to local Git checkouts")
    evidence = sub.add_parser("fetch-evidence")
    evidence.add_argument("sweep", type=Path)
    evidence.add_argument("out", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            freeze(args.spec, args.out)
        elif args.command == "record":
            record(args.sweep, args.run, args.cards, args.usage, args.transcript)
        elif args.command == "pool":
            pool(args.sweep)
        elif args.command == "fetch-evidence":
            fetch_evidence(args.sweep, args.out)
        else:
            return 0 if report(args.sweep, read(args.roots))["valid"] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"B2: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
