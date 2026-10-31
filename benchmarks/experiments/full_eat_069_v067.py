"""Read-only public feast comparison. Never execute programs from a maw or prey."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime

SCHEMA = "hungry-crab.feast-comparison/1"


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def utc():
    return datetime.now(UTC).isoformat(timespec="seconds")


def git(root, *args, binary=False):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                            check=True, capture_output=True, timeout=90)
    return result.stdout if binary else result.stdout.decode("utf-8", "replace").strip()


def run_child(script, args, log, timeout=1800):
    started = time.perf_counter()
    with Path(log).open("w", encoding="utf-8") as handle:
        process = subprocess.Popen([sys.executable, "-B", str(script), *args], stdout=handle, stderr=subprocess.STDOUT,
                                   cwd=Path.cwd(), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            import signal
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            raise TimeoutError("bounded experiment child exceeded its time limit")
    return {"exit_code": process.returncode, "wall_seconds": time.perf_counter()-started}


def semantic(menu):
    keys = ("prey", "maw", "verdict", "mode", "hunger", "scoring", "memory", "counts",
            "candidates", "initial_candidates", "hidden", "analysis_inputs", "post_comparison_worktree")
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in {"compared_at", "generated_at"}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return clean({k: menu.get(k) for k in keys})


def source_packet(root, repo, patterns, output, max_files=40):
    """Keep authenticated inert Git objects and bounded excerpts; no worktree execution."""
    root = Path(root)
    sha = git(root, "rev-parse", "HEAD")
    listing = git(root, "ls-tree", "-r", "-z", sha, binary=True)
    available = {}
    for entry in listing.split(b"\0"):
        if not entry or b"\t" not in entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, typ, object_sha = meta.decode().split()
        path = raw_path.decode("utf-8", "replace")
        if mode in {"100644", "100755"} and typ == "blob" and "\ufffd" not in path and len(path) < 400:
            available[path] = object_sha
    selected = []
    missing = []
    for pattern in patterns:
        matches = sorted(p for p in available if fnmatch.fnmatchcase(p, pattern))
        if not matches:
            missing.append(pattern)
        for path in matches[:2]:
            if path not in selected and len(selected) < max_files:
                selected.append(path)
    grants = sorted(p for p in available if (PurePosixPath(p).name.lower().startswith(("license", "copying", "notice"))
                                             or p.lower().endswith(".license")) and len(PurePosixPath(p).parts) <= 2)
    for path in [".crab.yml", ".crab/ledger.json", "pyproject.toml", "package.json", *grants[:12]]:
        if path in available and path not in selected:
            selected.append(path)
    inert = Path(output).parent / (Path(output).stem + "-objects")
    subprocess.run(["git", "-c", "init.templateDir=", "init", "--bare", str(inert)], check=True,
                   capture_output=True, timeout=30)
    seen = set()
    def retain(object_id):
        if object_id in seen:
            return
        typ = git(root, "cat-file", "-t", object_id)
        content = git(root, "cat-file", typ, object_id, binary=True)
        if len(content) > 3_000_000:
            raise ValueError("object exceeds packet object limit")
        write = subprocess.run(["git", "--git-dir", str(inert), "hash-object", "-w", "-t", typ, "--stdin"],
                               input=content, check=True, capture_output=True, timeout=30)
        if write.stdout.decode().strip() != object_id:
            raise ValueError("Git object digest mismatch")
        seen.add(object_id)
    retain(sha)
    retain(git(root, "rev-parse", sha+"^{tree}"))
    records = []
    keywords = re.compile(r"license|copyright|notice|vendor|generated|shebang|override|scope|exclude|curation|resolution|origin|"
                          r"fingerprint|timeout|partial|token|diff|cache|lock|prune|retention|state|increment|raw|"
                          r"metadata|retry|queue|overflow|rollback|expir|version|namespace|import|baseline|"
                          r"migration|error|example|preview|reus|contribut|accept|accessib|sidebar|execut|missing",
                          re.IGNORECASE)
    for path in selected:
        object_id = available[path]
        size = int(git(root, "cat-file", "-s", object_id))
        if size > 524288:
            records.append({"path": path, "blob": object_id, "state": "byte-budget-deferred", "bytes": size})
            continue
        raw = git(root, "cat-file", "blob", object_id, binary=True)
        if b"\0" in raw:
            records.append({"path": path, "blob": object_id, "state": "binary-not-read"})
            continue
        retain(object_id)
        parts = PurePosixPath(path).parts
        for i in range(1, len(parts)):
            retain(git(root, "rev-parse", sha+":"+PurePosixPath(*parts[:i]).as_posix()))
        lines = raw.decode("utf-8", "replace").splitlines()
        centers = [i for i, line in enumerate(lines) if keywords.search(line)]
        windows = []
        for center in [0, *centers, max(0, len(lines)-35)]:
            lo = max(0, center-12)
            hi = min(len(lines), lo+60)
            if any(abs(lo-v["start"]+1) < 50 for v in windows):
                continue
            windows.append({"start": lo+1, "end": hi, "text": "\n".join(lines[lo:hi])})
            if len(windows) >= 3:
                break
        records.append({"path": path, "blob": object_id, "bytes": size, "lines": len(lines), "state": "sampled",
                        "url": "https://github.com/"+repo+"/blob/"+sha+"/"+path, "windows": windows})
    subprocess.run(["git", "--git-dir", str(inert), "update-ref", "refs/heads/pinned", sha],
                   check=True, capture_output=True, timeout=30)
    dump(output, {"repository": repo, "sha": sha, "coverage": "bounded-pattern-selected", "missing_patterns": missing,
                  "files": records, "objects": str(inert), "retained_objects": len(seen)})


def child_acquire(args):
    sys.path.insert(0, str(Path(args.arm) / "src"))
    from hungry_crab.cache import Slug
    from hungry_crab.fetch.catch import CatchOptions, catch
    from hungry_crab.analysis_context import installed_source_identity
    owner, name = args.repo.split("/")
    from hungry_crab.sniff import sniff
    sniff(Slug(owner, name), cache_root=Path(args.cache), log=lambda s: print(s, flush=True))
    provider = read_marker(Path(args.cache)/"github"/owner/name/"api"/"repo.json")
    if provider.get("private") is not False:
        raise ValueError("refusing an unconfirmed public input before Git acquisition")
    from hungry_crab.fetch.acquisition import AcquisitionBudget
    from hungry_crab.fetch.git import GitRunner
    from hungry_crab.fetch.cache_identity import atomic_cache_text
    boundary = AcquisitionBudget(Path(args.volume), Path(args.cache), 1073741824, 8589934592)
    with boundary.scope():
        result = catch(Slug(owner, name), CatchOptions(shallow=True, wiki=False, issues=0,
                       max_download_bytes=1073741824, max_disk_bytes=8589934592),
                       cache_root=Path(args.cache), log=lambda s: print(s, flush=True))
        if result.sha != args.expected:
            runner = GitRunner(Path(result.repo_dir), github_token=os.environ.get("GITHUB_TOKEN"))
            boundary.require_url(result.url)
            runner.run("fetch", "--no-tags", "--depth=1", "origin", args.expected)
            runner.run("-c", "core.hooksPath=/dev/null", "reset", "--hard", "--quiet", args.expected)
            if runner.head_sha() != args.expected:
                raise ValueError("frozen revision could not be established")
            result.sha = args.expected
            atomic_cache_text(Path(result.repo_dir).parent/"catch.json",
                              json.dumps(result.to_dict(), indent=2)+"\n",
                              cache_root=Path(args.cache))
        boundary.check()
    dump(args.result, {"catch": result.to_dict(), "tool": installed_source_identity(), "completed_at": utc()})
    print("ACQUISITION_RESULT "+json.dumps({"repo": args.repo, "sha": result.sha}), flush=True)


def child_compare(args):
    sys.path.insert(0, str(Path(args.arm) / "src"))
    from hungry_crab.analysis_context import installed_source_identity
    from hungry_crab.cache import Slug, Target
    from hungry_crab.compare import compare_for_maw
    from hungry_crab.digest import DigestOptions
    sys.path.insert(1, str(Path(args.arm) / "benchmarks"))
    from paired_report import digest_input_identity
    import resource

    snapshot = json.loads(Path(args.snapshot).read_text())
    frozen = datetime.fromisoformat(snapshot["as_of"])
    owner, name = args.repo.split("/")
    started = time.perf_counter()
    cpu = time.process_time()
    before_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    result, prey_digest, ledger, config = compare_for_maw(
        Target(slug=Slug(owner, name)), Path(args.maw),
        digest_options=DigestOptions(cache_root=Path(args.cache), depth="normal", force=True, now=frozen),
        top=1000, now=frozen, record_ledger=False, issue_lookup=None, allow_partial=False,
        log=lambda s: print(s, flush=True))
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    measurement = {"wall_seconds": time.perf_counter()-started, "self_cpu_seconds": time.process_time()-cpu,
                   "child_cpu_seconds": children.ru_utime+children.ru_stime-before_children.ru_utime-before_children.ru_stime,
                   "self_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   "model_tokens": None, "currency": None, "cost_scope": "deterministic child process, no external model harness"}
    out = Path(args.result)
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(prey_digest.out_dir, out/"prey-digest")
    shutil.copytree(result.maw_dir, out/"maw-digest")
    for file in ("menu.json", "menu.md", "gap.md", "meal.json"):
        shutil.copy2(result.meal_dir/file, out/file)
    menu = result.menu
    manifests = {"prey": prey_digest.manifest, "maw": result.maw.manifest}
    observed_inputs = {side: digest_input_identity(value) for side, value in manifests.items()}
    source = installed_source_identity()
    record = {
        "schema": SCHEMA, "tool": source, "measurement": measurement,
        "prey": menu["prey"], "maw": menu["maw"], "counts": menu["counts"],
        "initial_count": len(menu.get("initial_candidates", [])), "verdict": menu["verdict"],
        "digest_inputs": observed_inputs, "analysis_inputs": menu.get("analysis_inputs"),
        "receiving_context": menu.get("receiving_context"), "semantic_sha256": digest(semantic(menu)),
        "coverage": {side: {"shallow": value.get("prey", {}).get("shallow"),
                          "inventory": value.get("inventory", value.get("summary", {})),
                          "warnings": value.get("warnings", [])} for side, value in manifests.items()},
        "completed_at": utc()}
    record["artifact_bytes"] = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    dump(out/"run.json", record)
    print("RUN_RESULT "+json.dumps({"tool": source, "counts": record["counts"], "verdict": record["verdict"],
                                    "semantic_sha256": record["semantic_sha256"], "wall_seconds": measurement["wall_seconds"]}), flush=True)


def read_marker(record):
    """The output record is a receipt, never an instruction."""
    return json.loads(Path(record).read_text(encoding="utf-8"))


def orchestrate(args):
    plan = read_marker(args.plan)
    batch = args.batch
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    dump(out/"plan.json", plan)
    volume = Path(args.volume).resolve()
    if not volume.is_mount() or volume == Path("/"):
        raise ValueError("comparison requires a dedicated mounted fixed-capacity volume")
    cache = volume/"cache"
    runtime = Path(os.environ["RUNNER_TEMP"])/("feast-arms-"+batch)
    runtime.mkdir(exist_ok=False)
    sources = {}
    for arm, expected in plan["arms"].items():
        actual = git(Path.cwd(), "rev-parse", expected+"^{commit}")
        if actual != expected:
            raise ValueError("tool tag differs from frozen source SHA")
        arm_root = runtime/arm
        arm_root.mkdir()
        archive = runtime/(arm+".tar")
        subprocess.run(["git", "archive", "--format=tar", "--output="+str(archive), expected, "src/hungry_crab", "benchmarks/paired_report.py", "skills/eat/SKILL.md", "docs/discovery.md"],
                       check=True, timeout=60)
        with tarfile.open(archive) as handle:
            handle.extractall(arm_root, filter="data")
        sources[arm] = arm_root
    acquisitions = {}
    maw = plan["maws"][batch]
    repos = [maw["repo"], *[p["prey"] for p in plan["pairs"] if p["batch"] == batch]]
    for index, repo in enumerate(repos):
        slug = repo.replace("/", "__")
        record = out/"acquisition"/(slug+".json")
        log = out/"acquisition"/(slug+".log")
        log.parent.mkdir(exist_ok=True)
        try:
            status = run_child(Path(__file__), ["--mode", "acquire", "--repo", repo,
                          "--arm", str(sources["v0.6.9.dev0"]), "--volume", str(volume),
                          "--cache", str(cache), "--result", str(record), "--expected",
                          maw["expected_sha"] if repo == maw["repo"] else next(p["expected_sha"] for p in plan["pairs"] if p["prey"] == repo)], log, timeout=900)
            if status["exit_code"] == 0 and record.exists():
                data = read_marker(record)
                if data["catch"]["slug"].casefold() != repo.casefold():
                    raise ValueError("acquired identity mismatch")
                api_path = Path(data["catch"]["repo_dir"]).parent/"api"/"repo.json"
                api = read_marker(api_path)
                if api.get("private") is not False:
                    raise ValueError("public visibility not independently confirmed by provider metadata")
                acquisitions[repo] = {"state": "PASS", **status, **data}
            else:
                acquisitions[repo] = {"state": "FAIL", **status, "log": str(log)}
        except Exception as exc:
            acquisitions[repo] = {"state": "FAIL", "error": str(exc)[:1200], "log": str(log)}
        print("ACQUIRE_PROGRESS "+json.dumps({"batch": batch, "index": index, "repo": repo,
                                              "state": acquisitions[repo]["state"]}), flush=True)
        dump(out/"acquisitions.json", acquisitions)
    snapshot = {"schema": SCHEMA, "as_of": utc(), "batch": batch, "acquisitions": acquisitions,
                "coverage": plan["coverage"], "setup_is_separate_from_arm_cost": True}
    dump(out/"snapshot.json", snapshot)
    results = []
    if acquisitions[maw["repo"]]["state"] != "PASS":
        dump(out/"result.json", {"schema": SCHEMA, "state": "FAIL", "reason": "maw acquisition failed",
                                "batch": batch, "acquisitions": acquisitions, "results": [],
                                "hypotheses": 30, "hypotheses_reviewed": 0})
        raise SystemExit(2)
    maw_root = Path(acquisitions[maw["repo"]]["catch"]["repo_dir"])
    observed_maw_sha = git(maw_root, "rev-parse", "HEAD")
    if observed_maw_sha != maw["expected_sha"]:
        dump(out/"result.json", {"schema": SCHEMA, "state": "FAIL", "batch": batch,
                                "reason": "receiving default head drifted from frozen expected SHA",
                                "expected": maw["expected_sha"], "observed": observed_maw_sha,
                                "acquisitions": acquisitions, "results": []})
        return
    source_packet(maw_root, maw["repo"], maw["source_hints"], out/"maw-source.json", max_files=50)
    shutil.copytree(maw_root, out/"receiving-checkout", symlinks=True)
    dump(out/"receiving-checkout-identity.json", {"sha": observed_maw_sha, "repo": maw["repo"], "status": "inert source data; never execute receiving programs"})
    for pair in [p for p in plan["pairs"] if p["batch"] == batch]:
        row = {"id": pair["id"], "prey": pair["prey"], "batch": batch, "hypotheses": pair["hypotheses"],
               "runs": [], "state": "NOT_RUN"}
        pair_out = out/pair["id"]
        pair_out.mkdir()
        acquired = acquisitions[pair["prey"]]
        if acquired["state"] != "PASS":
            row["reason"] = "donor acquisition failed"
            results.append(row)
            continue
        prey_root = Path(acquired["catch"]["repo_dir"])
        row["prey_sha"] = git(prey_root, "rev-parse", "HEAD")
        if row["prey_sha"] != pair["expected_sha"]:
            raise ValueError("donor head drifted from the previously frozen SHA: "+pair["id"])
        source_packet(prey_root, pair["prey"], pair["source_hints"], pair_out/"donor-source.json")
        repeats = {}
        for arm in plan["arm_order"]:
            repeat = repeats.get(arm, 0)+1
            repeats[arm] = repeat
            run_id = arm+"-r"+str(repeat)
            raw = pair_out/run_id
            log = pair_out/(run_id+".log")
            try:
                status = run_child(Path(__file__), ["--mode", "compare", "--repo", pair["prey"],
                             "--arm", str(sources[arm]), "--cache", str(cache), "--maw", str(maw_root),
                             "--snapshot", str(out/"snapshot.json"), "--result", str(raw)], log, timeout=1500)
                receipt = read_marker(raw/"run.json") if status["exit_code"] == 0 and (raw/"run.json").exists() else None
                row["runs"].append({"id": run_id, "arm": arm, "repeat": repeat, **status, "record": receipt})
                if receipt is None:
                    row["state"] = "FAIL"
                    row["reason"] = "arm failed; pair judgment remains blocked"
                    break
            except Exception as exc:
                row["runs"].append({"id": run_id, "arm": arm, "repeat": repeat, "error": str(exc)[:1200]})
                row["state"] = "FAIL"
                row["reason"] = "arm interrupted; pair judgment remains blocked"
                break
            print("PAIR_PROGRESS "+json.dumps({"batch": batch, "pair": pair["id"], "run": run_id,
                                               "counts": receipt["counts"], "wall_seconds": receipt["measurement"]["wall_seconds"]}), flush=True)
        if len(row["runs"]) == 4 and all(r.get("record") for r in row["runs"]):
            current = [r["record"] for r in row["runs"] if r["arm"] == "v0.6.9.dev0"]
            previous = [r["record"] for r in row["runs"] if r["arm"] == "v0.6.7"]
            inputs = [digest(r["record"]["digest_inputs"]) for r in row["runs"]]
            policies = [digest(r["record"]["analysis_inputs"]) for r in row["runs"]]
            row["paired_inputs_equal"] = len(set(inputs)) == 1 and len(set(policies)) == 1
            row["within_arm_repeat_equal"] = all(a[0]["semantic_sha256"] == a[1]["semantic_sha256"] for a in (current, previous))
            row["between_arm_semantic_equal"] = current[0]["semantic_sha256"] == previous[0]["semantic_sha256"]
            row["state"] = "PASS" if row["paired_inputs_equal"] and row["within_arm_repeat_equal"] else "PAIRING_FAIL"
        results.append(row)
        dump(pair_out/"pair-result.json", row)
        dump(out/"result.json", {"schema": SCHEMA, "batch": batch, "snapshot": snapshot, "results": results,
                                "hypotheses": 30, "hypotheses_reviewed": 0,
                                "review_note": "Deterministic seeds are not analyst-verified useful findings."})
        print("PAIR_RESULT "+json.dumps({k:v for k,v in row.items() if k not in {"runs","hypotheses"}}), flush=True)
    dump(out/"result.json", {"schema": SCHEMA, "batch": batch, "snapshot": snapshot, "results": results,
                            "hypotheses": 30, "hypotheses_reviewed": 0,
                            "review_note": "Deterministic seeds are not analyst-verified useful findings."})
    print("COHORT_SUMMARY "+json.dumps({"batch": batch, "pairs": len(results),
          "passed": sum(r["state"]=="PASS" for r in results),
          "pairing_failures": sum(r["state"]=="PAIRING_FAIL" for r in results),
          "equal": sum(r.get("between_arm_semantic_equal") is True for r in results)}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["orchestrate","acquire","compare"], default="orchestrate")
    for flag in ("plan","batch","output","volume","arm","repo","cache","result","maw","snapshot","expected"):
        parser.add_argument("--"+flag)
    args = parser.parse_args()
    if args.mode == "acquire":
        child_acquire(args)
    elif args.mode == "compare":
        child_compare(args)
    else:
        orchestrate(args)


if __name__ == "__main__":
    main()
