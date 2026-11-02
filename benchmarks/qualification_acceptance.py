"""Conservative precision comparison with explicitly undefined empty samples."""

from __future__ import annotations


def no_precision_regression(
    candidate_confirmed: int,
    candidate_positive: int,
    baseline_confirmed: int,
    baseline_positive: int,
) -> bool:
    """A perfect observed candidate cannot be below any baseline precision.

    An empty baseline has no observed precision. Do not invent one, or impose
    a new requirement that the baseline propose a positive before qualification
    can succeed. Other usefulness, coverage, cost and safety gates still apply.
    """
    for confirmed, positive in (
        (candidate_confirmed, candidate_positive),
        (baseline_confirmed, baseline_positive),
    ):
        if (
            type(confirmed) is not int
            or type(positive) is not int
            or not 0 <= confirmed <= positive
        ):
            raise ValueError("precision counts require 0 <= confirmed <= positive integers")
    if candidate_positive == 0:
        return False
    if candidate_confirmed == candidate_positive:
        return True
    return baseline_positive > 0 and (
        candidate_confirmed * baseline_positive >= baseline_confirmed * candidate_positive
    )
