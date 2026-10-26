"""Run one installed release against fixed inputs; never import or execute their code."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import hungry_crab
from hungry_crab.cache import Slug, Target, prey_paths
from hungry_crab.compare import CompareOptions, compare_digests, write_meal
from hungry_crab.compare.candidates import Side
from hungry_crab.compare.scoring import Scoring
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.digest_integrity import digest_integrity_errors
from hungry_crab.fetch.github import GitHubClient
from hungry_crab.hunt import discover
from hungry_crab.ledger import Ledger
from hungry_crab.maw import MawConfig
from hungry_crab.memory import learn


def usage() -> tuple[float, float]:
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime, children.ru_utime + children.ru_stime


def measured(function: Any) -> tuple[Any, dict[str, float]]:
    wall, cpu = time.perf_counter(), usage()
    result = function()
    after = usage()
    return result, {
        "wall_seconds": time.perf_counter() - wall,
        "cpu_self_seconds": after[0] - cpu[0],
        "cpu_children_seconds": after[1] - cpu[1],
    }


def main() -> None:
    expected, repeat = sys.argv[1], int(sys.argv[2])
    if hungry_crab.__version__ != expected:
        raise ValueError("Wrong installed release")
    spec = json.loads(Path(__file__).with_name("spec.json").read_text())
    now = datetime.fromisoformat(spec["as_of_utc"])
    volume = Path(os.environ["CRAB_VOLUME"])
    inputs = volume / "inputs"
    output = volume / "experiment-output" / expected / str(repeat)
    output.mkdir(parents=True)
    records: dict[str, Any] = {"version": expected, "repeat": repeat, "digests": [], "pairs": []}
    digests: dict[str, Any] = {}
    configurations, ledgers = {}, {}
    sources = [*spec["maws"], *spec["sources"]]
    digest_start = time.perf_counter()
    for source in sources:
        slug = Slug.parse(source["repo"])
        repo = prey_paths(slug, inputs).repo
        is_maw = "id" in source
        config = MawConfig.load(repo) if is_maw else None
        key = source["repo"].replace("/", "--")
        options = DigestOptions(
            out=output / "digests" / key,
            cache_root=inputs,
            force=True,
            now=now,
            md_budget=3500,
            total_budget=30000,
            budget_policy="warn",
            ignore=config.ignore if config else [],
        )
        target = Target(path=repo) if is_maw else Target(slug=slug)
        result, costs = measured(lambda t=target, o=options: run_digest(t, o))
        if digest_integrity_errors(result.out_dir, result.manifest) or any(
            not m["ok"] for m in result.manifest["miners"]
        ):
            raise ValueError("Incomplete digest: " + source["repo"])
        if result.manifest["prey"]["sha"] != source["sha"]:
            raise ValueError("Digest pin differs")
        digests[source["repo"]] = result
        records["digests"].append(
            {
                "repo": source["repo"],
                "maw": is_maw,
                **costs,
                "total_tokens_est": result.manifest.get("total_tokens_est"),
                "md_tokens_est": result.manifest.get("md_tokens_est"),
                "budget": result.manifest.get("budget"),
                "coverage": result.manifest.get("coverage"),
                "license": json.loads((result.out_dir / "license.json").read_text()).get("spdx"),
            }
        )
        if config:
            configurations[source["id"]] = config
            ledgers[source["id"]] = Ledger.load(config.ledger_path(inputs), maw=repo.name)
    records["all_digests_wall_seconds"] = time.perf_counter() - digest_start
    snapshots = volume / "experiment-output" / "search-snapshots"
    snapshots.mkdir(exist_ok=True)

    class FrozenSearch:
        def __init__(self) -> None:
            self.requests = 0
            self.network_requests = 0

        def get(self, path: str) -> Any:
            self.requests += 1
            key = hashlib.sha256(path.encode()).hexdigest()
            saved = snapshots / (key + ".json")
            if saved.exists():
                return json.loads(saved.read_text())["response"]
            self.network_requests += 1
            response = GitHubClient().get(path)
            saved.write_text(json.dumps({"path": path, "response": response}, indent=2))
            return response

    records["hunt"] = []
    for maw in spec["maws"]:
        config, ledger = configurations[maw["id"]], ledgers[maw["id"]]
        search = FrozenSearch()
        hunt_report, hunt_costs = measured(
            lambda: discover(
                Side.load(digests[maw["repo"]].out_dir, root=config.root),
                config,
                ledger,
                replace(config.hunt, licenses=["MIT"], limit=10, max_candidates=50),
                client=search,
                now=now,
            )
        )
        (output / "hunt").mkdir(exist_ok=True)
        (output / "hunt" / (maw["id"] + ".json")).write_text(json.dumps(hunt_report, indent=2))
        records["hunt"].append(
            {
                "maw": maw["id"],
                **hunt_costs,
                "requests": search.requests,
                "network_requests": search.network_requests,
            }
        )
        scoring, memory = learn(
            ledger,
            Scoring.default().merged(config.scoring),
            config.memory,
            overrides=config.scoring,
        )
        options = CompareOptions(
            hunger=config.hunger,
            scoring=scoring.to_dict(),
            memory=memory.to_dict(),
            ignore=config.ignore,
            top=30,
            maw_license=config.license,
            hidden_ids=ledger.hidden_ids(),
            now=now,
            maw_config=config,
            mode=config.mode,
        )
        for requested in spec["sets"][maw["id"]]:
            source = next(s for s in spec["sources"] if s["requested"] == requested)
            prey_repo = prey_paths(Slug.parse(source["repo"]), inputs).repo
            result, costs = measured(
                lambda s=source, p=prey_repo: compare_digests(
                    digests[s["repo"]].out_dir,
                    digests[maw["repo"]].out_dir,
                    prey_root=p,
                    prey_objects=p,
                    maw_root=config.root,
                    options=options,
                )
            )
            pair_dir = output / "pairs" / maw["id"] / source["repo"].replace("/", "--")
            result.menu["analysis_inputs"] = {
                "ledger": ledger.to_dict(now=now),
                "policy": config.raw,
            }
            write_meal(result, pair_dir)
            semantic = {
                "candidates": result.menu["candidates"],
                "hidden": result.menu["hidden"],
                "initial_candidates": result.menu["initial_candidates"],
            }
            records["pairs"].append(
                {
                    "maw": maw["id"],
                    "prey": source["repo"],
                    **costs,
                    "initial": len(result.menu["initial_candidates"]),
                    "visible": len(result.candidates),
                    "hidden": len(result.hidden),
                    "shown": len(result.shown),
                    "semantic_sha256": hashlib.sha256(
                        json.dumps(semantic, sort_keys=True).encode()
                    ).hexdigest(),
                }
            )
            print(json.dumps(records["pairs"][-1]), flush=True)
    records["peak_process_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    records["peak_child_rss_bytes"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
    records["source_hash"] = hashlib.sha256(Path(hungry_crab.__file__).read_bytes()).hexdigest()
    (output / "measurements.json").write_text(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
