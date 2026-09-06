"""The license engine: deterministic detection and a maw x prey verdict matrix.

The engine is conservative on purpose and never guesses in favour of copying. A license it read
and could not classify is the ``HUMAN`` mode: nothing is allowed until a person decides. A prey
with no license at all is ``IDEAS_ONLY``, flagged for review, because all rights are reserved by
default. It is a compliance aid, not legal advice.
"""

from __future__ import annotations

from .detect import (
    LicenseFindings,
    detect_from_text,
    detect_in_repo,
    find_spdx_identifier,
    is_license_file_name,
    license_name_from_file,
    licenses_mentioned,
)
from .matrix import (
    LicenseClass,
    MawClass,
    Mode,
    Relationship,
    Verdict,
    classify,
    decide,
    decide_for_class,
    decide_related,
    maw_class,
    modes_by_maw_class,
    normalize,
)

__all__ = [
    "LicenseClass",
    "LicenseFindings",
    "MawClass",
    "Mode",
    "Relationship",
    "Verdict",
    "classify",
    "decide",
    "decide_for_class",
    "decide_related",
    "detect_from_text",
    "detect_in_repo",
    "find_spdx_identifier",
    "is_license_file_name",
    "license_name_from_file",
    "licenses_mentioned",
    "maw_class",
    "modes_by_maw_class",
    "normalize",
]
