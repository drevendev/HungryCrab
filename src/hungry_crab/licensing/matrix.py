"""License classification and the maw x prey verdict matrix.

Modes (what a nutrient under the prey's license may become in the maw):

- ``COPY``         copy code/configs, keep the notice, record it in THIRD_PARTY_NOTICES.md
- ``COPY_FILE``    copy whole files; the file keeps its own license (MPL-2.0 style)
- ``REIMPLEMENT``  clean-room rewrite from a specification, no verbatim code
- ``IDEAS_ONLY``   ideas, architecture, approaches, facts only
- ``HUMAN``        the engine is unsure; a human decides

The matrix follows docs/design/01-concept-and-skill.md section 10. When in doubt it picks the
more restrictive mode.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class LicenseClass(StrEnum):
    PERMISSIVE = "permissive"
    PERMISSIVE_NOTICE = "permissive-notice"  # Apache-2.0: NOTICE file obligations
    FILE_COPYLEFT = "file-copyleft"  # MPL / EPL / CDDL
    LGPL = "lgpl"
    GPL = "gpl"
    AGPL = "agpl"
    SOURCE_AVAILABLE = "source-available"  # BUSL, SSPL, Elastic, Commons Clause, proprietary
    DOCS_ATTRIBUTION = "docs-attribution"  # CC-BY
    DOCS_SHARE_ALIKE = "docs-share-alike"  # CC-BY-SA, GFDL
    DOCS_RESTRICTED = "docs-restricted"  # CC-BY-NC, CC-BY-ND
    NONE = "none"
    UNKNOWN = "unknown"


class MawClass(StrEnum):
    PERMISSIVE = "permissive"
    GPL = "gpl"
    PROPRIETARY = "proprietary"


class Mode(StrEnum):
    COPY = "COPY"
    COPY_FILE = "COPY_FILE"
    REIMPLEMENT = "REIMPLEMENT"
    IDEAS_ONLY = "IDEAS_ONLY"
    HUMAN = "HUMAN"


_CANONICAL: dict[str, str] = {
    "mit": "MIT",
    "mit license": "MIT",
    "mit-0": "MIT-0",
    "x11": "X11",
    "isc": "ISC",
    "0bsd": "0BSD",
    "bsd": "BSD-3-Clause",
    "bsd-2": "BSD-2-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-3-clause-clear": "BSD-3-Clause-Clear",
    "bsd-4-clause": "BSD-4-Clause",
    "apache": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "mpl": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "epl-2.0": "EPL-2.0",
    "epl-1.0": "EPL-1.0",
    "cddl-1.0": "CDDL-1.0",
    "gpl": "GPL-3.0-only",
    "gplv2": "GPL-2.0-only",
    "gplv2+": "GPL-2.0-or-later",
    "gpl-2.0": "GPL-2.0-only",
    "gpl-2.0+": "GPL-2.0-or-later",
    "gpl-2.0-only": "GPL-2.0-only",
    "gpl-2.0-or-later": "GPL-2.0-or-later",
    "gplv3": "GPL-3.0-only",
    "gplv3+": "GPL-3.0-or-later",
    "gpl-3.0": "GPL-3.0-only",
    "gpl-3.0+": "GPL-3.0-or-later",
    "gpl-3.0-only": "GPL-3.0-only",
    "gpl-3.0-or-later": "GPL-3.0-or-later",
    "lgpl": "LGPL-3.0-only",
    "lgplv2.1": "LGPL-2.1-only",
    "lgpl-2.1": "LGPL-2.1-only",
    "lgpl-2.1+": "LGPL-2.1-or-later",
    "lgpl-2.1-only": "LGPL-2.1-only",
    "lgpl-2.1-or-later": "LGPL-2.1-or-later",
    "lgplv3": "LGPL-3.0-only",
    "lgpl-3.0": "LGPL-3.0-only",
    "lgpl-3.0+": "LGPL-3.0-or-later",
    "lgpl-3.0-only": "LGPL-3.0-only",
    "lgpl-3.0-or-later": "LGPL-3.0-or-later",
    "agpl": "AGPL-3.0-only",
    "agplv3": "AGPL-3.0-only",
    "agpl-3.0": "AGPL-3.0-only",
    "agpl-3.0+": "AGPL-3.0-or-later",
    "agpl-3.0-only": "AGPL-3.0-only",
    "agpl-3.0-or-later": "AGPL-3.0-or-later",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc-by-3.0": "CC-BY-3.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "cc-by-sa-3.0": "CC-BY-SA-3.0",
    "cc-by-nc-4.0": "CC-BY-NC-4.0",
    "cc-by-nc-sa-4.0": "CC-BY-NC-SA-4.0",
    "cc-by-nd-4.0": "CC-BY-ND-4.0",
    "gfdl-1.3": "GFDL-1.3-only",
    "zlib": "Zlib",
    "bsl-1.0": "BSL-1.0",
    "boost": "BSL-1.0",
    "busl-1.1": "BUSL-1.1",
    "bsl-1.1": "BUSL-1.1",
    "sspl-1.0": "SSPL-1.0",
    "elastic-2.0": "Elastic-2.0",
    "commons-clause": "Commons-Clause",
    "postgresql": "PostgreSQL",
    "python-2.0": "Python-2.0",
    "psf-2.0": "PSF-2.0",
    "wtfpl": "WTFPL",
    "artistic-2.0": "Artistic-2.0",
    "blueoak-1.0.0": "BlueOak-1.0.0",
    "ncsa": "NCSA",
    "upl-1.0": "UPL-1.0",
    "mulanpsl-2.0": "MulanPSL-2.0",
    "unlicensed": "LicenseRef-Proprietary",
    "proprietary": "LicenseRef-Proprietary",
    "licenseref-proprietary": "LicenseRef-Proprietary",
    "noassertion": "NOASSERTION",
    "other": "NOASSERTION",
}

PERMISSIVE_IDS = frozenset(
    {
        "MIT", "MIT-0", "X11", "ISC", "0BSD", "BSD-2-Clause", "BSD-3-Clause",
        "BSD-3-Clause-Clear", "BSD-4-Clause", "Zlib", "Unlicense", "CC0-1.0", "BSL-1.0",
        "PostgreSQL", "Python-2.0", "PSF-2.0", "WTFPL", "Artistic-2.0", "BlueOak-1.0.0",
        "NCSA", "UPL-1.0", "MulanPSL-2.0",
    }
)  # fmt: skip

SOURCE_AVAILABLE_IDS = frozenset(
    {"BUSL-1.1", "SSPL-1.0", "Elastic-2.0", "Commons-Clause", "LicenseRef-Proprietary"}
)

_RANK: tuple[LicenseClass, ...] = (
    LicenseClass.PERMISSIVE,
    LicenseClass.PERMISSIVE_NOTICE,
    LicenseClass.DOCS_ATTRIBUTION,
    LicenseClass.FILE_COPYLEFT,
    LicenseClass.DOCS_SHARE_ALIKE,
    LicenseClass.LGPL,
    LicenseClass.GPL,
    LicenseClass.AGPL,
    LicenseClass.DOCS_RESTRICTED,
    LicenseClass.SOURCE_AVAILABLE,
    LicenseClass.UNKNOWN,
    LicenseClass.NONE,
)


def normalize(spdx: str | None) -> str | None:
    """Map aliases and deprecated identifiers to canonical SPDX ids; keep unknown ids as-is."""
    if spdx is None:
        return None
    text = spdx.strip().strip("\"'")
    if not text:
        return None
    upper = text.upper()
    if " OR " in upper or " AND " in upper:
        joiner = " OR " if " OR " in upper else " AND "
        parts = [normalize(part.strip("() ")) or "" for part in _split(text, joiner)]
        return joiner.join(part for part in parts if part)
    lowered = text.lower()
    if lowered in _CANONICAL:
        return _CANONICAL[lowered]
    if lowered.startswith("see license"):
        return "NOASSERTION"
    return text


def _split(text: str, joiner: str) -> list[str]:
    needle = joiner.strip().lower()
    parts: list[str] = []
    current: list[str] = []
    for token in text.split():
        if token.lower() == needle:
            parts.append(" ".join(current))
            current = []
        else:
            current.append(token)
    parts.append(" ".join(current))
    return parts


def classify(spdx: str | None) -> LicenseClass:
    ident = normalize(spdx)
    if ident is None:
        return LicenseClass.NONE
    if " OR " in ident:
        options = [classify(part) for part in ident.split(" OR ")]
        return min(options, key=_RANK.index)
    if " AND " in ident:
        options = [classify(part) for part in ident.split(" AND ")]
        return max(options, key=_RANK.index)
    if ident == "Apache-2.0":
        return LicenseClass.PERMISSIVE_NOTICE
    if ident in PERMISSIVE_IDS:
        return LicenseClass.PERMISSIVE
    if ident.startswith(("MPL-", "EPL-", "CDDL-")):
        return LicenseClass.FILE_COPYLEFT
    if ident.startswith("LGPL-"):
        return LicenseClass.LGPL
    if ident.startswith("AGPL-"):
        return LicenseClass.AGPL
    if ident.startswith("GPL-"):
        return LicenseClass.GPL
    if ident in SOURCE_AVAILABLE_IDS or ident.startswith("LicenseRef-"):
        return LicenseClass.SOURCE_AVAILABLE
    if ident.startswith("CC-BY"):
        if "-NC" in ident or "-ND" in ident:
            return LicenseClass.DOCS_RESTRICTED
        if "-SA" in ident:
            return LicenseClass.DOCS_SHARE_ALIKE
        return LicenseClass.DOCS_ATTRIBUTION
    if ident.startswith("GFDL-"):
        return LicenseClass.DOCS_SHARE_ALIKE
    return LicenseClass.UNKNOWN


def maw_class(spdx: str | None) -> MawClass:
    """Which column of the matrix a maw with this license belongs to."""
    cls = classify(spdx)
    if cls in (LicenseClass.LGPL, LicenseClass.GPL, LicenseClass.AGPL):
        return MawClass.GPL
    if cls in (
        LicenseClass.PERMISSIVE,
        LicenseClass.PERMISSIVE_NOTICE,
        LicenseClass.FILE_COPYLEFT,
        LicenseClass.DOCS_ATTRIBUTION,
        LicenseClass.DOCS_SHARE_ALIKE,
    ):
        return MawClass.PERMISSIVE
    return MawClass.PROPRIETARY


@dataclass(frozen=True)
class Verdict:
    mode: Mode
    notice_required: bool = False
    share_alike: bool = False
    human_review: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["mode"] = self.mode.value
        return data


def _gpl_version(ident: str) -> tuple[str, bool]:
    """('2.0' | '3.0', or_later) for a GPL/AGPL/LGPL identifier."""
    body = ident.split("-", 1)[1] if "-" in ident else ""
    version = "3.0" if body.startswith("3") else "2.0" if body.startswith("2") else "3.0"
    return version, body.endswith(("or-later", "+"))


def _gpl_prey_fits_gpl_maw(prey: str, maw: str | None) -> bool:
    """Can GPL code (``prey``) be copied into a maw under ``maw`` without changing it?"""
    maw_id = normalize(maw) or ""
    prey_version, prey_later = _gpl_version(prey)
    if prey.startswith("AGPL-"):
        return maw_id.startswith("AGPL-3.0")
    if maw_id.startswith(("AGPL-3.0", "GPL-3.0")):
        return prey_version == "3.0" or prey_later
    if maw_id == "GPL-2.0-only":
        return prey_version == "2.0"
    return maw_id == "GPL-2.0-or-later"


def decide_for_class(prey_spdx: str | None, maw: MawClass, maw_spdx: str | None = None) -> Verdict:
    prey = normalize(prey_spdx)
    cls = classify(prey)
    if cls is LicenseClass.PERMISSIVE:
        return Verdict(Mode.COPY, reason="permissive license: keep the copyright notice")
    if cls is LicenseClass.PERMISSIVE_NOTICE:
        if maw is MawClass.GPL and (normalize(maw_spdx) or "") == "GPL-2.0-only":
            return Verdict(Mode.IDEAS_ONLY, reason="Apache-2.0 is incompatible with GPL-2.0-only")
        return Verdict(
            Mode.COPY, notice_required=True, reason="Apache-2.0: carry the NOTICE file over"
        )
    if cls is LicenseClass.DOCS_ATTRIBUTION:
        return Verdict(Mode.COPY, notice_required=True, reason="CC-BY: attribution required")
    if cls is LicenseClass.DOCS_SHARE_ALIKE:
        return Verdict(
            Mode.COPY_FILE,
            share_alike=True,
            reason="share-alike: copied documents keep their license",
        )
    if cls is LicenseClass.DOCS_RESTRICTED:
        return Verdict(Mode.IDEAS_ONLY, reason="non-commercial or no-derivatives clause")
    if cls is LicenseClass.FILE_COPYLEFT:
        return Verdict(
            Mode.COPY_FILE, reason="file-level copyleft: whole files only, each keeps its license"
        )
    if cls is LicenseClass.LGPL:
        if maw is MawClass.GPL:
            return Verdict(Mode.COPY, reason="LGPL code may be relicensed under the maw's GPL")
        return Verdict(Mode.REIMPLEMENT, reason="LGPL: clean-room rewrite; linking is separate")
    if cls in (LicenseClass.GPL, LicenseClass.AGPL):
        assert prey is not None
        if maw is MawClass.GPL:
            if _gpl_prey_fits_gpl_maw(prey, maw_spdx):
                return Verdict(Mode.COPY, reason="compatible copyleft versions")
            return Verdict(Mode.IDEAS_ONLY, reason="incompatible copyleft versions")
        if maw is MawClass.PERMISSIVE:
            return Verdict(
                Mode.REIMPLEMENT,
                reason="strong copyleft: only a clean-room reimplementation from a spec",
            )
        return Verdict(Mode.IDEAS_ONLY, reason="strong copyleft into a closed maw: ideas only")
    if cls is LicenseClass.SOURCE_AVAILABLE:
        return Verdict(Mode.IDEAS_ONLY, reason="source-available, not open source")
    if cls is LicenseClass.NONE:
        return Verdict(
            Mode.IDEAS_ONLY,
            human_review=True,
            reason="no license found: all rights reserved by default",
        )
    return Verdict(Mode.IDEAS_ONLY, human_review=True, reason=f"unrecognised license {prey or '?'}")


def decide(prey_spdx: str | None, maw_spdx: str | None) -> Verdict:
    return decide_for_class(prey_spdx, maw_class(maw_spdx), maw_spdx)


_SAMPLE_MAWS: dict[MawClass, str | None] = {
    MawClass.PERMISSIVE: "MIT",
    MawClass.GPL: "GPL-3.0-only",
    MawClass.PROPRIETARY: None,
}


def modes_by_maw_class(prey_spdx: str | None) -> dict[str, str]:
    """The mode a nutrient would get in a permissive, a GPL and a proprietary maw."""
    return {
        maw.value: decide_for_class(prey_spdx, maw, sample).mode.value
        for maw, sample in _SAMPLE_MAWS.items()
    }
