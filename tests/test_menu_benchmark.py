"""The B1 gate: the menu benchmark runs in the test suite, so every pull request measures it.

The benchmark itself lives in ``benchmarks/menu_benchmark.py`` and is importable here through the
``pythonpath`` setting in ``pyproject.toml``. Keeping the gate in pytest rather than in a separate
CI job means it runs on both platforms and both Python versions of the matrix, and that a
contributor sees the number locally before pushing.
"""

from __future__ import annotations

import menu_benchmark


def test_the_frozen_pairs_are_all_measured() -> None:
    results, spec = menu_benchmark.run()
    assert [r.prey for r in results] == [pair["prey"] for pair in spec["pairs"]]
    assert all(r.candidates > 0 for r in results), "a pair that produces no menu measures nothing"


def test_recall_and_noise_hold_their_thresholds() -> None:
    """The whole point: a rule change may not lose an accepted nutrient or revive a rejected one."""
    results, spec = menu_benchmark.run()
    summary = menu_benchmark.totals(results)
    failures = menu_benchmark.check(summary, spec["thresholds"])
    missed = {r.prey: r.missed for r in results if r.missed}
    assert not failures, f"{'; '.join(failures)}. Missed by prey: {missed}"


def test_the_benchmark_is_deterministic() -> None:
    """Frozen digests plus a fixed clock: two runs have to agree, or the numbers mean nothing."""
    first = menu_benchmark.totals(menu_benchmark.run()[0])
    second = menu_benchmark.totals(menu_benchmark.run()[0])
    assert first == second


def test_the_golden_set_is_the_maintainers_own_verdicts() -> None:
    """Guards the property that makes this benchmark worth anything: nothing here is invented."""
    pairs = menu_benchmark.load_spec()["pairs"]
    assert sum(len(pair.get("must") or []) for pair in pairs) == 10
    assert sum(len(pair.get("must_not") or []) for pair in pairs) == 29
    for pair in pairs:
        for item in pair["must_not"]:
            assert item.get("reason"), f"{item['key']} was rejected without a recorded reason"
