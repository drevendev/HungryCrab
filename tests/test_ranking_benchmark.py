from __future__ import annotations

import ranking_benchmark


def test_known_priorities_survive_real_ranking_pressure() -> None:
    result = ranking_benchmark.run()
    assert result["candidates"] >= 50 > result["top"]
    assert result["passed"], result


def test_rank_gate_detects_scoring_regressions() -> None:
    # Losing a candidate and ranking it below the cutoff are different failures. The
    # regression keeps every producer active, but reverses the category priorities.
    result = ranking_benchmark.run(scoring={"categories": {"ci": 0, "hygiene": 0, "tests": 0}})
    assert result["candidates"] >= 50
    assert result["missed"] and not result["passed"]


def test_ranking_is_repeatable() -> None:
    assert ranking_benchmark.run() == ranking_benchmark.run()
