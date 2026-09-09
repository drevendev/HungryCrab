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

import re
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


class Relationship(StrEnum):
    """How the maw is related to the prey, which a license alone cannot express.

    ``FOREIGN`` is the default and the only one that consults the matrix. ``OWN`` says the maw's
    owner also owns the prey, so the license is a note to third parties and not a limit on the
    owner. ``BYPASS`` is a deliberate override configured by the maw.
    """

    FOREIGN = "foreign"
    OWN = "own"
    BYPASS = "bypass"


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


_OPERATORS = frozenset({"AND", "OR"})
# A capture-less ``re.split`` would drop the brackets, which is the whole point of parsing.
_TOKEN_RE = re.compile(r"[()]|[^\s()]+")


@dataclass(frozen=True)
class _Expr:
    """A parsed SPDX expression: a leaf identifier, or an ``AND``/``OR`` of operands.

    ``WITH`` is deliberately not an operator here. ``GPL-2.0-only WITH Classpath-exception-2.0``
    stays one leaf, classified by its base identifier, which is what an exception can only make
    more permissive and never less.
    """

    op: str = ""
    ident: str = ""
    operands: tuple[_Expr, ...] = ()


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def _parse_primary(tokens: list[str], pos: int) -> tuple[_Expr, int] | None:
    if pos >= len(tokens):
        return None
    if tokens[pos] == "(":
        parsed = _parse_or(tokens, pos + 1)
        if parsed is None:
            return None
        inner, pos = parsed
        if pos >= len(tokens) or tokens[pos] != ")":
            return None
        return inner, pos + 1
    words: list[str] = []
    while pos < len(tokens):
        token = tokens[pos]
        if token in ("(", ")") or token.upper() in _OPERATORS:
            break
        words.append(token)
        pos += 1
    if not words:
        return None
    return _Expr(ident=" ".join(words)), pos


def _parse_and(tokens: list[str], pos: int) -> tuple[_Expr, int] | None:
    parsed = _parse_primary(tokens, pos)
    if parsed is None:
        return None
    first, pos = parsed
    operands = [first]
    while pos < len(tokens) and tokens[pos].upper() == "AND":
        parsed = _parse_primary(tokens, pos + 1)
        if parsed is None:
            return None
        operand, pos = parsed
        operands.append(operand)
    return (operands[0] if len(operands) == 1 else _Expr(op="AND", operands=tuple(operands))), pos


def _parse_or(tokens: list[str], pos: int) -> tuple[_Expr, int] | None:
    parsed = _parse_and(tokens, pos)
    if parsed is None:
        return None
    first, pos = parsed
    operands = [first]
    while pos < len(tokens) and tokens[pos].upper() == "OR":
        parsed = _parse_and(tokens, pos + 1)
        if parsed is None:
            return None
        operand, pos = parsed
        operands.append(operand)
    return (operands[0] if len(operands) == 1 else _Expr(op="OR", operands=tuple(operands))), pos


def parse_expression(text: str) -> _Expr | None:
    """The expression, or ``None`` when it is not one this parser understands."""
    tokens = _tokenize(text)
    parsed = _parse_or(tokens, 0)
    if parsed is None:
        return None
    expr, pos = parsed
    return expr if pos == len(tokens) else None


def _normalize_id(text: str) -> str:
    lowered = text.lower()
    if lowered in _CANONICAL:
        return _CANONICAL[lowered]
    if lowered.startswith("see license"):
        return "NOASSERTION"
    return text


def _render(expr: _Expr, *, parent: str = "") -> str:
    """Canonical text, with the parentheses the meaning needs and no others."""
    if not expr.op:
        return _normalize_id(expr.ident)
    joined = f" {expr.op} ".join(_render(operand, parent=expr.op) for operand in expr.operands)
    # AND binds tighter than OR, so only an OR nested inside an AND has to keep its brackets.
    return f"({joined})" if expr.op == "OR" and parent == "AND" else joined


def normalize(spdx: str | None) -> str | None:
    """Map aliases and deprecated identifiers to canonical SPDX ids; keep unknown ids as-is.

    An expression keeps its structure. Dropping the brackets of ``(MIT OR Apache-2.0) AND
    CC-BY-4.0`` and re-reading the result with SPDX precedence turns it into ``MIT OR
    (Apache-2.0 AND CC-BY-4.0)`` — a different licence, and always a more permissive one.
    """
    if spdx is None:
        return None
    text = spdx.strip().strip("\"'")
    if not text:
        return None
    expr = parse_expression(text)
    if expr is None:
        return _normalize_id(text)
    return _render(expr) or None


def _evaluate(expr: _Expr) -> tuple[LicenseClass, str]:
    """(class, the identifier that decided it)."""
    if not expr.op:
        ident = _normalize_id(expr.ident)
        return _classify_id(ident), ident
    results = [_evaluate(operand) for operand in expr.operands]
    if expr.op == "OR":
        # A choice: the recipient may take the least restrictive branch.
        return min(results, key=lambda item: _RANK.index(item[0]))
    # A conjunction: every term applies, so the most restrictive one governs.
    return max(results, key=lambda item: _RANK.index(item[0]))


def _fits_gpl_maw(expr: _Expr, maw_spdx: str | None) -> bool:
    """Can every branch of ``expr`` the recipient must honour live in this GPL maw?

    Version compatibility is a property of the expression, not of one identifier inside it.
    ``A OR B`` is satisfied by whichever branch fits, because the recipient chooses; ``A AND B``
    needs both, because both apply. Collapsing the expression to a single "governing" term gets
    this wrong whenever two terms share a licence class — ``GPL-2.0-or-later AND GPL-2.0-only``
    would answer with whichever term happened to come first.
    """
    if not expr.op:
        ident = _normalize_id(expr.ident)
        if _classify_id(ident) not in (LicenseClass.GPL, LicenseClass.AGPL):
            # Only a copyleft term constrains which GPL maw may take the code. Anything else in
            # the expression is either satisfiable outright or already handled by its own branch,
            # because it would have decided the class.
            return True
        return _gpl_prey_fits_gpl_maw(ident, maw_spdx)
    if expr.op == "OR":
        return any(_fits_gpl_maw(operand, maw_spdx) for operand in expr.operands)
    return all(_fits_gpl_maw(operand, maw_spdx) for operand in expr.operands)


def fits_gpl_maw(prey_spdx: str | None, maw_spdx: str | None) -> bool:
    """``decide_for_class``'s copyleft-compatibility question, asked of a whole expression."""
    ident = normalize(prey_spdx)
    if ident is None:
        return False
    expr = parse_expression(ident)
    if expr is None:
        return False
    return _fits_gpl_maw(expr, maw_spdx)


def governing_id(spdx: str | None) -> str | None:
    """The identifier an expression's class rests on, for explaining a verdict.

    When two operands share a class the first one wins, so this names *a* term that produced the
    class and not necessarily the only one. That makes it good for a reason string and unfit for
    a compatibility test: use :func:`fits_gpl_maw` for those.
    """
    ident = normalize(spdx)
    if ident is None:
        return None
    expr = parse_expression(ident)
    return _evaluate(expr)[1] if expr is not None else ident


def classify(spdx: str | None) -> LicenseClass:
    ident = normalize(spdx)
    if ident is None:
        return LicenseClass.NONE
    expr = parse_expression(ident)
    if expr is None:
        return LicenseClass.UNKNOWN
    return _evaluate(expr)[0]


def _classify_id(ident: str) -> LicenseClass:
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
            # Every copyleft term the recipient must honour has to fit this maw's version, and an
            # expression can carry several. Asking the whole expression keeps the answer the same
            # whichever order the terms were written in.
            if fits_gpl_maw(prey, maw_spdx):
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
    # Something was read and could not be classified. That is a different situation from "nothing
    # is granted": a human can read it in a minute, and until then no mode is honest.
    return Verdict(Mode.HUMAN, human_review=True, reason=f"unrecognised license {prey or '?'}")


# Owning the repository lets its owner relicense what they wrote. It does not launder code that
# arrived from someone else, and a copyleft or source-available license on one's own repository is
# usually a sign that some of it did.
_OWN_NEEDS_REVIEW = frozenset(
    {
        LicenseClass.LGPL,
        LicenseClass.GPL,
        LicenseClass.AGPL,
        LicenseClass.SOURCE_AVAILABLE,
        LicenseClass.DOCS_RESTRICTED,
        LicenseClass.UNKNOWN,
    }
)


def decide_related(
    prey_spdx: str | None,
    maw: MawClass,
    maw_spdx: str | None,
    relationship: Relationship,
) -> Verdict:
    """The verdict once the maw's relationship to the prey is known."""
    if relationship is Relationship.BYPASS:
        return Verdict(
            Mode.COPY,
            human_review=True,
            reason="license checks bypassed by .crab.yml (trust.bypass_license)",
        )
    if relationship is Relationship.OWN:
        cls = classify(normalize(prey_spdx))
        if cls in _OWN_NEEDS_REVIEW:
            return Verdict(
                Mode.COPY,
                human_review=True,
                reason=(
                    f"same owner, but the prey is {cls.value}: check the parts of it that came "
                    "from someone else before copying"
                ),
            )
        return Verdict(Mode.COPY, reason="same owner: the maw's owner also owns the prey")
    return decide_for_class(prey_spdx, maw, maw_spdx)


def decide(
    prey_spdx: str | None,
    maw_spdx: str | None,
    *,
    relationship: Relationship | str = Relationship.FOREIGN,
) -> Verdict:
    return decide_related(prey_spdx, maw_class(maw_spdx), maw_spdx, Relationship(str(relationship)))


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
