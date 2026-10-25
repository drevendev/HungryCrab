"""License detection from license files, manifests, file headers and the forge API."""

from __future__ import annotations

import json
import re
import stat
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..fs import is_link_stat, read_text
from .matrix import (
    LicenseClass,
    canonical_expression,
    classify,
    license_terms,
    normalize,
    parse_expression,
    restriction_rank,
    restricts_copying,
    selected_terms,
)

_SPDX_ID_RE = re.compile(
    r"^[ \t]*(?:(?://|\#|/\*|\*|<!--)[ \t]*)?SPDX-License-Identifier:[ \t]*(.+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_LICENSE_FILE_RE = re.compile(
    r"^(LICENSE|LICENCE|COPYING|UNLICENSE|COPYRIGHT)(?:[-_.].*)?$", re.IGNORECASE
)
_NOTICE_FILE_RE = re.compile(
    r"^(NOTICE|THIRD[-_ ]PARTY[-_ ]NOTICES?|3RD[-_ ]PARTY.*)(\..*)?$", re.I
)
_NAMED_LICENSE_RE = re.compile(r"^(?:LICENSE|LICENCE|COPYING)[-_.](?P<name>[A-Za-z0-9.+]+)", re.I)
# The other convention: the license name comes first, `apache-2.0.LICENSE`, `cc-by-4.0.LICENSE`.
_SUFFIX_LICENSE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9.+-]*)[-_.](?:LICEN[CS]E|COPYING)(?:\.[A-Za-z0-9]+)?$", re.I
)
# A directory of license texts, one per license (the REUSE convention).
LICENSE_DIRS = frozenset({"licenses", "licences", "license-files"})
# `LICENSE.md` names a file format, not a license. Without this, the name reader called the
# license "md" with 0.6 confidence and the repository looked licensed under something unknown.
_TEXT_SUFFIXES = frozenset(
    {"md", "txt", "rst", "html", "htm", "adoc", "asciidoc", "markdown", "text", "doc"}
)
# A name that starts with LICENSE is a licence file when what follows is a licence name or a text
# format. When it is a programming language or a data format, it is the code that reads licences
# (`license.py`) or a digest of one (`license.json`) — and once nested licence files could decide
# a verdict, a source file called `license.py` would have made a split repository HUMAN.
_NOT_LICENCE_SUFFIXES = frozenset(
    {
        "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "go", "rs", "java", "kt", "kts",
        "rb", "php", "c", "h", "cc", "cpp", "hpp", "cs", "fs", "swift", "scala", "dart", "lua",
        "sh", "bash", "ps1", "bat", "cmd",
        "json", "jsonl", "yaml", "yml", "toml", "xml", "ini", "cfg", "lock", "csv", "tsv", "sql",
        "png", "jpg", "jpeg", "gif", "svg", "ico", "pdf",
    }
)  # fmt: skip
# Phrases that say a single file covers several situations rather than granting one license.
_SPLIT_RE = re.compile(
    r"portions of (?:this|the) \w+(?: \w+)? (?:are|is) licensed"
    r"|licensed as follows"
    r"|licens\w* transition|transitioning from the"
    r"|remain licensed under"
    r"|are not licensed"
    # Timescale's root file: "Source code in this repository is variously licensed under ...".
    r"|variously licen[cs]ed"
    r"|licen[cs]ed under (?:several|different|multiple) licen[cs]es",
    re.IGNORECASE,
)
# Licenses named in prose. Only consulted when `_SPLIT_RE` already said the file is a patchwork,
# so a passing mention inside an ordinary license text cannot move the verdict.
_MENTIONS: tuple[tuple[str, str], ...] = (
    ("apache license", "Apache-2.0"),
    ("apache-2.0", "Apache-2.0"),
    ("mit license", "MIT"),
    ("bsd 3-clause", "BSD-3-Clause"),
    ("bsd 2-clause", "BSD-2-Clause"),
    ("mozilla public license", "MPL-2.0"),
    ("affero general public license", "AGPL-3.0-only"),
    ("lesser general public license", "LGPL-3.0-only"),
    ("general public license", "GPL-3.0-only"),
    ("creative commons attribution 4.0", "CC-BY-4.0"),
    ("cc-by-4.0", "CC-BY-4.0"),
    ("cc-by-sa", "CC-BY-SA-4.0"),
    ("elastic license", "Elastic-2.0"),
    ("server side public license", "SSPL-1.0"),
    ("business source license", "BUSL-1.1"),
    ("functional source license", "FSL-1.1-ALv2"),
    ("polyform", "LicenseRef-PolyForm"),
    ("sustainable use license", "LicenseRef-SustainableUse"),
    ("commons clause", "Commons-Clause"),
    ("enterprise license", "LicenseRef-Proprietary"),
    ("commercial license", "LicenseRef-Proprietary"),
)

_ISC_GRANT = (
    "permission to use, copy, modify, and/or distribute this software for any purpose "
    "with or without fee"
)

# (spdx, required phrases, forbidden phrases); evaluated on lower-cased, whitespace-collapsed text.
_Signature = tuple[str, tuple[str, ...], tuple[str, ...]]

# Source-available licences are checked before everything else, and on their name alone.
#
# A BUSL-1.1 file names its Change License in its own Parameters block — Apache for Sentry and
# CockroachDB, MPL for HashiCorp's Terraform, Vault and Consul — and an FSL file names its future
# licence the same way. Checked after the permissive signatures, each of them used to read as the
# licence it merely names, and source-available code was offered for copying (HungryCrab#86).
#
# The asymmetry with the table below is the safety argument. A document that is BUSL and mentions
# Apache is BUSL; an Apache licence never mentions BUSL. So a lenient match here can only err
# towards the restrictive reading, and a lenient match there could only err the other way.
_SOURCE_AVAILABLE_SIGNATURES: tuple[_Signature, ...] = (
    ("BUSL-1.1", ("business source license",), ()),
    ("SSPL-1.0", ("server side public license",), ()),
    ("Elastic-2.0", ("elastic license", "2.0"), ()),
    ("Commons-Clause", ("commons clause",), ()),
    ("FSL-1.1-MIT", ("functional source license", "mit future license"), ()),
    ("FSL-1.1-ALv2", ("functional source license",), ()),
    ("PolyForm-Noncommercial-1.0.0", ("polyform noncommercial license",), ()),
    ("PolyForm-Small-Business-1.0.0", ("polyform small business license",), ()),
    ("LicenseRef-PolyForm", ("polyform", "license"), ()),
    ("LicenseRef-SustainableUse", ("sustainable use license",), ()),
)

# More specific entries come first. The licences other documents cite by name — Apache, MPL, EPL
# — need a phrase from their own operative text, not just the name: a custom licence that says
# "the rest of this product is under the Apache License, Version 2.0" is not the Apache License,
# and without a signature of its own it has to stay unreadable rather than become Apache.
_SIGNATURES: tuple[_Signature, ...] = (
    (
        "Apache-2.0",
        ("apache license", "terms and conditions for use, reproduction, and distribution"),
        (),
    ),
    (
        "Apache-2.0",
        ("apache license", "you may not use this file except in compliance with the license"),
        (),
    ),
    ("MPL-2.0", ("mozilla public license", "covered software"), ()),
    (
        "MPL-2.0",
        ("this source code form is subject to the terms of the mozilla public license",),
        (),
    ),
    (
        "EPL-2.0",
        (
            "eclipse public license",
            "2.0",
            "provided under the terms of this eclipse public license",
        ),
        (),
    ),
    (
        "EPL-1.0",
        (
            "eclipse public license",
            "1.0",
            "provided under the terms of this eclipse public license",
        ),
        (),
    ),
    ("Unlicense", ("this is free and unencumbered software released into the public domain",), ()),
    ("CC0-1.0", ("cc0 1.0",), ()),
    ("CC-BY-NC-SA-4.0", ("attribution-noncommercial-sharealike 4.0",), ()),
    ("CC-BY-NC-4.0", ("attribution-noncommercial 4.0",), ()),
    ("CC-BY-ND-4.0", ("attribution-noderivatives 4.0",), ()),
    ("CC-BY-SA-4.0", ("attribution-sharealike 4.0",), ()),
    ("CC-BY-4.0", ("attribution 4.0 international",), ()),
    ("CC-BY-SA-3.0", ("attribution-sharealike 3.0",), ()),
    ("CC-BY-3.0", ("attribution 3.0 unported",), ()),
    ("GFDL-1.3-only", ("gnu free documentation license",), ()),
    ("BSL-1.0", ("boost software license",), ()),
    ("WTFPL", ("do what the fuck you want to public license",), ()),
    ("Artistic-2.0", ("artistic license 2.0",), ()),
    ("PostgreSQL", ("postgresql license",), ()),
    ("Python-2.0", ("python software foundation license",), ()),
    ("BlueOak-1.0.0", ("blue oak model license",), ()),
    (
        "Zlib",
        ("altered source versions must be plainly marked as such", "must not be misrepresented"),
        (),
    ),
    (
        "ISC",
        (
            _ISC_GRANT,
            "provided that the above copyright notice and this permission notice appear",
        ),
        (),
    ),
    ("0BSD", (_ISC_GRANT,), ()),
    (
        "MIT",
        (
            "permission is hereby granted, free of charge",
            "without restriction",
            "shall be included in all copies or substantial portions",
        ),
        (),
    ),
    ("MIT-0", ("permission is hereby granted, free of charge", "without restriction"), ()),
    ("BSD-4-Clause", ("redistributions of source code must retain", "advertising materials"), ()),
    ("BSD-3-Clause", ("redistributions of source code must retain", "neither the name"), ()),
    ("BSD-2-Clause", ("redistributions of source code must retain",), ()),
)

_OSI = "License :: OSI Approved :: "
_CLASSIFIER_MAP: dict[str, str] = {
    _OSI + "MIT License": "MIT",
    _OSI + "Apache Software License": "Apache-2.0",
    _OSI + "BSD License": "BSD-3-Clause",
    _OSI + "ISC License (ISCL)": "ISC",
    _OSI + "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    _OSI + "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    _OSI + "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    _OSI + "GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    _OSI + "GNU Affero General Public License v3": "AGPL-3.0-only",
    _OSI + "GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0-or-later",
    _OSI + "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0-only",
    _OSI + "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    _OSI + "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    _OSI + "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    _OSI + "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    _OSI + "The Unlicense (Unlicense)": "Unlicense",
    _OSI + "zlib/libpng License": "Zlib",
    _OSI + "Eclipse Public License 2.0 (EPL-2.0)": "EPL-2.0",
    _OSI + "Python Software Foundation License": "PSF-2.0",
    "License :: Public Domain": "CC0-1.0",
    "License :: Other/Proprietary License": "LicenseRef-Proprietary",
}


def is_license_file_name(name: str) -> bool:
    """Is this a name a licence is kept under?

    ``LICENSE``, ``LICENSE.md``, ``LICENSE-MIT``, ``COPYING.LESSER`` and ``apache-2.0.LICENSE``
    are. ``license.py`` and ``license.json`` are not, whatever their first seven letters say.
    """
    if not (_LICENSE_FILE_RE.match(name) or _SUFFIX_LICENSE_RE.match(name)):
        return False
    suffix = name.rsplit(".", 1)[1].lower() if "." in name else ""
    return suffix not in _NOT_LICENCE_SUFFIXES


def license_name_from_file(name: str) -> str | None:
    """The SPDX id a license file's *name* declares, or None when the name says nothing.

    ``LICENSE-APACHE`` declares Apache-2.0. ``LICENSE.md`` declares nothing: ``md`` is a file
    format. ``apache-2.0.LICENSE`` declares Apache-2.0 the other way round.
    """
    named = _NAMED_LICENSE_RE.match(name)
    if named:
        raw = named.group("name")
        if raw.lower() in _TEXT_SUFFIXES:
            return None
        return normalize(raw)
    suffixed = _SUFFIX_LICENSE_RE.match(name)
    if suffixed:
        return normalize(suffixed.group("name"))
    return None


def combined_root_grant(grants: Mapping[str, str]) -> str | None:
    """Combine proven root texts; named components cannot override an explicit expression."""
    explicit = [grant for name, grant in grants.items() if license_name_from_file(name) is None]
    if explicit:
        primary = explicit[0]
        canonical = canonical_expression(primary)
        components = {canonical_expression(term) for term in license_terms(primary)}
        if any(canonical_expression(grant) != canonical for grant in explicit):
            return None
        if any(
            canonical_expression(grant) not in components | {canonical} for grant in grants.values()
        ):
            return None
        return normalize(primary)
    distinct = sorted(set(grants.values()))
    if not distinct:
        return None
    if len(distinct) == 1:
        return normalize(distinct[0])
    alternatives = set(distinct) <= {"MIT", "Apache-2.0"} and all(
        _NAMED_LICENSE_RE.match(name) and license_name_from_file(name) == grant
        for name, grant in grants.items()
    )
    operator = " OR " if alternatives else " AND "
    return normalize(operator.join(f"({grant})" for grant in distinct))


def licenses_mentioned(text: str) -> list[str]:
    """SPDX ids named in prose, for a file that says several licenses apply."""
    lowered = _norm(text)
    found: list[str] = []
    for phrase, spdx in _MENTIONS:
        if phrase in lowered and spdx not in found:
            found.append(spdx)
    if re.search(r"\bmit\b", lowered) and "MIT" not in found:
        found.append("MIT")
    # "affero general public license" also contains "general public license".
    if "AGPL-3.0-only" in found or "LGPL-3.0-only" in found:
        found = [item for item in found if item != "GPL-3.0-only"]
    return found


def is_split_license_text(text: str) -> bool:
    """True when one file says different parts of the repository carry different licenses."""
    return bool(_SPLIT_RE.search(text))


_QUOTES = str.maketrans({chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'})


def _norm(text: str) -> str:
    return " ".join(text.translate(_QUOTES).lower().split())


def find_spdx_identifier(text: str) -> str | None:
    found: str | None = None
    for index, match in enumerate(_SPDX_ID_RE.finditer(text)):
        expression = re.sub(r"[ \t]*(?:\*/|-->)$", "", match.group(1)).strip()
        if (
            index >= 64
            or re.fullmatch(r"[A-Za-z0-9.+()\- \t]+", expression) is None
            or parse_expression(expression) is None
        ):
            return "NOASSERTION"
        ident = normalize(expression)
        if found is not None and canonical_expression(found) != canonical_expression(ident):
            return "NOASSERTION"
        found = ident
    return found


# Every GNU licence carries the first phrase in its own text, and every GNU per-file notice
# carries the second. Naming "the GNU General Public License" is neither: a BUSL file whose
# Change License is the GPL names it too, and so does a proprietary licence explaining what it is
# not.
_GNU_OPERATIVE = (
    "everyone is permitted to copy and distribute verbatim copies of this license document",
    "you can redistribute it and/or modify it under the terms of the gnu",
)


def _matches(normalized: str, required: tuple[str, ...], forbidden: tuple[str, ...]) -> bool:
    return all(phrase in normalized for phrase in required) and not any(
        phrase in normalized for phrase in forbidden
    )


def _detect_gnu(head: str, normalized: str = "") -> tuple[str | None, float]:
    if "general public license" not in head:
        return None, 0.0
    if not any(phrase in (normalized or head) for phrase in _GNU_OPERATIVE):
        return None, 0.0
    title = re.search(r"\bgnu (?:(affero|lesser|library) )?general public license\b", head)
    if title is None:
        return None, 0.0
    family = title.group(1) or ""
    # The first named GNU grant determines the family; a later preamble mention of LGPL
    # cannot turn a reformatted GPL title into LGPL. Version evidence stays near that title.
    version_text = head[title.end() : title.end() + 180]
    later = "any later version" in head
    if family == "affero":
        return ("AGPL-3.0-or-later" if later else "AGPL-3.0-only"), 0.95
    if family in {"lesser", "library"}:
        if "version 3" in version_text:
            return ("LGPL-3.0-or-later" if later else "LGPL-3.0-only"), 0.95
        if "version 2.1" in version_text:
            return ("LGPL-2.1-or-later" if later else "LGPL-2.1-only"), 0.95
        return ("LGPL-2.0-only", 0.95) if "version 2" in version_text else (None, 0.0)
    if "version 3" in version_text:
        return ("GPL-3.0-or-later" if later else "GPL-3.0-only"), 0.95
    if "version 2" in version_text:
        return ("GPL-2.0-or-later" if later else "GPL-2.0-only"), 0.95
    return None, 0.0


# "Covered by a triple license under AGPL-3.0, SSPL and the Elastic License": a choice, and one of
# its branches is open source. Letting the first source-available name decide read that as SSPL,
# IDEAS_ONLY even for an AGPL maw the AGPL branch lets copy — master had answered AGPL, by accident
# of checking the GNU head first. The crab cannot build the OR from prose reliably, so such a file
# stays unreadable and a human picks the branch. A choice among source-available licences only is
# source-available whichever branch is taken, and is still decided by name.
_CHOICE_RE = re.compile(
    r"\b(?:dual|triple|tri|multi)[- ]?licen[cs]|\byour choice of\b|\blicen[cs]ed under either\b",
    re.IGNORECASE,
)


def _offers_an_open_choice(text: str, normalized: str) -> bool:
    """A choice of licences in which at least one branch is not source-available."""
    if not _CHOICE_RE.search(normalized):
        return False
    classes = {classify(spdx) for spdx in licenses_mentioned(text)}
    return LicenseClass.SOURCE_AVAILABLE in classes and len(classes) > 1


def detect_from_text(text: str) -> tuple[str | None, float]:
    """Return (spdx, confidence) for the text of a license file."""
    if not text.strip():
        return None, 0.0
    ident = find_spdx_identifier(text)
    body = _SPDX_ID_RE.sub("", text) if ident else text
    normalized = _norm(body)
    # An MIT-looking grant must account for the operative text, including appended riders.
    # A tag embedded in that same document cannot overrule contradictory terms.
    if "permission is hereby granted, free of charge" in normalized:
        grant = _detect_mit(body)
        if ident and canonical_expression(ident) != canonical_expression(grant[0]):
            return None, 0.0
        return grant
    if re.search(
        r"\b(?:non[- ]commercial|no redistribution|internal use only|enterprise subscription"
        r"|shall be used for good|human[- ]rights restrictions?)\b"
        r"|\b(?:commercial use|model training)\b.{0,40}\b(?:prohibited|forbidden|not permitted)\b"
        r"|\b(?:may|must) not\b.{0,80}\b(?:hosted|managed) service\b",
        normalized,
    ):
        return None, 0.0
    if ident:
        remaining = [line.strip() for line in body.splitlines() if line.strip()]
        if not remaining or all(_copyright_metadata(line) for line in remaining):
            return ident, 0.99
        detected_grant, confidence = detect_from_text(body)
        if canonical_expression(detected_grant) == canonical_expression(ident):
            return ident, confidence
        return None, 0.0
    if _mit_apache_choice(normalized):
        return "MIT OR Apache-2.0", 0.95
    head = normalized[:700]
    if not _offers_an_open_choice(text, normalized):
        for spdx, required, forbidden in _SOURCE_AVAILABLE_SIGNATURES:
            if _matches(normalized, required, forbidden):
                return spdx, 0.95
    gnu, confidence = _detect_gnu(head, normalized)
    if gnu:
        return gnu, confidence
    for spdx, required, forbidden in _SIGNATURES:
        if _matches(normalized, required, forbidden):
            return spdx, 0.95
    if "all rights reserved" in normalized and len(normalized) < 3000:
        return "LicenseRef-Proprietary", 0.5
    return None, 0.0


def _mit_apache_choice(text: str) -> bool:
    """Only a complete established choice statement, never arbitrary enclosing custom terms."""
    if len(text) > 500:
        return False
    mit = r"(?:the )?mit(?: license)?"
    apache = r"(?:the )?apache(?:-2\.0| license(?:,? version)? 2\.0)"
    prefix = (
        r"(?:(?:this (?:project|software|repository) is )?"
        r"(?:dual[- ]licensed(?: under)?|licensed under either"
        r"|licensed under|your choice of)[: ]+)?"
        r"(?:you may use it under )?"
    )
    branches = rf"(?:{mit} or (?:under )?{apache}|{apache} or (?:under )?{mit})"
    return (
        re.fullmatch(prefix + branches + r"(?:,? at your (?:option|choice))?\.?", text) is not None
    )


_MIT_GRANT = (
    "permission is hereby granted, free of charge, to any person obtaining a copy of this "
    'software and associated documentation files (the "software"), to deal in the software '
    "without restriction, including without limitation the rights to use, copy, modify, merge, "
    "publish, distribute, sublicense, and/or sell copies of the software, and to permit persons "
    "to whom the software is furnished to do so"
)
_MIT_NOTICE = (
    "the above copyright notice and this permission notice shall be included in all copies or "
    "substantial portions of the software."
)
_MIT_WARRANTY = (
    'the software is provided "as is", without warranty of any kind, express or implied, '
    "including but not limited to the warranties of merchantability, fitness for a particular "
    "purpose and noninfringement. in no event shall the authors or copyright holders be liable "
    "for any claim, damages or other liability, whether in an action of contract, tort or "
    "otherwise, arising from, out of or in connection with the software or the use or other "
    "dealings in the software."
)


def _copyright_metadata(line: str) -> bool:
    """Recognize a bounded year/holder notice, never arbitrary copyright-prefixed prose.

    Unrecognized notices need review. Holder names allow identifiers, capitalized name words,
    conventional connectors/initials/company suffixes and a final contact address.
    """
    line = re.sub(r"^(?://|\#|/\*|\*|<!--)\s*", "", line.strip())
    line = re.sub(r"\s*(?:\*/|-->)$", "", line).strip()
    match = re.fullmatch(
        r"(?:copyright\s*(?:(?:\(c\)|\u00a9)\s*)?|spdx-filecopyrighttext:\s*|\u00a9\s*)"
        r"\d{4}(?:\s*[-,]\s*\d{2,4})*\s+(.{1,300})",
        line.strip(),
        re.I,
    )
    if match is None:
        return False
    holder = match.group(1)
    # Contacts and the conventional reservation can follow each other, with terminal
    # punctuation. Remove only these complete suffixes, in a bounded number of passes.
    for _ in range(3):
        previous = holder
        holder = re.sub(r"\s+all rights reserved\.?$", "", holder.removesuffix("."), flags=re.I)
        holder = re.sub(r"\s+<(?:[^\s<>@]+@[^\s<>@]+|https://[^\s<>]+)>$", "", holder)
        if holder == previous:
            break
    words = holder.split()
    if not words or len(words) > 16:
        return False
    # Longer unstructured phrases are not a holder-name field. In particular, capitalization
    # cannot turn a sentence after the owner into metadata. Recognize explicit name forms.
    connectors = {"and", "of", "the", "de", "van", "von"}
    groups = holder.split(" and ")
    name_form = all(
        0 < sum(word.casefold() not in connectors for word in group.split()) <= 2
        for group in groups
    )
    initials = all(re.fullmatch(r"(?:[A-Z]\.){1,6}", word) for word in words[:-1])
    entity_suffixes = {
        "inc",
        "ltd",
        "corp",
        "co",
        "llc",
        "foundation",
        "association",
        "institute",
        "corporation",
        "authors",
        "contributors",
    }
    organization = words[-1].rstrip(",.").casefold() in entity_suffixes and (
        len(words) <= 3
        or (
            len(words) == 4
            and words[-2].rstrip(",.").casefold() in entity_suffixes
            and words[-1].rstrip(",.").casefold() in {"inc", "ltd", "corp", "co", "llc"}
        )
    )
    university = any(
        holder.casefold().startswith(prefix) and len(holder[len(prefix) :].split()) <= 2
        for prefix in ("university of ", "the university of ")
    )
    if not (name_form or initials or organization or university):
        return False
    for word in words:
        word = word.rstrip(",")
        if re.fullmatch(r"(?:[A-Z]\.){1,6}", word):
            continue
        if word.casefold() in {"inc.", "ltd.", "corp.", "co."}:
            continue
        plain = word.replace("-", "").replace("'", "").replace("\u2019", "")
        if not plain.isalnum():
            return False
        if len(words) > 1 and word not in connectors and word[:1].islower():
            return False
    return True


def _detect_mit(text: str) -> tuple[str | None, float]:
    start = re.search(r"permission\s+is\s+hereby\s+granted,\s*free\s+of\s+charge", text, re.I)
    if start is None or start.start() > 1500:
        return None, 0.0
    body = _norm(text[start.start() :])
    title = re.compile(
        r"(?:(?:the )?mit(?:-0| no attribution)?(?: license)?(?: \(mit\))?|expat license)[.:]?"
    )
    for raw in text[: start.start()].splitlines():
        line = _norm(raw.strip("#/* \t"))
        if not line or title.fullmatch(line) or line == "all rights reserved.":
            continue
        if not _copyright_metadata(raw.strip("#/* \t")):
            return None, 0.0
    mit = f"{_MIT_GRANT}, subject to the following conditions: {_MIT_NOTICE} {_MIT_WARRANTY}"
    mit0 = f"{_MIT_GRANT}. {_MIT_WARRANTY}"
    if body == mit:
        return "MIT", 0.99
    if body == mit0:
        return "MIT-0", 0.99
    return None, 0.0


def manifest_license(name: str, text: str) -> tuple[str | None, str]:
    """(spdx, note) declared by a manifest file, or (None, '')."""
    lowered = name.lower()
    try:
        if lowered == "package.json" or lowered == "composer.json":
            data = json.loads(text)
            if isinstance(data, dict):
                value = data.get("license")
                if isinstance(value, dict):
                    value = value.get("type")
                if isinstance(value, str) and value.strip():
                    return normalize(value), ""
                legacy = data.get("licenses")
                if isinstance(legacy, list) and legacy and isinstance(legacy[0], dict):
                    first = legacy[0].get("type")
                    if isinstance(first, str):
                        return normalize(first), "legacy licenses array"
            return None, ""
        if lowered == "pyproject.toml":
            return _pyproject_license(tomllib.loads(text))
        if lowered == "cargo.toml":
            data = tomllib.loads(text)
            package = data.get("package", {})
            value = package.get("license") if isinstance(package, dict) else None
            return (normalize(value) if isinstance(value, str) else None), ""
        if lowered.endswith((".csproj", ".fsproj", ".vbproj", ".props")):
            # Strip in Python: `\s*([^<]+?)\s*</` rescanned a whitespace run from every
            # position inside it, minutes for one hostile project file.
            for found in re.finditer(
                r"<PackageLicenseExpression>([^<]*)</PackageLicenseExpression>", text
            ):
                if found.group(1).strip():
                    return normalize(found.group(1).strip()), ""
            return None, ""
        if lowered == "setup.cfg":
            # `[ \t]*`, not `\s*`: at every line start `\s*` ran across all the blank lines below.
            match = re.search(r"^[ \t]*license\s*=\s*(.+)$", text, re.MULTILINE)
            return (normalize(match.group(1).strip()) if match else None), ""
        if lowered == "setup.py":
            match = re.search(r"""license\s*=\s*["']([^"']+)["']""", text)
            return (normalize(match.group(1)) if match else None), ""
    except (ValueError, TypeError, tomllib.TOMLDecodeError, RecursionError, MemoryError):
        # Hostile nesting exhausts the parser's stack, not its grammar (#330): it is the same
        # unparseable manifest, and it must not take the licence verdict down with it.
        return None, "unparseable manifest"
    return None, ""


def _pyproject_license(data: dict[str, Any]) -> tuple[str | None, str]:
    project = data.get("project")
    if isinstance(project, dict):
        value = project.get("license")
        if isinstance(value, str) and value.strip():
            return normalize(value), ""
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                return normalize(text.strip()), "license.text"
        classifiers = project.get("classifiers")
        if isinstance(classifiers, list):
            for item in classifiers:
                if isinstance(item, str) and item in _CLASSIFIER_MAP:
                    note = "from trove classifier"
                    if "BSD License" in item:
                        note = "trove classifier 'BSD License' does not name the variant"
                    return _CLASSIFIER_MAP[item], note
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict) and isinstance(poetry.get("license"), str):
            return normalize(poetry["license"]), "tool.poetry"
    return None, ""


@dataclass
class Evidence:
    source: str
    path: str
    spdx: str | None
    confidence: float
    note: str = ""


@dataclass
class LicenseFindings:
    spdx: str | None = None
    confidence: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)
    license_files: list[str] = field(default_factory=list)
    notice_files: list[str] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    header_counts: dict[str, int] = field(default_factory=dict)
    headers_scanned: int = 0
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    human_review: bool = False
    # Why the repository ended up with this identifier. `NOASSERTION` used to mean all four of
    # the last values at once, which is the same as meaning nothing.
    #   single      one license, one identifier
    #   dual        several license files offered as alternatives (SPDX `OR`)
    #   split       several licenses that apply to different parts (conservative, SPDX `AND`)
    #   per-path    nothing at the root, but nested packages carry their own licenses
    #   unreadable  a license file is there and no known license text matched it
    #   absent      no license file, no manifest declaration, no API license
    resolution: str = "absent"
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [asdict(item) for item in self.evidence]
        return data


def _same_family(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    if canonical_expression(a) == canonical_expression(b):
        return True
    fam_a = a.split("-")[0].upper()
    fam_b = b.split("-")[0].upper()
    if fam_a == fam_b == "BSD":
        return True
    return (
        classify(a) == classify(b)
        and a.rstrip("+").split("-or-later")[0] == b.rstrip("+").split("-or-later")[0]
    )


def _own_entry(path: Path, kind: Callable[[int], bool]) -> bool:
    """Whether ``path`` is a ``kind`` of entry itself, never a symlink or junction to one.

    A link can name a licence anywhere on the disk, and evidence about a prey must come from
    the prey's own tree.
    """
    try:
        info = path.lstat()
    except OSError:
        return False
    return kind(info.st_mode) and not is_link_stat(info)


def _license_candidates(
    root: Path,
    entries: Sequence[str],
    findings: LicenseFindings,
    allowed_files: frozenset[str] | None = None,
) -> list[tuple[str, str | None]]:
    """Every license file at the root or in a `LICENSES/` directory, with what its name declares.

    Notice files are recorded on ``findings`` on the way past, because they are found by the same
    walk and nothing else looks at the root.
    """
    found: list[tuple[str, str | None]] = []
    for name in sorted(entries):
        if _NOTICE_FILE_RE.match(name) and _own_entry(root / name, stat.S_ISREG):
            findings.notice_files.append(name)
            continue
        if not is_license_file_name(name):
            continue
        if _own_entry(root / name, stat.S_ISREG):
            found.append((name, license_name_from_file(name)))
    # Read the directory name from the listing, not from the probe: on Windows `root / "licenses"`
    # happily opens `LICENSES/` and would then report a path that does not exist on Linux.
    for directory in sorted(name for name in entries if name.lower() in LICENSE_DIRS):
        path = root / directory
        if not _own_entry(path, stat.S_ISDIR):
            continue
        try:
            candidates = (
                path.iterdir()
                if allowed_files is None
                else (
                    root / rel
                    for rel in allowed_files
                    if PurePosixPath(rel).parent.as_posix() == directory
                )
            )
            children = sorted(child for child in candidates if _own_entry(child, stat.S_ISREG))
        except OSError:
            continue
        # `LICENSES/MIT.txt`: here the whole stem is the license name, by the REUSE convention.
        found.extend((f"{path.name}/{child.name}", normalize(child.stem)) for child in children)
    return found


def _nested_licences(findings: LicenseFindings) -> list[tuple[str, str]]:
    """(path, spdx) of every nested licence file that disagrees with the root, readable or not."""
    return [
        (str(item["path"]), str(item["spdx"]))
        for item in findings.exceptions
        if item.get("kind") == "nested-license"
    ]


def _heavier_than_root(findings: LicenseFindings) -> list[tuple[str, str, str]]:
    """(path, spdx, what) for the nested licences the repository verdict does not answer for.

    Everything the root's verdict cannot carry counts, not only what restricts copying. An Apache
    subtree under an MIT root is copyable, but its NOTICE obligation is not in the MIT verdict and
    there are no per-path verdicts to put it in, so a nutrient from that subtree would go out as
    COPY without it — the failure #57 removed at the licence level, one level down (raised in
    review of #90). An unreadable licence counts for the same reason: its obligations are unknown,
    so nothing shows they are covered.

    This is the project's own tree only. Vendored code, sample corpora and virtualenvs are
    `vendored-license` and never reach this list, which is what made flagging it bearable. A
    licence already folded into a declared split is not repeated here.
    """
    root = restriction_rank(findings.spdx)
    folded = set(re.findall(r"[^\s()]+", findings.spdx or ""))
    heavier: list[tuple[str, str, str]] = []
    for path, spdx in _nested_licences(findings):
        if spdx in folded:
            continue
        if spdx == "NOASSERTION":
            heavier.append((path, spdx, "cannot be read"))
        elif restriction_rank(spdx) > root:
            what = (
                "restricts copying" if restricts_copying(spdx) else "adds a notice or attribution"
            )
            heavier.append((path, spdx, what))
    return heavier


def _resolve_unclear(
    findings: LicenseFindings, split_mentions: Sequence[str], *, root_declares_split: bool = False
) -> None:
    """Separate the three situations that used to share one `NOASSERTION`.

    A file that says several licenses apply, a license file nobody can read, and a repository
    that licenses nothing at the root but licenses its packages are three different problems with
    three different answers. ``NOASSERTION`` is SPDX's own word for "looked, cannot assert", and
    it classifies as unknown, which is the mode ``HUMAN``.
    """
    # A repository that offers a choice of licenses has already answered the question; a file that
    # merely mentions other licenses cannot take that choice away.
    #
    # When the root declares the split, the licence files in the tree say where it falls, and they
    # join it exactly as a licence named in the prose would — including one the crab cannot read,
    # which is how an unnamed restrictive licence stops inheriting the permissive verdict.
    folded = _nested_licences(findings) if root_declares_split else []
    mentions = list(dict.fromkeys([*split_mentions, *(spdx for _, spdx in folded)]))
    if mentions and findings.resolution != "dual":
        current = findings.spdx.split(" AND ") if findings.spdx else []
        combined = sorted({*mentions, *current})
        if set(combined) != set(current):
            findings.resolution = "split"
            findings.candidates = combined
            findings.spdx = " AND ".join(combined)
            findings.notes.append(
                "the license file says different parts carry different licenses "
                f"({', '.join(combined)}); the most restrictive one is used for the whole "
                "repository until a nutrient names its own path"
            )
            if folded:
                where = ", ".join(f"{path} ({spdx})" for path, spdx in folded)
                findings.notes.append(
                    "the root declares the split and these nested licence files say where it "
                    f"falls, so they join it: {where}"
                )
        return
    if findings.spdx is not None:
        return
    if findings.license_files:
        findings.resolution = "unreadable"
        findings.spdx = "NOASSERTION"
        findings.notes.append(
            f"{', '.join(findings.license_files)} is present but matches no known license text: "
            "a human has to read it"
        )
        return
    if findings.exceptions:
        paths = ", ".join(f"{item['path']} ({item['spdx']})" for item in findings.exceptions[:5])
        findings.resolution = "per-path"
        findings.spdx = "NOASSERTION"
        findings.notes.append(
            f"no license at the root, but {len(findings.exceptions)} nested license file(s) "
            f"carry their own: {paths}. Each package has to be judged on its own"
        )


def detect_in_repo(
    root: Path,
    code_files: Sequence[str],
    *,
    root_entries: Sequence[str] | None = None,
    manifests: Sequence[str] = (),
    nested_license_files: Sequence[str] = (),
    vendored_license_files: Sequence[str] = (),
    api_spdx: str | None = None,
    max_header_files: int = 400,
    allowed_files: frozenset[str] | None = None,
) -> LicenseFindings:
    """Combine every source of truth into one finding for the repository at ``root``."""
    findings = LicenseFindings()
    entries = list(root_entries) if root_entries is not None else _list_root(root)

    named: list[tuple[str, str]] = []  # (relative path, spdx) for files whose *name* declares one
    alternatives = True  # every named file used the `LICENSE-<name>` form: a choice, not a split
    split_mentions: list[str] = []
    root_declares_split = False
    directory_grants: list[tuple[str, str | None]] = []
    for rel, declared in _license_candidates(root, entries, findings, allowed_files):
        if "/" in rel:
            directory_grants.append((rel, declared))
            continue
        path = root / rel
        text = read_text(path, limit=200_000)
        spdx, confidence = detect_from_text(text)
        note = ""
        if spdx is None:
            # A short root pointer may designate a readable grant inside this tree. Its name,
            # a manifest classifier or the API can never replace unreadable operative terms.
            pointer = re.fullmatch(r"\s*(?:see|license(?: is)? in)\s+([\w./-]+)[.\s]*", text, re.I)
            if pointer:
                target = pointer.group(1).rstrip(".")
                parts = PurePosixPath(target).parts
                parent = root
                safe_parents = (
                    bool(parts) and not PurePosixPath(target).is_absolute() and ".." not in parts
                )
                for part in parts[:-1] if safe_parents else ():
                    parent = parent / part
                    if not _own_entry(parent, stat.S_ISDIR):
                        safe_parents = False
                        break
                if (
                    parts
                    and not PurePosixPath(target).is_absolute()
                    and ".." not in parts
                    and safe_parents
                    and (allowed_files is None or target in allowed_files)
                    and _own_entry(root / target, stat.S_ISREG)
                ):
                    spdx, confidence = detect_from_text(read_text(root / target, limit=200_000))
                    note = "readable referenced grant" if spdx else "unreadable referenced grant"
        findings.license_files.append(rel)
        findings.evidence.append(Evidence("file", rel, spdx, confidence, note))
        if spdx and declared:
            named.append((rel, spdx))
            alternatives = alternatives and bool(_NAMED_LICENSE_RE.match(PurePosixPath(rel).name))
        if is_split_license_text(text):
            root_declares_split = True
            for mention in licenses_mentioned(text):
                if mention not in split_mentions:
                    split_mentions.append(mention)

    for rel in manifests:
        path = root / rel
        if not path.is_file():
            continue
        spdx, note = manifest_license(path.name, read_text(path, limit=500_000))
        if spdx:
            findings.evidence.append(Evidence("manifest", rel, spdx, 0.7, note))

    if api_spdx and api_spdx.upper() != "NOASSERTION":
        findings.evidence.append(Evidence("api", "repos/{owner}/{repo}", normalize(api_spdx), 0.9))

    # A REUSE text supplies a declared grant; an asset's licence does not declare a root grant.
    declarations = {
        term
        for item in findings.evidence
        if item.source != "api"
        for term in license_terms(item.spdx)
    }
    if code_files and not declarations:
        headers = [find_spdx_identifier(read_text(root / rel, limit=4000)) for rel in code_files]
        if all(headers):
            declarations = {term for header in headers for term in license_terms(header)}
    for rel, declared in directory_grants:
        spdx, confidence = detect_from_text(read_text(root / rel, limit=200_000))
        if declared in declarations and canonical_expression(spdx) == canonical_expression(
            declared
        ):
            findings.license_files.append(rel)
            findings.evidence.append(
                Evidence("file", rel, spdx, confidence, "REUSE declared grant")
            )
        else:
            findings.exceptions.append(
                {
                    "path": rel,
                    "spdx": spdx or "NOASSERTION",
                    "kind": "nested-license",
                    "scope": "unknown",
                }
            )
    primary = max(
        (e for e in findings.evidence if e.source == "file" and e.spdx),
        key=lambda e: e.confidence,
        default=None,
    )
    if (
        primary is not None
        and directory_grants
        and all("/" in item.path for item in findings.evidence if item.source == "file")
    ):
        declaration = next(
            (
                item.spdx
                for item in findings.evidence
                if item.source == "manifest"
                and item.spdx
                and set(selected_terms(item.spdx)) <= declarations
            ),
            None,
        )
        if declaration:
            primary = Evidence(
                "file", primary.path, declaration, primary.confidence, "REUSE declared grant"
            )

    if primary is not None:
        findings.spdx, findings.confidence = primary.spdx, primary.confidence
        findings.resolution = "single"
        if primary.note == "REUSE declared grant" and " AND " in (primary.spdx or ""):
            findings.resolution = "split"
            findings.candidates = list(license_terms(primary.spdx))
        distinct = sorted({spdx for _, spdx in named})
        findings.candidates = distinct or findings.candidates
        if len(distinct) > 1:
            # `LICENSE-MIT` next to `LICENSE-APACHE` offers a choice. `cc-by-4.0.LICENSE` next to
            # `apache-2.0.LICENSE` does not: those cover different parts of the repository, and
            # picking the nicer one is how a documentation license gets ignored.
            docs = any(classify(spdx).value.startswith("docs") for spdx in distinct)
            known_alternatives = set(distinct) <= {"MIT", "Apache-2.0"}
            if alternatives and not docs and known_alternatives:
                established = combined_root_grant(
                    {
                        item.path: item.spdx
                        for item in findings.evidence
                        if item.source == "file" and "/" not in item.path and item.spdx
                    }
                )
                expression = parse_expression(established or "")
                if expression is None:
                    findings.spdx = "NOASSERTION"
                    findings.resolution = "unreadable"
                    findings.conflicts.append("root licence expressions disagree")
                else:
                    findings.spdx = established
                    findings.resolution = (
                        "dual"
                        if expression.op == "OR"
                        else "split"
                        if expression.op == "AND"
                        else "single"
                    )
                    findings.notes.append("named licence texts retain the explicit root expression")
            else:
                findings.resolution = "split"
                findings.spdx = " AND ".join(distinct)
                findings.notes.append(
                    "several license files that are not alternatives "
                    f"({', '.join(rel for rel, _ in named)}): every one of them is assumed to "
                    "apply somewhere, so the most restrictive wins"
                )
    elif findings.license_files:
        findings.spdx = "NOASSERTION"
        findings.resolution = "unreadable"
        findings.conflicts.append("root licence text could not establish a grant")
        findings.notes.append(
            f"{', '.join(findings.license_files)} is unreadable: a human has to read it"
        )
    else:
        fallback = max(
            (e for e in findings.evidence if e.spdx),
            key=lambda e: e.confidence,
            default=None,
        )
        if fallback is not None:
            findings.spdx, findings.confidence = fallback.spdx, fallback.confidence
            findings.resolution = "single"
            findings.notes.append(f"no license text found; using the {fallback.source} declaration")
        elif not findings.license_files:
            findings.notes.append("no license file, manifest declaration or API license")

    if any(item.source == "file" and item.spdx is None for item in findings.evidence):
        findings.spdx = "NOASSERTION"
        findings.resolution = "unreadable"
        findings.conflicts.append("an unreadable root licence leaves the grant unresolved")
    for item in findings.evidence:
        if (
            item.spdx
            and findings.spdx
            and canonical_expression(item.spdx) != canonical_expression(findings.spdx)
        ):
            if " OR " in findings.spdx and item.spdx in findings.spdx.split(" OR "):
                continue
            if item.source == "file" and item.spdx in license_terms(findings.spdx):
                continue
            if _same_family(item.spdx, findings.spdx) and item.source == "manifest":
                findings.notes.append(
                    f"{item.path} declares {item.spdx}, text says {findings.spdx}"
                )
                continue
            findings.conflicts.append(f"{item.source} {item.path} says {item.spdx}")

    _scan_headers(root, code_files, findings, max_header_files)

    # Licence files below the root come in two kinds. Those in the project's own tree can say that
    # part of the repository is under another licence, and they are weighed. Those in trees the
    # inventory already calls not-the-project — vendored code, sample corpora, example directories,
    # a virtualenv — are recorded, because dropping a licence file is how #86 began, but they
    # neither raise the review flag nor join a split: linguist's corpus carries a LICENSE.mysql
    # that is test data, and a maw's .venv carries one per installed package.
    for kind, paths in (
        ("nested-license", nested_license_files),
        ("vendored-license", vendored_license_files),
    ):
        for rel in paths:
            path = root / rel
            if not path.is_file():
                continue
            spdx, confidence = detect_from_text(read_text(path, limit=200_000))
            # A licence file the crab cannot read is still a licence file. Dropping it is how a
            # restricted subtree used to inherit the root's verdict without anyone being told.
            spdx = spdx or "NOASSERTION"
            if spdx == "NOASSERTION" or canonical_expression(spdx) != canonical_expression(
                findings.spdx
            ):
                findings.exceptions.append(
                    {"path": rel, "spdx": spdx, "confidence": confidence, "kind": kind}
                )

    _resolve_unclear(findings, split_mentions, root_declares_split=root_declares_split)

    cls = classify(findings.spdx)
    heavier = _heavier_than_root(findings)
    if heavier:
        where = "; ".join(f"{path} ({spdx}) {what}" for path, spdx, what in heavier[:5])
        findings.notes.append(
            f"{len(heavier)} nested licence file(s) the repository verdict does not answer for: "
            f"{where}. The verdict is the root's; a nutrient from those paths needs a human"
        )
    findings.human_review = (
        bool(findings.conflicts)
        or bool(heavier)
        or any(item.get("kind") == "header" for item in findings.exceptions)
        or cls
        in (
            LicenseClass.NONE,
            LicenseClass.UNKNOWN,
            LicenseClass.SOURCE_AVAILABLE,
        )
    )
    if findings.spdx and findings.spdx.startswith(("GPL-", "LGPL-", "AGPL-")):
        findings.notes.append("'-only' assumed unless headers say 'or later'")
    return findings


def _list_root(root: Path) -> list[str]:
    try:
        return [entry.name for entry in root.iterdir()]
    except OSError:
        return []


def _scan_headers(
    root: Path, code_files: Sequence[str], findings: LicenseFindings, limit: int
) -> None:
    sample = list(code_files)
    if len(sample) > limit:
        step = len(sample) / limit
        sample = [sample[int(i * step)] for i in range(limit)]
    counts: dict[str, int] = {}
    exceptions = 0
    for rel in sample:
        text = read_text(root / rel, limit=4000)
        ident = find_spdx_identifier(text)
        findings.headers_scanned += 1
        if not ident:
            continue
        counts[ident] = counts.get(ident, 0) + 1
        if findings.spdx and not _same_family(ident, findings.spdx) and exceptions < 50:
            findings.exceptions.append({"path": rel, "spdx": ident, "kind": "header"})
            exceptions += 1
    findings.header_counts = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    if exceptions:
        findings.notes.append(f"{exceptions} file headers declare a different license")
