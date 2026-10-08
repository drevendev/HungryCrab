"""Bounded discovery policy, owned exclusively by the maw."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import UsageError
from .loop_config import mapping, positive, strings


@dataclass
class HuntSettings:
    queries: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    licenses: list[str] = field(default_factory=list)
    min_stars: int = 20
    max_repo_kb: int = 300 * 1024
    max_candidates: int = 50
    limit: int = 10
    include_seen: bool = False
    allow_unknown_size: bool = False

    @classmethod
    def load(cls, value: object) -> HuntSettings:
        keys = set(cls.__dataclass_fields__)
        data = mapping(value, "hunt", keys)
        result = cls()
        for name in ("queries", "exclude", "licenses"):
            setattr(result, name, strings(data.get(name, []), "hunt." + name))
        if len(result.queries) > 4 or any(len(query) > 256 for query in result.queries):
            raise UsageError("hunt accepts at most four queries of at most 256 characters")
        for name in ("max_repo_kb", "max_candidates", "limit"):
            setattr(result, name, positive(data.get(name, getattr(result, name)), "hunt." + name))
        stars = data.get("min_stars", result.min_stars)
        if isinstance(stars, bool) or not isinstance(stars, int) or stars < 0:
            raise UsageError("hunt.min_stars must be a non-negative integer")
        result.min_stars = stars
        if result.max_candidates > 100 or result.limit > result.max_candidates:
            raise UsageError("hunt.limit <= hunt.max_candidates <= 100 is required")
        for name in ("include_seen", "allow_unknown_size"):
            setting = data.get(name, getattr(result, name))
            if not isinstance(setting, bool):
                raise UsageError(f"hunt.{name} must be a boolean")
            setattr(result, name, setting)
        return result
