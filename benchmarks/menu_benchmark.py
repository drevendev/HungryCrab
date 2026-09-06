"""B1 - the menu benchmark: does the deterministic layer still find what a human accepted?

    uv run python benchmarks/menu_benchmark.py [--json] [--top 30]

Everything here is frozen. The prey and maw digests under ``menu/`` are the ones that were on
disk when the maintainer judged these three repositories, and ``menu/golden.yml`` is that
judgement: ``must`` was served, ``must_not`` was rejected, with the reason kept. So the benchmark
asks one question and needs no model to ask it: with today's rules and weights, would the same
menu still put the accepted nutrients in the top 30, and would it still show the rejected ones?

Two numbers come out:

* ``recall_must@30`` - the share of accepted nutrients that reach the top 30. A floor. Dropping
  below it means a rule change lost something a human wanted.
* ``noise@30`` - the share of rejected nutrients that still reach the top 30. A ceiling. It is
  not zero and is not supposed to be: several of those cards are reasonable proposals that this
  particular maw did not want. It may only go down.

The maw's own ``.crab.yml`` is deliberately *not* applied. Hunger, the ledger and existing issues
all suppress candidates for reasons that have nothing to do with rule quality, and a benchmark
that mixes them measures the configuration instead of the code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from hungry_crab.compare import CompareOptions, compare_digests

ROOT = Path(__file__).parent / "menu"
TOP = 30
# The comparison stamps a timestamp into every card's trace. Fixing it keeps runs identical.
FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class PairResult:
    prey: str
    sha: str
    candidates: int
    verdict: str
    must: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    noisy: list[str] = field(default_factory=list)
    must_not_total: int = 0

    @property
    def recall(self) -> float:
        return 1.0 if not self.must else (len(self.must) - len(self.missed)) / len(self.must)

    @property
    def noise(self) -> float:
        return 0.0 if not self.must_not_total else len(self.noisy) / self.must_not_total


def load_spec(root: Path = ROOT) -> dict[str, Any]:
    spec = yaml.safe_load((root / "golden.yml").read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise SystemExit(f"{root / 'golden.yml'} is not a mapping")
    return spec


def run(root: Path = ROOT, *, top: int = TOP) -> tuple[list[PairResult], dict[str, Any]]:
    spec = load_spec(root)
    maw = spec["maw"]
    results: list[PairResult] = []
    for pair in spec["pairs"]:
        comparison = compare_digests(
            root / pair["digest"],
            root / maw["digest"],
            options=CompareOptions(maw_license=maw["license"], top=top, now=FIXED_NOW),
        )
        shown = [card.key for card in comparison.candidates][:top]
        must = [str(key) for key in pair.get("must") or []]
        must_not = [str(item["key"]) for item in pair.get("must_not") or []]
        results.append(
            PairResult(
                prey=str(pair["prey"]),
                sha=str(pair.get("sha", ""))[:12],
                candidates=len(comparison.candidates),
                verdict=str(comparison.verdict["mode"]),
                must=must,
                missed=[key for key in must if key not in shown],
                noisy=[key for key in must_not if key in shown],
                must_not_total=len(must_not),
            )
        )
    return results, spec


def totals(results: list[PairResult]) -> dict[str, float]:
    must = sum(len(r.must) for r in results)
    missed = sum(len(r.missed) for r in results)
    must_not = sum(r.must_not_total for r in results)
    noisy = sum(len(r.noisy) for r in results)
    return {
        "recall_must_at_30": round((must - missed) / must, 4) if must else 1.0,
        "noise_at_30": round(noisy / must_not, 4) if must_not else 0.0,
        "must": must,
        "found": must - missed,
        "must_not": must_not,
        "noisy": noisy,
    }


def check(summary: dict[str, float], thresholds: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    floor = float(thresholds.get("recall_must_at_30", 1.0))
    ceiling = float(thresholds.get("noise_at_30", 1.0))
    if summary["recall_must_at_30"] < floor:
        failures.append(
            f"recall_must@30 fell to {summary['recall_must_at_30']:.2f}, floor is {floor:.2f}: "
            "a rule change lost a nutrient a human had accepted"
        )
    if summary["noise_at_30"] > ceiling:
        failures.append(
            f"noise@30 rose to {summary['noise_at_30']:.2f}, ceiling is {ceiling:.2f}: "
            "a rule change brought back a card this maw had rejected"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--top", type=int, default=TOP, help="menu depth to measure (default: 30)")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    results, spec = run(args.root, top=args.top)
    summary = totals(results)
    failures = check(summary, spec.get("thresholds", {}))

    if args.json:
        print(
            json.dumps(
                {
                    "schema": "hungry-crab.menu-benchmark-result/1",
                    "top": args.top,
                    "summary": summary,
                    "pairs": [asdict(r) for r in results],
                    "failures": failures,
                },
                indent=2,
            )
        )
        return 1 if failures else 0

    print(f"{'prey':<28}{'cards':>6}{'recall':>8}{'noise':>7}  verdict")
    for result in results:
        print(
            f"{result.prey:<28}{result.candidates:>6}"
            f"{result.recall:>8.2f}{result.noise:>7.2f}  {result.verdict}"
        )
    print(
        f"\nrecall_must@{args.top} = {summary['recall_must_at_30']:.2f} "
        f"({summary['found']}/{summary['must']})   "
        f"noise@{args.top} = {summary['noise_at_30']:.2f} "
        f"({summary['noisy']}/{summary['must_not']})"
    )
    for result in results:
        for key in result.missed:
            print(f"  MISSED  {result.prey}: {key}")
    for failure in failures:
        print(f"\nFAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
