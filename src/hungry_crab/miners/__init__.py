"""Miner registry. Order matters: later miners read the results of earlier ones."""

from __future__ import annotations

from .ai_config import AiConfigMiner
from .base import FileInfo, MineContext, Miner, MinerResult
from .branches import BranchesMiner
from .ci import CiMiner
from .deps import DepsMiner
from .docs import DocsMiner
from .history import HistoryMiner
from .inventory import InventoryMiner
from .license import LicenseMiner
from .testing import TestingMiner
from .traits import TraitsMiner

__all__ = [
    "ALL_MINERS",
    "MINER_NAMES",
    "FileInfo",
    "MineContext",
    "Miner",
    "MinerResult",
    "select_miners",
]

ALL_MINERS: tuple[Miner, ...] = (
    InventoryMiner(),
    LicenseMiner(),
    DepsMiner(),
    CiMiner(),
    TestingMiner(),
    DocsMiner(),
    AiConfigMiner(),
    HistoryMiner(),
    BranchesMiner(),
    TraitsMiner(),
)

MINER_NAMES: tuple[str, ...] = tuple(miner.name for miner in ALL_MINERS)


def select_miners(names: list[str] | None = None) -> list[Miner]:
    """All miners, or the requested ones plus everything they require (in registry order)."""
    if not names:
        return list(ALL_MINERS)
    by_name = {miner.name: miner for miner in ALL_MINERS}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise ValueError(f"unknown miner(s): {', '.join(unknown)}; known: {', '.join(MINER_NAMES)}")
    wanted: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in wanted:
            continue
        wanted.add(name)
        pending.extend(by_name[name].requires)
    return [miner for miner in ALL_MINERS if miner.name in wanted]
