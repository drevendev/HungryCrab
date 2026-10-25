"""Recheck supervised operation consent immediately before built-in effects.

This process-local guard is not an agent-host sandbox. Direct tools still require #243.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT: ContextVar[Callable[[], None] | None] = ContextVar("crab_effect_guard", default=None)


@contextmanager
def guarded_effects(check: Callable[[], None]) -> Iterator[None]:
    token = _CURRENT.set(check)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def check_effect() -> None:
    check = _CURRENT.get()
    if check is not None:
        check()
