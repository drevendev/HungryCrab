"""Attribution: what the crab actually carried into the maw, and the notice file that says so.

A ``COPY`` or ``COPY_FILE`` verdict promises two things: the material may travel, and its
source is recorded. The record is a *materialization receipt* — the trusted caller's statement,
made when it copied or adapted prey material into the maw, of which maw paths were taken from
which prey paths, at which commit, under which licence. ``crab serve --as pr-branch`` verifies
the receipt against the meal and against the prey's own history, appends it to
``.crab/attributions.json``, renders the maw's notice file from every receipt, and carries the
material, the receipts and the notice in one pull request. Nothing else produces a notice: an
issue that merely proposes a COPY nutrient has taken nothing, and a ledger entry names the prey
that *proposed* a nutrient, which is not always the one it was taken from (#70, #22).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .errors import CrabError
from .fetch.git import GitRunner
from .licensing.detect import find_spdx_identifier, is_license_file_name
from .licensing.matrix import LicenseClass, classify, normalize
from .mdutil import cell
from .miners.inventory import VENDORED_DIRS
from .nutrients import Candidate
from .pr_publication import GeneratedFile, PreparedPullRequest, nutrient_branch_name
from .pr_serve import read_maw_text
from .typeutil import as_dict, as_list

ATTRIBUTIONS_PATH = ".crab/attributions.json"
ATTRIBUTIONS_SCHEMA = "hungry-crab.attributions/1"
MATERIALIZATION_KIND = "materialization"
COPY_MODES = frozenset({"COPY", "COPY_FILE"})
NOTICE_WIDTH = 80
NOTICE_HEADING = "# Third-party notices"
_RECEIPT_VERSION = 1
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_TEXT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PLAIN_RE = re.compile(r"[A-Za-z0-9(][A-Za-z0-9 ._/+()-]*")
_LINK_RE = re.compile(r"https://[^\s<>`]+")
_ATOM_RE = re.compile(r"(?:`[^`]*`|\S)+")
_BLOCK_START_RE = re.compile(r"(?:[-+*>#=~]|<(?!https://)|\d+[.)]|_{3})")
_RECEIPT_KEYS = frozenset(
    {"version", "kind", "nutrient_id", "source", "taken", "summary", "checks"}
)
_SOURCE_KEYS = frozenset({"label", "url", "sha", "license"})
_TAKEN_KEYS = frozenset({"maw_path", "prey_path", "verbatim"})


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def _receipt_error(detail: str) -> CrabError:
    return CrabError(
        "invalid materialization receipt; refusing to attribute what it does not state",
        hint=detail,
    )


def _canonical(path: str) -> bool:
    """A relative POSIX path that can be written into a notice as it stands.

    A file name may hold a line break on the systems prey comes from, and two of them would end
    the paragraph the notice puts the path in, so control characters are refused with the rest.
    """
    parts = path.split("/")
    return not (
        not path
        or path.startswith("/")
        or "\\" in path
        or _CONTROL_RE.search(path) is not None
        or any(not part or part in {".", ".."} for part in parts)
        or ":" in parts[0]
    )


def _strict_object(payload: str) -> dict[str, object]:
    """One JSON object with no duplicate members; anything else is a malformed receipt."""

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError(f"duplicate JSON object member: {key}")
            parsed[key] = value
        return parsed

    try:
        raw: object = json.loads(payload, object_pairs_hook=reject_duplicates)
    except ValueError as exc:
        detail = str(exc) if str(exc).startswith("duplicate") else "receipt must be valid JSON"
        raise _receipt_error(detail) from exc
    if not isinstance(raw, dict):
        raise _receipt_error("receipt root must be an object")
    return raw


def receipt_kind(payload: str) -> str:
    """``materialization`` for a COPY receipt, ``cleanroom`` for everything else.

    The kind is read leniently: a document that is not even an object is left to the strict
    parser of the kind the card expects, which reports what is wrong with it.
    """
    try:
        raw: object = json.loads(payload)
    except ValueError:
        return "cleanroom"
    if isinstance(raw, dict) and raw.get("kind") == MATERIALIZATION_KIND:
        return MATERIALIZATION_KIND
    return "cleanroom"


@dataclass(frozen=True)
class SourceRef:
    """The prey a receipt names: its label, URL, commit and licence, as the meal knew them."""

    label: str
    url: str | None
    sha: str
    license: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "url": self.url, "sha": self.sha, "license": self.license}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceRef:
        url = data.get("url")
        spdx = data.get("license")
        return cls(
            label=str(data.get("label", "")),
            url=url if isinstance(url, str) and url else None,
            sha=str(data.get("sha", "")),
            license=spdx if isinstance(spdx, str) and spdx else None,
        )


@dataclass(frozen=True)
class TakenFile:
    """One maw path and the prey path it was taken from; ``verbatim`` means byte for byte."""

    maw_path: str
    prey_path: str
    verbatim: bool

    def to_dict(self) -> dict[str, Any]:
        return {"maw_path": self.maw_path, "prey_path": self.prey_path, "verbatim": self.verbatim}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TakenFile:
        return cls(
            maw_path=str(data.get("maw_path", "")),
            prey_path=str(data.get("prey_path", "")),
            verbatim=data.get("verbatim") is True,
        )


@dataclass(frozen=True)
class CarriedText:
    """A licence or NOTICE file of the source, as it stood at the commit the material came from.

    A notice that only *names* an obligation discharges none: MIT, BSD, ISC and Boost ask for
    the copyright and permission notice itself to travel with the copy, and Apache for the
    licence and the NOTICE file's attributions. The text is kept with the receipt so that
    ``crab attribution`` can render it without the prey at hand.
    """

    path: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "text": self.text}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CarriedText:
        return cls(path=str(data.get("path", "")), text=str(data.get("text", "")))


@dataclass(frozen=True)
class MaterializationReceipt:
    """The trusted caller's statement of what it took, parsed strictly."""

    nutrient_id: str
    source: SourceRef
    taken: tuple[TakenFile, ...]
    summary: str
    checks: tuple[str, ...]


def load_materialization_receipt(payload: str) -> MaterializationReceipt:
    """Parse one strict materialization receipt; every field is required and typed.

    Like the clean-room receipt, this is the only producer-side statement of which maw files
    belong to the nutrient. Unknown members, a missing source, a non-canonical path, a duplicate
    maw path or a bad commit fail closed rather than being guessed around.
    """

    raw = _strict_object(payload)
    if set(raw) != _RECEIPT_KEYS:
        raise _receipt_error(
            "receipt must contain only version, kind, nutrient_id, source, taken, summary and "
            "checks"
        )
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != _RECEIPT_VERSION:
        raise _receipt_error(f"unsupported receipt version: {version!r}")
    if raw["kind"] != MATERIALIZATION_KIND:
        raise _receipt_error(f"kind must be {MATERIALIZATION_KIND!r}")
    nutrient_id = raw["nutrient_id"]
    if not isinstance(nutrient_id, str) or not nutrient_id.startswith("crab:"):
        raise _receipt_error("nutrient_id must be a crab nutrient id")

    source_raw = raw["source"]
    if not isinstance(source_raw, dict) or set(source_raw) != _SOURCE_KEYS:
        raise _receipt_error("source must contain exactly label, url, sha and license")
    label = source_raw["label"]
    if not isinstance(label, str) or not label.strip():
        raise _receipt_error("source.label must name the prey")
    sha = source_raw["sha"]
    if not isinstance(sha, str) or _SHA_RE.fullmatch(sha) is None:
        raise _receipt_error("source.sha must be a commit hash of 7 to 40 hex digits")
    url = source_raw["url"]
    if url is not None and (not isinstance(url, str) or not url.startswith("https://")):
        raise _receipt_error("source.url must be an https URL or null")
    spdx = source_raw["license"]
    if spdx is not None and (not isinstance(spdx, str) or not spdx.strip()):
        raise _receipt_error("source.license must be an SPDX expression or null")

    taken_raw = raw["taken"]
    if not isinstance(taken_raw, list) or not taken_raw:
        raise _receipt_error("receipt must declare at least one taken file")
    taken: list[TakenFile] = []
    seen: set[str] = set()
    for item in taken_raw:
        if not isinstance(item, dict) or set(item) != _TAKEN_KEYS:
            raise _receipt_error(
                "each taken file must contain exactly maw_path, prey_path and verbatim"
            )
        maw_path, prey_path, verbatim = item["maw_path"], item["prey_path"], item["verbatim"]
        if not isinstance(maw_path, str) or not _canonical(maw_path):
            raise _receipt_error("maw_path must be a canonical maw-relative POSIX path")
        if not isinstance(prey_path, str) or not _canonical(prey_path):
            raise _receipt_error("prey_path must be a canonical prey-relative POSIX path")
        if not isinstance(verbatim, bool):
            raise _receipt_error("verbatim must be true or false")
        if maw_path in seen:
            raise _receipt_error(f"duplicate maw path: {maw_path}")
        seen.add(maw_path)
        taken.append(TakenFile(maw_path=maw_path, prey_path=prey_path, verbatim=verbatim))

    summary = raw["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise _receipt_error("summary must say what was taken and how it was adapted")
    checks = raw["checks"]
    if (
        not isinstance(checks, list)
        or not checks
        or any(not isinstance(check, str) or not check.strip() for check in checks)
    ):
        raise _receipt_error("checks must be a non-empty list of command strings")

    return MaterializationReceipt(
        nutrient_id=nutrient_id,
        source=SourceRef(label=label.strip(), url=url, sha=sha, license=spdx),
        taken=tuple(taken),
        summary=summary.strip(),
        checks=tuple(checks),
    )


@dataclass(frozen=True)
class Obligation:
    """What the source's licence asks of the maw for the material taken, in one line."""

    kind: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Obligation:
        return cls(kind=str(data.get("kind", "review")), text=str(data.get("text", "")))


def obligation_for(spdx: str | None, verdict: Mapping[str, Any]) -> Obligation:
    """The structured obligation behind a COPY verdict.

    Apache's NOTICE obligation is not a copyright line, a CC-BY credit is not a NOTICE file,
    and one's own code owes nobody a notice; the verdict's reason says which case this is when
    the licence alone cannot (the ``own`` relationship is not stored on the card).
    """

    reason = str(verdict.get("reason", ""))
    if reason.startswith("same owner:"):
        return Obligation("none", "same owner: no third-party notice is owed for one's own code")
    if reason.startswith(("same owner, but", "license checks bypassed")):
        return Obligation(
            "review", "the verdict asked for human review: the notice decision is a person's"
        )
    cls = classify(normalize(spdx))
    if cls is LicenseClass.PERMISSIVE:
        return Obligation(
            "copyright-notice",
            "the source's copyright and permission notice travel with the material; its licence "
            "file is reproduced below",
        )
    if cls is LicenseClass.PERMISSIVE_NOTICE:
        return Obligation(
            "notice-file",
            "Apache-2.0: the licence and the attribution notices of the source's NOTICE file "
            "travel with the material (both reproduced below), and a modified file says that it "
            "was changed",
        )
    if cls is LicenseClass.DOCS_ATTRIBUTION:
        return Obligation("attribution", "CC-BY: credit the source and link its licence")
    if cls in (LicenseClass.DOCS_SHARE_ALIKE, LicenseClass.FILE_COPYLEFT):
        return Obligation(
            "file-license", "each copied file keeps its own licence header and stays under it"
        )
    return Obligation(
        "review", f"no obligation rule for {spdx or 'an unlicensed source'}: a person decides"
    )


@dataclass(frozen=True)
class AttributionRecord:
    """One confirmed materialization: the receipt plus what the crab added when it published."""

    nutrient_id: str
    source: SourceRef
    taken: tuple[TakenFile, ...]
    mode: str
    obligation: Obligation
    summary: str
    branch: str
    recorded_at: str
    texts: tuple[CarriedText, ...] = ()

    @property
    def identity(self) -> tuple[str, str]:
        return (self.nutrient_id, self.source.sha)

    @property
    def material(self) -> tuple[Any, ...]:
        """What must not silently change under an existing identity.

        The carried texts are not part of it: they are the source's files at that commit, which
        the identity already fixes.
        """
        return (self.mode, self.obligation.kind, tuple(sorted(self.taken, key=_taken_key)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "nutrient_id": self.nutrient_id,
            "source": self.source.to_dict(),
            "taken": [item.to_dict() for item in self.taken],
            "mode": self.mode,
            "obligation": self.obligation.to_dict(),
            "summary": self.summary,
            "branch": self.branch,
            "recorded_at": self.recorded_at,
            "texts": [item.to_dict() for item in self.texts],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AttributionRecord:
        return cls(
            nutrient_id=str(data.get("nutrient_id", "")),
            source=SourceRef.from_dict(as_dict(data.get("source"))),
            taken=tuple(
                TakenFile.from_dict(as_dict(item))
                for item in as_list(data.get("taken"))
                if isinstance(item, dict)
            ),
            mode=str(data.get("mode", "")),
            obligation=Obligation.from_dict(as_dict(data.get("obligation"))),
            summary=str(data.get("summary", "")),
            branch=str(data.get("branch", "")),
            recorded_at=str(data.get("recorded_at", "")),
            texts=tuple(
                CarriedText.from_dict(as_dict(item))
                for item in as_list(data.get("texts"))
                if isinstance(item, dict)
            ),
        )


def _taken_key(item: TakenFile) -> tuple[str, str, bool]:
    return (item.maw_path, item.prey_path, item.verbatim)


def _record_key(record: AttributionRecord) -> tuple[str, str, str]:
    return (record.source.label.lower(), record.source.sha, record.nutrient_id)


def parse_attributions(text: str) -> list[AttributionRecord]:
    try:
        data = as_dict(json.loads(text))
    except ValueError as exc:
        raise CrabError(f"{ATTRIBUTIONS_PATH} is not valid JSON: {exc}") from exc
    if data.get("schema") != ATTRIBUTIONS_SCHEMA:
        raise CrabError(
            f"{ATTRIBUTIONS_PATH} has an unknown schema",
            hint=f"expected {ATTRIBUTIONS_SCHEMA}, got {data.get('schema')!r}",
        )
    records = [
        AttributionRecord.from_dict(as_dict(item))
        for item in as_list(data.get("receipts"))
        if isinstance(item, dict)
    ]
    for record in records:
        if not record.nutrient_id or not record.source.sha or not record.taken:
            raise CrabError(
                f"{ATTRIBUTIONS_PATH} holds a receipt without a nutrient, a commit or files",
                hint="every receipt names what was taken from which commit",
            )
    return sorted(records, key=_record_key)


def load_attributions(maw_root: Path) -> list[AttributionRecord]:
    """The maw's confirmed receipts; a maw that has taken nothing has no file."""

    path = maw_root / ATTRIBUTIONS_PATH
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CrabError(f"cannot read {ATTRIBUTIONS_PATH}: {exc}") from exc
    return parse_attributions(text)


def dump_attributions(records: Iterable[AttributionRecord]) -> str:
    ordered = sorted(records, key=_record_key)
    return (
        json.dumps(
            {"schema": ATTRIBUTIONS_SCHEMA, "receipts": [item.to_dict() for item in ordered]},
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def add_attribution(
    records: Sequence[AttributionRecord], record: AttributionRecord
) -> list[AttributionRecord]:
    """Append a receipt, or keep the one already recorded under the same identity.

    Identity is the nutrient and the source commit. A retry after a crash, and a rerun that
    reconciles an existing pull request, carry the same receipt and must not duplicate it; the
    same nutrient taken later from another prey or another commit is a second receipt, and the
    first is never rewritten. The same identity with *different* material is refused: that is
    a receipt contradicting a record, and a person has to say which one is true. The one thing
    a later sighting adds to a record is the source's licence texts, where a record written
    before they were carried has none.
    """

    for index, existing in enumerate(records):
        if existing.identity != record.identity:
            continue
        if existing.material != record.material:
            raise CrabError(
                f"an attribution receipt for {record.nutrient_id} from "
                f"{record.source.label}@{record.source.sha[:7]} already exists with different "
                "material",
                hint=f"edit or remove the recorded receipt in {ATTRIBUTIONS_PATH} first",
            )
        if record.texts and not existing.texts:
            return [*records[:index], replace(existing, texts=record.texts), *records[index + 1 :]]
        return list(records)
    return [*records, record]


def _code(text: str) -> str:
    """An inline code span around whatever a path holds, backticks included."""

    runs = {len(run) for run in re.findall(r"`+", text)}
    fence = "`" * next(count for count in range(1, len(text) + 2) if count not in runs)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _inline(text: str) -> str:
    """A name the source gave itself: as it is when it is plain, a code span when it is not."""

    flat = " ".join(text.split())
    return flat if _PLAIN_RE.fullmatch(flat) else _code(flat)


def _atoms(text: str) -> list[str]:
    """Prose as the pieces a line may break between: words, and code spans kept whole."""

    return _ATOM_RE.findall(text)


def _wrapped(atoms: Iterable[str]) -> list[str]:
    """One paragraph, broken between atoms only, so a code span or a link is never split.

    An atom longer than the width gets a line of its own, which no line-length rule objects to
    because nothing on it can be broken. An atom that would read as a list marker, a quote, a
    heading or a rule at the start of a line never starts one.
    """

    lines: list[str] = []
    current = ""
    for atom in atoms:
        if not current:
            current = atom
        elif len(current) + 1 + len(atom) <= NOTICE_WIDTH or _BLOCK_START_RE.match(atom):
            current = f"{current} {atom}"
        else:
            lines.append(current)
            current = atom
    if current:
        lines.append(current)
    return lines


def _sentence(text: str) -> str:
    flat = " ".join(text.split())
    return flat if flat.endswith((".", "!", "?")) else f"{flat}."


def _taken_sentence(record: AttributionRecord, item: TakenFile) -> list[str]:
    how = "copied unchanged from" if item.verbatim else "adapted from"
    return [
        f"{_code(item.maw_path)}:",
        *how.split(),
        f"{_code(item.prey_path)};",
        f"{_inline(record.mode)},",
        f"{_code(record.nutrient_id)}.",
    ]


def clean_text(text: str) -> str:
    """A carried text as the notice prints it: LF line ends, no control characters but tabs, no
    trailing whitespace, no blank lines around it. Nothing a reader of the licence would see
    changes, and nothing a formatter would rewrite remains."""

    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_TEXT_CONTROL_RE.sub("", line).rstrip() for line in unified.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _carried(records: Sequence[AttributionRecord], commit: str) -> list[str]:
    """Each licence and NOTICE text of one source commit, once, in a fence of its own.

    A formatter leaves the inside of a fenced block alone, so the text keeps its own line
    breaks; the fence is longer than any run of backticks in it, so no line of it can close
    the block early.
    """

    lines: list[str] = []
    seen: set[str] = set()
    for record in records:
        for item in record.texts:
            if item.path in seen:
                continue
            seen.add(item.path)
            text = clean_text(item.text)
            longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
            fence = "`" * max(3, longest + 1)
            said = [_code(item.path), "of", "the", "source", "at", f"{commit}:"]
            lines.extend([*_wrapped(said), "", f"{fence}text", *text.split("\n"), fence, ""])
    return lines


def render_notices(records: Iterable[AttributionRecord]) -> str:
    """The notice file: one section per source commit, one sentence per file taken, byte-stable.

    The layout is headings and paragraphs wrapped at ``NOTICE_WIDTH``, with no table, no list,
    no hard line break and no trailing whitespace. A maw's formatter runs over this file like
    over any other, and what it rewrites `crab attribution --check` reports as stale; a table is
    realigned by every Markdown formatter, a paragraph is left alone by their defaults. Each
    section ends with the source's licence and NOTICE texts, which is what the obligation it
    names is discharged with, in fenced blocks a formatter does not touch.
    """

    ordered = sorted(records, key=_record_key)
    intro = (
        f"Written by `crab attribution` from `{ATTRIBUTIONS_PATH}`, the receipts of material "
        "Hungry Crab carried into this repository. Edit the receipts, not this file; rerunning "
        "the command reproduces it byte for byte. It is headings and paragraphs on purpose: a "
        "Markdown formatter has nothing to rewrite here."
    )
    lines = [NOTICE_HEADING, "", *_wrapped(_atoms(intro)), ""]
    if not ordered:
        lines.extend(["Nothing has been carried over yet.", ""])
        return "\n".join(lines)

    groups: list[list[AttributionRecord]] = []
    for record in ordered:
        key = (record.source.label, record.source.sha)
        if groups and (groups[-1][0].source.label, groups[-1][0].source.sha) == key:
            groups[-1].append(record)
        else:
            groups.append([record])
    for members in groups:
        source = members[0].source
        spdx = _inline(source.license) if source.license else "no licence declared"
        commit = _inline(source.sha[:7])
        lines.extend([f"## {_inline(source.label)} @ {commit} — {spdx}", ""])
        if source.url:
            tree = f"{source.url}/tree/{source.sha}"
            link = f"<{tree}>" if _LINK_RE.fullmatch(tree) else _code(tree)
            lines.extend([*_wrapped(["Source:", link]), ""])
        obligation: Obligation | None = None
        for record in members:
            if record.obligation != obligation:
                obligation = record.obligation
                owed = f"Obligation ({obligation.kind}): {_sentence(obligation.text)}"
                lines.extend([*_wrapped(_atoms(owed)), ""])
            for item in sorted(record.taken, key=_taken_key):
                lines.extend([*_wrapped(_taken_sentence(record, item)), ""])
        lines.extend(_carried(members, commit))
    return "\n".join(lines)


class SourceReader(Protocol):
    """Read-only access to the prey at one commit; the crab never runs anything there."""

    def exists(self, sha: str, path: str) -> bool: ...

    def read(self, sha: str, path: str) -> str | None: ...

    def entries(self, sha: str, directory: str = "") -> dict[str, str] | None:
        """The files (not directories) in one directory of the prey at ``sha``, its root by
        default, by name, with their git mode: ``100644`` or ``100755`` for a file, ``120000``
        for a symbolic link, ``160000`` for a submodule; ``None`` when there is no such
        directory."""
        ...


class GitSourceReader:
    """The prey's cached clone, read through git plumbing only."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.git = GitRunner(repo)

    # A path after `<sha>:` resolves from the top of the repository; `./` resolves it from the
    # directory the reader was opened at, which is the prey when the prey is a subdirectory.
    def exists(self, sha: str, path: str) -> bool:
        return self.git.ok("cat-file", "-e", f"{sha}:./{path}")

    def read(self, sha: str, path: str) -> str | None:
        return self.git.try_run("show", f"{sha}:./{path}")

    def entries(self, sha: str, directory: str = "") -> dict[str, str] | None:
        # `<sha>:./` is the tree of the directory the clone was opened at, which is the prey's
        # root when the prey is a subdirectory of a larger repository; `--full-tree` keeps
        # ls-tree from filtering that tree's entries by the same directory a second time.
        listing = self.git.try_run("ls-tree", "--full-tree", "-z", f"{sha}:./{directory}")
        if listing is None:
            return None
        files: dict[str, str] = {}
        for entry in listing.split("\0"):
            meta, _, name = entry.partition("\t")
            mode, kind = [*meta.split(" "), "", ""][:2]
            if name and kind in ("blob", "commit"):
                files[name] = mode
        return files


# A licence obligation that asks for the source's own notice to travel with the material.
_NOTICE_OWED = frozenset({"copyright-notice", "notice-file", "attribution"})
_SOURCE_NOTICE_RE = re.compile(r"NOTICE(?:\.[A-Za-z0-9]+)?", re.IGNORECASE)
_MAX_CARRIED = 100_000
# A regular file, executable or not; a link (120000) or a submodule (160000) is not material.
_FILE_MODES = frozenset({"100644", "100755"})


def carried_texts(sha: str, reader: SourceReader) -> tuple[CarriedText, ...]:
    """The licence and NOTICE files at the root of the source at ``sha``, in name order."""

    names = reader.entries(sha)
    if names is None:
        raise CrabError(
            "cannot list the prey's files at the commit the material came from",
            hint=f"{sha[:7]} is not in the prey's clone; catch it again without --shallow",
        )
    texts: list[CarriedText] = []
    for name in sorted(names):
        if not (is_license_file_name(name) or _SOURCE_NOTICE_RE.fullmatch(name)):
            continue
        if names[name] not in _FILE_MODES:
            # A link's blob is the path it points at; carried as text it would be a notice that
            # reproduces a file name, and it would satisfy the refusal below by its name alone.
            raise CrabError(
                "a licence file of the prey is a symbolic link, which the crab does not follow",
                hint=f"{name} at {sha[:7]} points at another file; carry the notice by hand",
            )
        text = reader.read(sha, name)
        if text is None or len(text) > _MAX_CARRIED:
            raise CrabError(
                "cannot carry a licence file of the prey",
                hint=f"{name} at {sha[:7]} is unreadable or longer than {_MAX_CARRIED} characters",
            )
        texts.append(CarriedText(name, clean_text(text)))
    return tuple(texts)


def check_taken_licences(receipt: MaterializationReceipt, reader: SourceReader) -> None:
    """Every taken file answers to the source's licence, and not to one of its own.

    The verdict, the obligation and the carried texts are the repository's. A file in vendored
    code, under a directory that holds a licence file of its own, or with a licence header that
    names another licence is someone else's material inside the prey, and the repository's
    verdict does not answer for it: a person decides, outside the crab.
    """

    sha = receipt.source.sha
    for item in receipt.taken:
        parts = item.prey_path.split("/")
        vendored = next((part for part in parts[:-1] if part in VENDORED_DIRS), None)
        if vendored is not None:
            raise CrabError(
                "a taken file lives in vendored code, which the source's licence does not cover",
                hint=f"{item.prey_path} is under {vendored}/; take it from its own upstream",
            )
        listed: dict[str, str] = {}
        for depth in range(len(parts)):
            directory = "/".join(parts[:depth])
            names = reader.entries(sha, directory)
            if names is None:
                raise CrabError(
                    "cannot list a directory of the prey at the commit the material came from",
                    hint=f"{directory or 'the root'} at {sha[:7]}",
                )
            listed = names
            if not directory:
                continue  # the root's licence is the verdict's own
            own = next((name for name in sorted(names) if is_license_file_name(name)), None)
            if own is not None:
                raise CrabError(
                    "a taken file lives under a licence of its own, which the verdict does not "
                    "answer for",
                    hint=f"{item.prey_path} is under {directory}/{own}; a person decides",
                )
        if listed.get(parts[-1]) not in _FILE_MODES:
            # A directory, a link or a submodule is not material: git would hand back a tree
            # listing, the path a link points at, or nothing, and any of them would pass as
            # the source of an adapted file.
            raise CrabError(
                "a taken prey path is not a regular file at that commit",
                hint=f"{item.prey_path} is a directory, a link or a submodule; take a file",
            )
        declared = find_spdx_identifier((reader.read(sha, item.prey_path) or "")[:4000])
        if declared and normalize(declared) != normalize(receipt.source.license):
            raise CrabError(
                "a taken file declares another licence than the source's",
                hint=(
                    f"{item.prey_path} says SPDX-License-Identifier: {declared}, the source is "
                    f"{receipt.source.license or 'unlicensed'}; a person decides"
                ),
            )


def checked_attribution_file(maw_root: Path, name: str) -> str:
    """The notice path `.crab.yml` names, refused unless the crab may write it.

    A plain relative path inside the maw, not the receipts file, not under `.git`, and not a
    file somebody else wrote: a notice path of `README.md` would replace the README with the
    notice in the next pull request.
    """

    parts = name.split("/")
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or _CONTROL_RE.search(name)
        or any(part in ("", ".", "..") for part in parts)
        or any(part.casefold() == ".git" for part in parts)
        or name == ATTRIBUTIONS_PATH
    ):
        raise CrabError(
            f"attribution_file {name!r} in .crab.yml is not a plain path inside the maw",
            hint="name a relative path such as THIRD_PARTY_NOTICES.md, without ./ or ..",
        )
    target = maw_root / name
    root = maw_root.resolve()
    if not target.resolve().is_relative_to(root):
        raise CrabError(
            f"attribution_file {name!r} leads outside the maw", hint="a link must not carry it out"
        )
    if target.exists():
        try:
            head = target.read_text(encoding="utf-8")[:200]
        except (OSError, UnicodeError) as exc:
            raise CrabError(f"cannot read {name}: {exc}") from exc
        if not head.startswith(NOTICE_HEADING):
            raise CrabError(
                f"attribution_file {name!r} is a file the crab did not write",
                hint="name another file in .crab.yml; the crab overwrites only its own notice",
            )
    return name


def base_receipts_kept(
    prepared: PreparedPullRequest,
) -> Callable[[Callable[[str], str | None]], None]:
    """A check, for the moment the default branch is fetched, that no receipt on it is dropped.

    The receipts a pull request carries are the maw's working tree plus the new one, and the
    branch is built on the default branch as fetched at publication. A checkout behind that
    branch would replace receipts merged since with its older file, and nothing would conflict:
    `crab attribution --check` stays green while a merged file is attributed nowhere.
    """

    ours = next((item.content for item in prepared.files if item.path == ATTRIBUTIONS_PATH), None)

    def check(read_base: Callable[[str], str | None]) -> None:
        base = read_base(ATTRIBUTIONS_PATH)
        if base is None or ours is None:
            return
        kept = {record.identity for record in parse_attributions(ours)}
        dropped = [record for record in parse_attributions(base) if record.identity not in kept]
        if dropped:
            names = ", ".join(f"{r.nutrient_id}@{r.source.sha[:7]}" for r in dropped[:5])
            raise CrabError(
                "this pull request would drop receipts the default branch already holds",
                hint=f"the maw's checkout is behind; pull, then serve again ({names})",
            )

    return check


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n")


def verify_sources(
    receipt: MaterializationReceipt, reader: SourceReader, maw_files: Mapping[str, str]
) -> None:
    """Every prey path the receipt names exists at that commit; a verbatim copy matches it."""

    for item in receipt.taken:
        if not reader.exists(receipt.source.sha, item.prey_path):
            raise CrabError(
                "materialization receipt names a source path the prey does not have",
                hint=(
                    f"{item.prey_path} is not in {receipt.source.label} at {receipt.source.sha[:7]}"
                ),
            )
        if not item.verbatim:
            continue
        original = reader.read(receipt.source.sha, item.prey_path)
        if original is None:
            raise CrabError(
                "cannot read the source of a verbatim copy from the prey",
                hint=f"{item.prey_path} at {receipt.source.sha[:7]}",
            )
        if _normalized(original) != _normalized(maw_files[item.maw_path]):
            raise CrabError(
                "a file declared as a verbatim copy differs from its source",
                hint=(
                    f"{item.maw_path} is not byte for byte {item.prey_path} at "
                    f"{receipt.source.sha[:7]}; declare it adapted, or copy it unchanged"
                ),
            )


def _check_against_meal(
    card: Candidate,
    receipt: MaterializationReceipt,
    menu: Mapping[str, Any],
    attribution_file: str,
) -> None:
    if card.license_mode not in COPY_MODES:
        raise CrabError(
            f"license mode {card.license_mode} does not materialize prey files",
            hint="COPY and COPY_FILE carry files with attribution; REIMPLEMENT uses the clean room",
        )
    if receipt.nutrient_id != card.id:
        raise CrabError(
            "materialization receipt nutrient does not match the selected nutrient",
            hint=f"expected {card.id}, got {receipt.nutrient_id}",
        )
    for item in receipt.taken:
        if item.maw_path in (ATTRIBUTIONS_PATH, attribution_file):
            raise CrabError(
                "a materialization receipt may not take over the attribution files",
                hint=f"{item.maw_path} is written by the crab, not taken from the prey",
            )
    prey = as_dict(menu.get("prey"))
    expected = SourceRef.from_dict(prey)
    mismatches = [
        name
        for name, got, want in (
            ("label", receipt.source.label, expected.label),
            ("sha", receipt.source.sha, expected.sha),
            ("url", receipt.source.url, expected.url),
            ("license", receipt.source.license, expected.license),
        )
        if got != want
    ]
    if mismatches:
        raise CrabError(
            "materialization receipt names a different source than the meal",
            hint=(
                f"{', '.join(mismatches)} differ; the meal was eaten from "
                f"{expected.label}@{expected.sha[:7]} ({expected.license or 'no licence'})"
            ),
        )
    if card.license_mode == "COPY_FILE" and any(not item.verbatim for item in receipt.taken):
        raise CrabError(
            "COPY_FILE carries whole files that keep their own licence",
            hint="every taken file must be verbatim under COPY_FILE",
        )


def _attribution_section(record: AttributionRecord, attribution_file: str) -> str:
    source = record.source
    spdx = _inline(source.license) if source.license else "no licence declared"
    taken_from = (
        f"Taken from {_code(f'{source.label}@{source.sha[:7]}')} "
        f"({spdx}, mode {_inline(record.mode)}): {_sentence(record.obligation.text)}"
    )
    lines = ["## Attribution", "", taken_from, "", "| Into | From | |", "|---|---|---|"]
    for item in sorted(record.taken, key=_taken_key):
        how = "verbatim" if item.verbatim else "adapted"
        lines.append(f"| {cell(_code(item.maw_path))} | {cell(_code(item.prey_path))} | {how} |")
    carried = (
        f"The receipt is recorded in `{ATTRIBUTIONS_PATH}` and rendered into "
        f"`{attribution_file}`; both are carried in this pull request. The crab wrote them "
        "when it published, so no check that ran in the working tree has seen them: this "
        "repository's own gate sees them when it runs on this branch."
    )
    if record.texts:
        names = ", ".join(_code(item.path) for item in record.texts)
        carried += f" The notice reproduces the source's {names} as they stood at that commit."
    lines.extend(["", record.summary, "", carried])
    return "\n".join(lines)


def prepare_copy_pull_request(
    card: Candidate,
    menu: Mapping[str, Any],
    receipt_payload: str,
    maw_root: Path,
    *,
    title: str,
    body: str,
    attribution_file: str,
    source_reader: SourceReader,
    now: datetime | None = None,
) -> PreparedPullRequest:
    """Freeze one COPY nutrient's files, its receipt and the notice file into an immutable payload.

    Order of proof: the receipt is parsed strictly, checked against the card and the meal's prey,
    the notice path is checked as one the crab may write, its maw files are read under the maw's
    containment rule, its prey paths are checked against the prey's own history (a verbatim copy
    byte for byte) and against licences of their own (vendored code, a nested licence file, a
    header naming another licence), the source's licence and NOTICE files are read at that
    commit — a licence that asks for its notice to travel and a prey that has none to carry is
    refused — the receipt is appended to the maw's attributions under the identity rule, and the
    notice file is rendered from all of them. Only then is there a payload; the transaction
    scans it before any provider effect, and the effect refuses a branch that would drop a
    receipt the default branch already holds.
    """

    receipt = load_materialization_receipt(receipt_payload)
    _check_against_meal(card, receipt, menu, attribution_file)
    checked_attribution_file(maw_root, attribution_file)
    maw_files: dict[str, str] = {}
    for item in receipt.taken:
        try:
            maw_files[item.maw_path] = read_maw_text(maw_root, item.maw_path)
        except CrabError as exc:
            raise CrabError(
                "a file the materialization receipt declares cannot be published from the maw",
                hint=exc.hint,
            ) from exc
    verify_sources(receipt, source_reader, maw_files)
    check_taken_licences(receipt, source_reader)

    obligation = obligation_for(receipt.source.license, as_dict(menu.get("verdict")))
    texts: tuple[CarriedText, ...] = ()
    if obligation.kind != "none":
        texts = carried_texts(receipt.source.sha, source_reader)
    if obligation.kind in _NOTICE_OWED and not any(is_license_file_name(t.path) for t in texts):
        raise CrabError(
            "the source's licence asks for its notice to travel with the material, and the "
            "prey has no licence file to carry",
            hint=(
                f"{receipt.source.label} at {receipt.source.sha[:7]} has no licence file at its "
                "root; serve the nutrient as an issue, or carry the notice by hand"
            ),
        )
    record = AttributionRecord(
        nutrient_id=card.id,
        source=receipt.source,
        taken=receipt.taken,
        mode=card.license_mode,
        obligation=obligation,
        summary=receipt.summary,
        branch=nutrient_branch_name(card.id),
        recorded_at=_stamp(now),
        texts=texts,
    )
    records = add_attribution(load_attributions(maw_root), record)
    recorded = next(item for item in records if item.identity == record.identity)

    files = [GeneratedFile(path=path, content=content) for path, content in maw_files.items()]
    files.append(GeneratedFile(path=ATTRIBUTIONS_PATH, content=dump_attributions(records)))
    files.append(GeneratedFile(path=attribution_file, content=render_notices(records)))

    # Always the crab's own section, last: a note or an evidence path that happens to contain
    # the heading must not be what the reader takes for the attribution.
    rendered_body = body.rstrip() + "\n\n" + _attribution_section(recorded, attribution_file)
    return PreparedPullRequest(title=title, body=rendered_body + "\n", files=tuple(files))
