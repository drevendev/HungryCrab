from hypothesis import given
from hypothesis import strategies as st

from pycli.core import count_pools


def test_count(sample_text: str) -> None:
    assert count_pools(sample_text) == 3
    assert count_pools(sample_text, unique=True) == 2


@given(st.lists(st.text(min_size=1)))
def test_unique_never_exceeds_total(names: list[str]) -> None:
    text = "\n".join(names)
    assert count_pools(text, unique=True) <= count_pools(text)
