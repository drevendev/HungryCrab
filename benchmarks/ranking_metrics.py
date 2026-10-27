"""Original stdlib implementations of published RR/DCG formulas; diagnostic only.

Relevance grades are 0..3. Unjudged items contribute zero gain, but Judged@k makes
that assumption visible. Menu order, including its deterministic tie break, is retained.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def diagnostics(
    order: Sequence[str], relevance: Mapping[str, int], *, k: int = 10
) -> dict[str, float]:
    if type(k) is not int or k < 1:
        raise ValueError("ranking depth must be a positive integer")
    if len(order) != len(set(order)):
        raise ValueError("ranking contains duplicate identities")
    if any(type(grade) is not int or not 0 <= grade <= 3 for grade in relevance.values()):
        raise ValueError("relevance grades must be integers in 0..3")
    selected = order[:k]
    grades = [relevance.get(item, 0) for item in selected]

    def dcg(values: Sequence[int]) -> float:
        return sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(values, 1))

    ideal = dcg(sorted(relevance.values(), reverse=True)[:k])
    reciprocal = next((1 / rank for rank, grade in enumerate(grades, 1) if grade > 0), 0.0)
    return {
        "reciprocal_rank_at_k": reciprocal,
        "ndcg_at_k": dcg(grades) / ideal if ideal else 0.0,
        "judged_at_k": sum(item in relevance for item in selected) / k,
        "returned": len(selected),
        "depth": k,
    }


def mean_diagnostics(queries: Sequence[dict[str, float]]) -> dict[str, float]:
    """Unweighted macro mean; empty/no-relevant queries contribute zero, not fabricated success."""
    return {
        f"mean_{key}": sum(query[key] for query in queries) / len(queries) if queries else 0.0
        for key in ("reciprocal_rank_at_k", "ndcg_at_k", "judged_at_k")
    }
