"""Check the actual running probe interpreter against trusted receiving metadata."""

from __future__ import annotations

import operator
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from .artifact_read import read_bytes
from .errors import CrabError


def runtime_preflight(maw: Path) -> dict[str, Any]:
    """Never install or execute a path supplied by donor content.

    The invoking host chooses an already trusted interpreter before calling the
    CLI. Unsupported constraints stay unavailable rather than silently falling
    back to the interpreter running Crab.
    """
    path = maw / "pyproject.toml"
    requirement = None
    if path.exists():
        try:
            document = tomllib.loads(read_bytes(path, 262144).decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise CrabError("receiving runtime preflight: invalid project metadata") from exc
        project = document.get("project", {})
        if isinstance(project, dict):
            requirement = project.get("requires-python")
    version = sys.version_info[:3]
    if requirement is not None:
        if not isinstance(requirement, str) or not 0 < len(requirement) <= 200:
            raise CrabError("receiving runtime preflight: unsupported Python requirement")
        for raw in requirement.split(","):
            match = re.fullmatch(
                r"\s*(>=|<=|==|!=|>|<|~=)\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?(\.\*)?\s*", raw
            )
            if not match:
                raise CrabError("receiving runtime unavailable: unsupported Python constraint")
            op, major, minor, micro, wildcard = match.groups()
            wanted = (int(major), int(minor or 0), int(micro or 0))
            if wildcard:
                if op not in {"==", "!="}:
                    raise CrabError(
                        "receiving runtime unavailable: unsupported wildcard constraint"
                    )
                length = 3 if micro else 2 if minor else 1
                accepted = (version[:length] == wanted[:length]) == (op == "==")
            elif op == "~=":
                upper = (wanted[0], wanted[1] + 1, 0) if micro else (wanted[0] + 1, 0, 0)
                accepted = wanted <= version < upper
            else:
                compare = {
                    ">=": operator.ge,
                    "<=": operator.le,
                    "==": operator.eq,
                    "!=": operator.ne,
                    ">": operator.gt,
                    "<": operator.lt,
                }[op]
                accepted = compare(version, wanted)
            if not accepted:
                raise CrabError(
                    "receiving runtime unavailable: active interpreter violates requires-python"
                )
    return {
        "interpreter": str(Path(sys.executable).resolve()),
        "version": list(version),
        "requires_python": requirement,
        "status": "compatible" if requirement else "not-declared",
        "implicit_installation": False,
        "donor_runtime_used": False,
    }
