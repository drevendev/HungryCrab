"""The license engine: deterministic detection and a maw x prey verdict matrix.

The engine is conservative on purpose. Anything it cannot classify becomes ``IDEAS_ONLY`` with a
``HUMAN`` flag; it never guesses in favour of copying. It is a compliance aid, not legal advice.
"""

from __future__ import annotations

from .detect import (
    LicenseFindings,
    detect_from_text,
    detect_in_repo,
    find_spdx_identifier,
    is_license_file_name,
)
from .matrix import (
    LicenseClass,
    MawClass,
    Mode,
    Verdict,
    classify,
    decide,
    decide_for_class,
    maw_class,
    modes_by_maw_class,
    normalize,
)

__all__ = [
    "LicenseClass",
    "LicenseFindings",
    "MawClass",
    "Mode",
    "Verdict",
    "classify",
    "decide",
    "decide_for_class",
    "detect_from_text",
    "detect_in_repo",
    "find_spdx_identifier",
    "is_license_file_name",
    "maw_class",
    "modes_by_maw_class",
    "normalize",
]
