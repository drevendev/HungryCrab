"""Trusted, in-process host boundary; plans cannot install or qualify a meter.

Adapters are reviewed host code, never imported from a plan, prey or artifact.
Their external/model ports must call execute_operation, including nested calls.
This is an integration contract, not a sandbox for arbitrary Python callbacks.
No production native adapter is shipped. Owned meters only prove mechanics.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from .errors import CrabError


@dataclass(frozen=True)
class MeterIdentity:
    source: str
    evidence_sha256: str
    scope: str

    def to_dict(self) -> dict[str, str]:
        if (
            not self.source.strip()
            or len(self.source) > 200
            or re.fullmatch(r"[a-f0-9]{64}", self.evidence_sha256) is None
            or self.scope not in {"owned-fixture", "reviewed-native"}
        ):
            raise CrabError("invalid trusted host metering identity")
        return {
            "source": self.source,
            "evidence_sha256": self.evidence_sha256,
            "scope": self.scope,
        }


class UsageMeter(Protocol):
    """Host-owned usage source, separate from semantic output and plan JSON.

    begin must apply the supplied token cap to the actual model invocation.
    finish reads native actual usage even when that invocation raised. It must
    exclude nested scopes, never estimate or read tokens from the model output.
    preflight performs no model/provider work. Missing usage returns None.
    """

    def preflight(self) -> MeterIdentity: ...

    def begin(self, operation: str, token_limit: int) -> object: ...

    def finish(self, ticket: object) -> int | None: ...


_METER: ContextVar[UsageMeter | None] = ContextVar("crab_discovery_meter", default=None)


@contextmanager
def host_meter(meter: UsageMeter | None) -> Iterator[None]:
    """Bind trusted host code explicitly; there is no CLI/JSON adapter loader."""
    token = _METER.set(meter)
    try:
        yield
    finally:
        _METER.reset(token)


def require_meter(expected: object = None) -> tuple[UsageMeter, dict[str, str]]:
    meter = _METER.get()
    if meter is None:
        raise CrabError("strict discovery requires a trusted live host metering adapter")
    try:
        identity = meter.preflight().to_dict()
    except Exception as exc:
        raise CrabError("trusted host metering preflight is unavailable") from exc
    if expected is not None and expected != identity:
        raise CrabError("trusted host metering identity changed; reconcile original session")
    return meter, identity
