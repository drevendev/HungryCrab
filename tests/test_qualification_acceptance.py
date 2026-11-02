"""Undefined observations stay unknown; maximal precision has a bounded proof."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from qualification_acceptance import no_precision_regression


def test_empty_baseline_does_not_force_an_inferior_or_empty_candidate_green() -> None:
    assert no_precision_regression(2, 2, 0, 0)
    assert not no_precision_regression(4, 5, 0, 0)
    assert not no_precision_regression(0, 0, 0, 0)
    assert not no_precision_regression(0, 0, 1, 2)
    assert no_precision_regression(4, 5, 8, 10)
    assert not no_precision_regression(4, 5, 9, 10)
    assert no_precision_regression(3, 3, 9, 10)
    for counts in ((True, 2, 0, 0), (3, 2, 0, 0), (1, 1, -1, 0)):
        with pytest.raises(ValueError):
            no_precision_regression(*counts)
