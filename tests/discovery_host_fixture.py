"""Owned scripted usage source. This cannot qualify a native production meter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from hungry_crab.discovery_host import MeterIdentity
from hungry_crab.discovery_session import execute_operation

SOURCE = "synthetic native pytest usage fixture"


class OwnedMeter:
    available = True
    tokens: int | None = 0

    def preflight(self) -> MeterIdentity:
        if not self.available:
            raise RuntimeError("owned usage source unavailable")
        return MeterIdentity(SOURCE, "a" * 64, "owned-fixture")

    def begin(self, operation: str, token_limit: int) -> object:
        return operation

    def finish(self, ticket: object) -> int | None:
        return self.tokens


def provider_call(
    directory: Path, attempt: str, operation: str, *, now: datetime | None = None
) -> None:
    execute_operation(directory, attempt, operation, lambda _: None, now=now)
