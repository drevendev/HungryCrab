from __future__ import annotations

import math

import pytest
from ranking_metrics import diagnostics, mean_diagnostics


def test_hand_computed_graded_oracle_and_reversed_control() -> None:
    labels = {"a": 3, "b": 2, "c": 0}
    ideal = diagnostics(["a", "b", "c"], labels, k=3)
    reverse = diagnostics(["c", "b", "a"], labels, k=3)
    assert ideal["ndcg_at_k"] == pytest.approx(1.0)
    assert reverse["ndcg_at_k"] == pytest.approx(
        (3 / math.log2(3) + 7 / 2) / (7 + 3 / math.log2(3))
    )
    assert reverse["reciprocal_rank_at_k"] == 0.5
    assert ideal["ndcg_at_k"] - reverse["ndcg_at_k"] > 0.02


def test_empty_unjudged_short_and_tied_order_contract() -> None:
    assert diagnostics([], {}, k=10)["ndcg_at_k"] == 0
    assert diagnostics(["unknown"], {"wanted": 1}, k=10)["judged_at_k"] == 0
    assert diagnostics(["wanted"], {"wanted": 1}, k=10)["judged_at_k"] == 0.1
    assert diagnostics(["b", "a"], {"a": 1, "b": 0}, k=2)["reciprocal_rank_at_k"] == 0.5
    assert mean_diagnostics([])["mean_ndcg_at_k"] == 0
    assert (
        mean_diagnostics([diagnostics(["a"], {"a": 1}), diagnostics([], {})])["mean_ndcg_at_k"]
        == 0.5
    )


@pytest.mark.parametrize("order,labels,k", [(["a", "a"], {}, 2), ([], {}, 0), ([], {"a": True}, 2)])
def test_invalid_ranking_inputs_are_refused(
    order: list[str], labels: dict[str, int], k: int
) -> None:
    with pytest.raises(ValueError):
        diagnostics(order, labels, k=k)
