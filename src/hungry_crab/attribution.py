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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .errors import CrabError
from .fetch.git import GitRunner
from .licensing.matrix import LicenseClass, classify, normalize
from .nutrients import Candidate
from .pr_publication import GeneratedFile, PreparedPullRequest, nutrient_branch_name
from .pr_serve import read_maw_text
from .typeutil import as_dict, as_list

ATTRIBUTIONS_PATH = ".crab/attributions.json"
ATTRIBUTIONS_SCHEMA = "hungry-crab.attributions/1"
MATERIALIZATION_KIND = "materialization"
COPY_MODES = frozenset({"COPY", "COPY_FILE"})
_RECEIPT_VERSION = 1
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")
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
    parts = path.split("/")
    return not (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
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
            "keep the source's copyright and permission notice with the material",
        )
    if cls is LicenseClass.PERMISSIVE_NOTICE:
        return Obligation(
            "notice-file",
            "Apache-2.0: carry the attribution notices of the source's NOTICE file, if it has one",
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

    @property
    def identity(self) -> tuple[str, str]:
        return (self.nutrient_id, self.source.sha)

    @property
    def material(self) -> tuple[Any, ...]:
        """What must not silently change under an existing identity."""
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
    a receipt contradicting a record, and a person has to say which one is true.
    """

    for existing in records:
        if existing.identity != record.identity:
            continue
        if existing.material != record.material:
            raise CrabError(
                f"an attribution receipt for {record.nutrient_id} from "
                f"{record.source.label}@{record.source.sha[:7]} already exists with different "
                "material",
                hint=f"edit or remove the recorded receipt in {ATTRIBUTIONS_PATH} first",
            )
        return list(records)
    return [*records, record]


def render_notices(records: Iterable[AttributionRecord]) -> str:
    """The notice file: one section per source commit, one row per file taken, byte-stable."""

    ordered = sorted(records, key=_record_key)
    lines = [
        "# Third-party notices",
        "",
        f"Written by `crab attribution` from `{ATTRIBUTIONS_PATH}`, the receipts of material",
        "Hungry Crab carried into this repository. Edit the receipts, not this file; rerunning",
        "the command reproduces it byte for byte.",
        "",
    ]
    if not ordered:
        lines.extend(["Nothing has been carried over yet.", ""])
        return "\n".join(lines)

    current: tuple[str, str] | None = None
    for record in ordered:
        source = record.source
        key = (source.label, source.sha)
        if key != current:
            if current is not None:
                lines.append("")
            current = key
            spdx = source.license or "no licence declared"
            lines.append(f"## {source.label} @ {source.sha[:7]} — {spdx}")
            lines.append("")
            if source.url:
                lines.append(f"Source: <{source.url}/tree/{source.sha}>  ")
            lines.append(f"Obligation ({record.obligation.kind}): {record.obligation.text}")
            lines.append("")
            lines.append("| Nutrient | Into | From | Mode |")
            lines.append("|---|---|---|---|")
        for item in sorted(record.taken, key=_taken_key):
            origin = f"`{item.prey_path}`" + ("" if item.verbatim else " (adapted)")
            lines.append(
                f"| `{record.nutrient_id}` | `{item.maw_path}` | {origin} | {record.mode} |"
            )
    lines.append("")
    return "\n".join(lines)


class SourceReader(Protocol):
    """Read-only access to the prey at one commit; the crab never runs anything there."""

    def exists(self, sha: str, path: str) -> bool: ...

    def read(self, sha: str, path: str) -> str | None: ...


class GitSourceReader:
    """The prey's cached clone, read through git plumbing only."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.git = GitRunner(repo)

    def exists(self, sha: str, path: str) -> bool:
        return self.git.ok("cat-file", "-e", f"{sha}:{path}")

    def read(self, sha: str, path: str) -> str | None:
        return self.git.try_run("show", f"{sha}:{path}")


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
    taken_from = (
        f"Taken from `{source.label}@{source.sha[:7]}` "
        f"({source.license or 'no licence declared'}, mode {record.mode}): "
        f"{record.obligation.text}."
    )
    lines = ["## Attribution", "", taken_from, "", "| Into | From | |", "|---|---|---|"]
    for item in sorted(record.taken, key=_taken_key):
        how = "verbatim" if item.verbatim else "adapted"
        lines.append(f"| `{item.maw_path}` | `{item.prey_path}` | {how} |")
    carried = (
        f"The receipt is recorded in `{ATTRIBUTIONS_PATH}` and rendered into "
        f"`{attribution_file}`; both are carried in this pull request."
    )
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
    its maw files are read under the maw's containment rule, its prey paths are checked against
    the prey's own history (a verbatim copy byte for byte), the receipt is appended to the maw's
    attributions under the identity rule, and the notice file is rendered from all of them. Only
    then is there a payload; the transaction scans it before any provider effect.
    """

    receipt = load_materialization_receipt(receipt_payload)
    _check_against_meal(card, receipt, menu, attribution_file)
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

    record = AttributionRecord(
        nutrient_id=card.id,
        source=receipt.source,
        taken=receipt.taken,
        mode=card.license_mode,
        obligation=obligation_for(receipt.source.license, as_dict(menu.get("verdict"))),
        summary=receipt.summary,
        branch=nutrient_branch_name(card.id),
        recorded_at=_stamp(now),
    )
    records = add_attribution(load_attributions(maw_root), record)
    recorded = next(item for item in records if item.identity == record.identity)

    files = [GeneratedFile(path=path, content=content) for path, content in maw_files.items()]
    files.append(GeneratedFile(path=ATTRIBUTIONS_PATH, content=dump_attributions(records)))
    files.append(GeneratedFile(path=attribution_file, content=render_notices(records)))

    rendered_body = body.rstrip()
    if "## Attribution" not in rendered_body:
        rendered_body += "\n\n" + _attribution_section(recorded, attribution_file)
    rendered_body += "\n"
    return PreparedPullRequest(title=title, body=rendered_body, files=tuple(files))
