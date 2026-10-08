"""One bounded, restart-safe phase per scheduler wake-up.

Model judgement is a receipt, never executable input. Only record advances a phase. A lease
reserves the daily budget before work and prevents concurrent schedulers from doing it twice.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .cache import Slug
from .digest import incomplete_miners
from .digest_integrity import digest_integrity_errors
from .errors import CrabError, UsageError
from .loop_config import AUTONOMIES, mapping, strings
from .loop_provider import LoopProvider
from .maw import MawConfig, maw_slug

LOOP_SCHEMA = "hungry-crab.loop/1"
PHASES = ("crave", "hunt", "eat", "serve", "grow", "trial", "taste", "molt", "harden")
WORK_PHASES = ("grow", "molt", "harden")


def stamp(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_members,
            parse_constant=_invalid_constant,
        )
    except (OSError, ValueError) as exc:
        raise CrabError(f"cannot read loop JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CrabError(f"loop JSON must be an object: {path}")
    return value


def safe_parent(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise CrabError(f"loop state must not traverse a symlink: {candidate}")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    safe_parent(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def state_lock(path: Path) -> Iterator[None]:
    """OS advisory locks disappear on process death, including on Windows."""
    safe_parent(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_parent(path.with_suffix(".lock"))
    with path.with_suffix(".lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CrabError("another process is updating this loop; try the next wake-up") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def ci_state(artifact: dict[str, Any]) -> str:
    """Latest check per name, plus legacy statuses; an empty suite is not a green suite."""
    latest: dict[str, dict[str, Any]] = {}
    for check in artifact.get("checks", []):
        key = str(check.get("name", ""))
        if int(check.get("id", 0)) > int(latest.get(key, {}).get("id", -1)):
            latest[key] = check
    statuses = artifact.get("statuses", [])
    conclusions = [check.get("conclusion") for check in latest.values()]
    if any(
        value in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
        for value in conclusions
    ) or any(row.get("state") in {"failure", "error"} for row in statuses):
        return "fail"
    if not latest and not statuses:
        return "pending"
    if any(check.get("status") != "completed" for check in latest.values()) or any(
        row.get("state") != "success" for row in statuses
    ):
        return "pending"
    return (
        "pass"
        if all(value in {"success", "neutral", "skipped"} for value in conclusions)
        else "pending"
    )


class Loop:
    def __init__(
        self,
        maw: Path,
        control: Path | None = None,
        *,
        provider: LoopProvider | None = None,
        now: datetime | None = None,
    ) -> None:
        self.maw = maw.resolve()
        if not self.maw.is_dir():
            raise UsageError(f"maw does not exist: {self.maw}")
        self.config = MawConfig.load(self.maw)
        self.settings = self.config.loop
        self.slug = maw_slug(self.maw)
        identity = str(self.slug) if self.slug else str(self.maw)
        self.key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        self.control = control.resolve() if control else None
        if self.control:
            self._control_settings()
        self.path = (
            (self.control / ".crab" / "loops" / f"{self.key}.json")
            if self.control
            else self.maw / ".crab" / "loop.json"
        )
        self.provider = provider
        self.now = (now or datetime.now(UTC)).astimezone(UTC)

    def _control_settings(self) -> None:
        import yaml

        registry = self.control / ".crab" / "maws.yml" if self.control else None
        if registry is None or not registry.exists():
            return
        try:
            loaded = yaml.safe_load(registry.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise UsageError("invalid control .crab/maws.yml") from exc
        entries = mapping(loaded, "control", {"maws"}).get("maws")
        if not isinstance(entries, list):
            raise UsageError("control.maws must be a list")
        matches: list[dict[str, Any]] = []
        for raw in entries:
            item = mapping(
                raw, "control.maws entry", {"repo", "path", "autonomy", "cadence", "prey"}
            )
            path = _text(item, "path")
            if (registry.parent.parent / path).resolve() == self.maw:
                matches.append(item)
        if len(matches) != 1:
            raise UsageError("control registry must contain exactly one entry for this maw path")
        entry = matches[0]
        if self.slug is None or Slug.parse(_text(entry, "repo")) != self.slug:
            raise UsageError("control registry repo does not match the maw origin")
        autonomy = entry.get("autonomy", "serve")
        if autonomy not in AUTONOMIES:
            raise UsageError("invalid control autonomy")
        self.settings.autonomy = AUTONOMIES[
            min(AUTONOMIES.index(autonomy), AUTONOMIES.index(self.settings.autonomy))
        ]
        cadence = entry.get("cadence", self.settings.cadence)
        if cadence not in {"daily", "weekly"}:
            raise UsageError("invalid control cadence")
        self.settings.cadence = cadence
        self.settings.prey = strings(entry.get("prey", self.settings.prey), "control.prey")

    def _load(self) -> dict[str, Any]:
        safe_parent(self.path)
        data = read_json(self.path)
        data.setdefault("revisions", {})
        if data.get("schema") != LOOP_SCHEMA:
            raise CrabError("unsupported loop schema; preserve the file and upgrade explicitly")
        if (
            data.get("maw") != str(self.maw)
            or data.get("slug") != (str(self.slug) if self.slug else None)
            or data.get("control") != (str(self.control) if self.control else None)
        ):
            raise CrabError("loop state belongs to a different maw or control repository")
        try:
            _require(data["phase"] in PHASES)
            _require(
                isinstance(data["revisions"], dict)
                and all(
                    isinstance(key, str) and type(value) is int and value >= 0
                    for key, value in data["revisions"].items()
                )
            )
            for field in ("round", "attempt", "phases_today", "prey_index"):
                _require(type(data[field]) is int and data[field] >= (1 if field == "round" else 0))
            _require(type(data["paused"]) is bool)
            for field in ("history", "prey", "prs", "issues", "meals"):
                _require(isinstance(data[field], list))
            _require(all(isinstance(item, str) for item in data["prey"]))
            if "discovery" in data:
                discovery = data["discovery"]
                _require(
                    isinstance(discovery, dict) and discovery.get("schema") == "hungry-crab.hunt/1"
                )
                _require(
                    isinstance(discovery.get("candidates"), list)
                    and len(discovery["candidates"]) <= 100
                )
                _require(
                    all(
                        isinstance(row, dict) and isinstance(row.get("prey"), str)
                        for row in discovery["candidates"]
                    )
                )
                _require(
                    isinstance(discovery.get("token"), str)
                    and type(discovery.get("round")) is int
                    and isinstance(discovery.get("policy_sha"), str)
                )
            _require(len(set(data["prey"])) == len(data["prey"]))
            _require(len(data["prey"]) <= 3)
            _require(data["prey_index"] <= len(data["prey"]))
            if data["phase"] == "eat":
                _require(data["prey_index"] < len(data["prey"]))
            for field in ("history", "prs", "meals"):
                _require(all(isinstance(item, dict) for item in data[field]))
            _require(all(isinstance(url, str) for url in data["issues"]))
            for meal in data["meals"]:
                _require(all(isinstance(meal[key], str) for key in ("prey", "meal", "notes")))
            _require(all(isinstance(data[field], str) for field in ("goal", "day", "notes")))
            for pr in data["prs"]:
                _require(
                    type(pr["round"]) is int
                    and pr["phase"] in WORK_PHASES
                    and pr["state"] in {"open", "closed", "merged"}
                    and isinstance(pr["sha"], str)
                    and isinstance(pr["url"], str)
                    and isinstance(pr["files"], list)
                )
                _require(
                    all(
                        isinstance(row, dict)
                        and isinstance(row.get("filename"), str)
                        and isinstance(row.get("status"), str)
                        for row in pr["files"]
                    )
                )
            if data["active"] is not None:
                lease = data["active"]
                _require(isinstance(lease, dict) and isinstance(lease["token"], str))
                _require(lease["phase"] == data["phase"] and lease["round"] == data["round"])
                _require(datetime.fromisoformat(lease["expires_at"]).tzinfo is not None)
                _require(datetime.fromisoformat(lease["started_at"]).tzinfo is not None)
            if data["waiting_on"] is not None:
                _require(isinstance(data["waiting_on"], dict))
                _require(data["waiting_on"]["kind"] in {"human", "ci", "merge"})
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            raise CrabError("malformed loop state; preserve it for recovery") from exc
        return data

    def _save(self, data: dict[str, Any]) -> None:
        atomic_json(self.path, data)

    def init(self) -> dict[str, Any]:
        for prey in self.settings.prey:
            Slug.parse(prey)
        if not self.settings.prey and not self.settings.discovery:
            raise UsageError("configure a fixed loop.prey list in the maw's .crab.yml first")
        with state_lock(self.path):
            if self.path.exists():
                raise CrabError("loop already exists; init never resets history or a paused loop")
            data: dict[str, Any] = {
                "schema": LOOP_SCHEMA,
                "maw": str(self.maw),
                "slug": str(self.slug) if self.slug else None,
                "control": str(self.control) if self.control else None,
                "round": 1,
                "phase": "crave",
                "attempt": 0,
                "paused": False,
                "day": self.now.date().isoformat(),
                "phases_today": 0,
                "active": None,
                "waiting_on": None,
                "goal": "",
                "prey": [],
                "prey_index": 0,
                "history": [],
                "prs": [],
                "issues": [],
                "meals": [],
                "notes": "",
                "revisions": {},
            }
            self._save(data)
            return self._view(data)

    def _provider(self) -> LoopProvider:
        if self.provider is None:
            raise CrabError("provider truth is required before loop issue or PR work")
        return self.provider

    def _work_allowed(self) -> bool:
        return (
            self.settings.autonomy == "work"
            and self.settings.work_authorized
            and self.config.ledger != "none"
        )

    def _optional_empty(self, data: dict[str, Any]) -> bool:
        return data["phase"] in {"molt", "harden"} and not self._landed(data)

    def _reason(self, data: dict[str, Any]) -> str | None:
        if data["paused"]:
            return "paused"
        if data["waiting_on"]:
            return str(data["waiting_on"].get("reason", data["waiting_on"]["kind"]))
        if data["active"] and datetime.fromisoformat(data["active"]["expires_at"]) > self.now:
            return "phase already leased"
        if data["attempt"] >= self.settings.max_attempts:
            return "retry limit; human acknowledgement required"
        if data["phase"] == "serve" and self.settings.autonomy == "read":
            return "read autonomy stops before SERVE"
        if (
            data["phase"] in WORK_PHASES
            and not self._optional_empty(data)
            and not self._work_allowed()
        ):
            return "work requires loop.autonomy: work and maw-owned work_authorized: true"
        used = data["phases_today"] if data["day"] == self.now.date().isoformat() else 0
        if used >= self.settings.phases_per_day:
            return "daily phase budget exhausted (resets at UTC midnight)"
        return None

    def _view(self, data: dict[str, Any], *, ready: bool = False) -> dict[str, Any]:
        used = data["phases_today"] if data["day"] == self.now.date().isoformat() else 0
        prey = data["prey"][data["prey_index"]] if data["prey_index"] < len(data["prey"]) else None
        return {
            **data,
            "state_path": str(self.path),
            "ready": ready,
            "blocked_reason": None if ready else self._reason(data),
            "autonomy": self.settings.autonomy,
            "cadence": self.settings.cadence,
            "budget_left": max(0, self.settings.phases_per_day - used),
            "current_prey": prey,
            "inputs": {
                "ledger": str(self.config.ledger_path()) if self.config.ledger_path() else None,
                "config": str(self.config.path),
                "prey_candidates": self.settings.prey
                or [row["prey"] for row in data.get("discovery", {}).get("candidates", [])],
                "meals": data["meals"],
                "notes": data["notes"],
                "goal": data["goal"],
            },
            "limits": {
                "prey_per_round": self.settings.prey_per_round,
                "open_issues_max": self.settings.open_issues_max,
                "open_prs_max": self.settings.open_prs_max,
            },
        }

    def status(self) -> dict[str, Any]:
        return self._view(self._load())

    def discover(
        self, token: str, *, search: Callable[[Path], dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        from .hunt import hunt

        if not self.settings.discovery:
            raise UsageError("loop.discovery must be enabled in the maw before discovering prey")
        with state_lock(self.path):
            data = self._load()
            self.active(data, token, "hunt")
            policy = self.config.path.read_bytes() if self.config.exists else b""
        started = datetime.now(UTC)
        report = search(self.maw) if search else hunt(self.maw)
        self.now += datetime.now(UTC) - started
        with state_lock(self.path):
            data = self._load()
            self.active(data, token, "hunt")
            current = self.config.path.read_bytes() if self.config.path.exists() else b""
            if current != policy:
                raise CrabError("maw policy changed during discovery; repeat HUNT")
            if report.get("schema") != "hungry-crab.hunt/1" or not isinstance(
                report.get("candidates"), list
            ):
                raise CrabError("invalid discovery report")
            data["discovery"] = {
                **report,
                "token": token,
                "round": data["round"],
                "policy_sha": hashlib.sha256(policy).hexdigest(),
            }
            self._save(data)
            return self._view(data)

    def marker(self, data: dict[str, Any]) -> str:
        revision = data["revisions"].get(f"{data['round']}:{data['phase']}", 0)
        return f"<!-- crab:loop:{self.key}:{data['round']}:{data['phase']}:{revision} -->"

    def branch(self, data: dict[str, Any]) -> str:
        revision = data["revisions"].get(f"{data['round']}:{data['phase']}", 0)
        return f"crab/loop/{self.key}/r{data['round']}/{data['phase']}-v{revision}"

    def _reconcile(self, data: dict[str, Any]) -> None:
        waiting = data["waiting_on"]
        if not waiting or waiting["kind"] == "human":
            return
        pending = False
        for pr in data["prs"]:
            if pr["round"] != data["round"] or pr.get("state") in {"merged", "closed"}:
                continue
            truth = self._provider().artifact(pr["url"])
            if truth["head"]["sha"] != pr["sha"]:
                data["waiting_on"] = {
                    "kind": "human",
                    "reason": "PR head changed; re-trial required",
                }
                return
            if truth.get("merged_at"):
                pr["state"] = "merged"
                pr["merge_sha"] = truth["merge_commit_sha"]
            elif truth.get("state") == "closed":
                pr["state"] = "closed"
            elif pr["phase"] == "molt" and ci_state(truth) == "fail":
                if not self._work_allowed():
                    data["waiting_on"] = {"kind": "human", "reason": "MOLT CI failed; work revoked"}
                    return
                self._provider().close_pr(pr["url"])
                pr["state"] = "closed"
                data["notes"] += (
                    f"\nRejected MOLT {pr['url']}: CI failed; do not debug indefinitely."
                )
            elif waiting["kind"] == "ci":
                status = ci_state(truth)
                pending |= status == "pending"
                if status == "fail":
                    data["waiting_on"] = {"kind": "human", "reason": "CI failed; retry or drop PR"}
                    return
            else:
                pending = True
        if not pending:
            data["waiting_on"] = None

    def next(self) -> dict[str, Any]:
        with state_lock(self.path):
            data = self._load()
            if data["paused"]:
                return self._view(data)
            before = json.dumps(data, sort_keys=True)
            if data["active"] and datetime.fromisoformat(data["active"]["expires_at"]) <= self.now:
                self._event(data, "fail", "lease expired; interrupted work is not success", {})
                data["active"] = None
                self._save(data)
            self._reconcile(data)
            reason = self._reason(data)
            if reason:
                if json.dumps(data, sort_keys=True) != before:
                    self._save(data)
                return self._view(data)
            if data["phase"] in ("serve", *WORK_PHASES) and not self._optional_empty(data):
                issues, prs = self._provider().counts()
                limit = (
                    issues >= self.settings.open_issues_max
                    if data["phase"] == "serve"
                    else prs >= self.settings.open_prs_max
                )
                if limit:
                    return {**self._view(data), "blocked_reason": "open artifact budget exhausted"}
            if data["day"] != self.now.date().isoformat():
                data["day"] = self.now.date().isoformat()
                data["phases_today"] = 0
            data["attempt"] += 1
            data["phases_today"] += 1
            data["active"] = {
                "token": uuid.uuid4().hex,
                "phase": data["phase"],
                "round": data["round"],
                "started_at": stamp(self.now),
                "expires_at": stamp(self.now + timedelta(minutes=self.settings.lease_minutes)),
            }
            self._save(data)
            return self._view(data, ready=True)

    def active(self, data: dict[str, Any], token: str, phase: str | None = None) -> None:
        lease = data["active"]
        if data["paused"] or not lease or lease["token"] != token:
            raise CrabError("no matching active loop lease (or the loop is paused)")
        if datetime.fromisoformat(lease["expires_at"]) <= self.now:
            raise CrabError("loop lease expired; reconcile effects before retrying")
        if phase is not None and data["phase"] != phase:
            raise CrabError("record phase does not match the active phase")
        if (
            data["phase"] in WORK_PHASES
            and not self._optional_empty(data)
            and not self._work_allowed()
        ):
            raise CrabError("maw policy no longer authorizes loop work")
        if data["phase"] == "serve" and self.settings.autonomy == "read":
            raise CrabError("maw policy no longer authorizes issue serving")

    def _event(self, data: dict[str, Any], result: str, note: str, receipt: dict[str, Any]) -> None:
        active = data["active"]
        seconds = max(
            0.0, (self.now - datetime.fromisoformat(active["started_at"])).total_seconds()
        )
        data["history"].append(
            {
                "round": data["round"],
                "phase": data["phase"],
                "attempt": data["attempt"],
                "result": result,
                "note": note,
                "at": stamp(self.now),
                "wall_seconds": seconds,
                "tokens": receipt.get("tokens"),
                "cost_usd": receipt.get("cost_usd"),
                "receipt": receipt,
            }
        )

    def _eat(self, data: dict[str, Any], receipt: dict[str, Any]) -> None:
        meal_path = Path(_text(receipt, "meal")).resolve()
        meal = read_json(meal_path)
        menu_path = meal_path.parent / "menu.json"
        menu = read_json(menu_path)
        prey = data["prey"][data["prey_index"]]
        if meal.get("prey", {}).get("label") != prey or menu.get("prey") != meal.get("prey"):
            raise CrabError("meal and menu must describe the active prey at the same commit")
        if Path(meal.get("maw", {}).get("root", "")).resolve() != self.maw:
            raise CrabError("meal belongs to another maw")
        try:
            generated = datetime.fromisoformat(meal["generated_at"])
            if (
                generated.tzinfo is None
                or generated < datetime.fromisoformat(data["active"]["started_at"])
                or menu.get("generated_at") != meal["generated_at"]
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise CrabError("EAT requires a fresh meal/menu generated during this lease") from exc
        for key in ("prey_digest", "maw_digest"):
            directory = Path(_text(meal, key))
            if not directory.is_absolute():
                directory = meal_path.parent / directory
            manifest = read_json(directory / "manifest.json")
            side = "prey" if key == "prey_digest" else "maw"
            if manifest.get("schema") != "hungry-crab.digest/1" or manifest.get("prey", {}).get(
                "sha"
            ) != meal[side].get("sha"):
                raise CrabError("meal digest commit differs from the compared side")
            problem = digest_integrity_errors(directory, manifest)
            if (
                problem
                or incomplete_miners(manifest)
                or manifest.get("coverage", {}).get("healthy") is not True
            ):
                raise CrabError(f"loop refuses incomplete digest evidence: {key}")
            if any(row.get("name") == "wiki" for row in manifest.get("miners", [])) or (
                manifest.get("wiki", {}).get("status") == "available"
            ):
                wiki = read_json(directory / "wiki.json")
                if wiki.get("coverage", {}).get("healthy") is not True:
                    raise CrabError(f"loop refuses incomplete wiki evidence: {key}")
        notes = read_json(Path(_text(receipt, "notes")))
        ids = {row["id"] for row in menu.get("candidates", [])}
        if any(
            key not in ids
            or not isinstance(value, dict)
            or not value.get("why")
            or not value.get("how")
            for key, value in notes.items()
        ):
            raise CrabError("EAT notes require why/how for real menu nutrient IDs")
        data["meals"].append(
            {
                "prey": prey,
                "meal": str(menu_path.parent / "meal.json"),
                "menu": str(menu_path),
                "notes": receipt["notes"],
            }
        )

    def _accept(self, data: dict[str, Any], receipt: dict[str, Any]) -> None:
        phase = data["phase"]
        if phase == "crave":
            data["goal"] = _text(receipt, "goal")
        elif phase == "hunt":
            prey = receipt.get("prey")
            configured = {str(Slug.parse(item)) for item in self.settings.prey}
            if self.settings.discovery and not configured:
                discovery = data.get("discovery", {})
                if (
                    discovery.get("round") != data["round"]
                    or discovery.get("token") != data["active"]["token"]
                ):
                    raise CrabError("HUNT requires discovery during the current lease")
                policy = self.config.path.read_bytes() if self.config.path.exists() else b""
                if discovery.get("policy_sha") != hashlib.sha256(policy).hexdigest():
                    raise CrabError("maw policy changed after discovery; repeat HUNT")
                configured = {
                    str(Slug.parse(row["prey"])) for row in discovery.get("candidates", [])
                }
            if (
                not isinstance(prey, list)
                or not prey
                or len(prey) > self.settings.prey_per_round
                or (any(not isinstance(item, str) or item not in configured for item in prey))
                or len(set(prey)) != len(prey)
            ):
                raise CrabError(
                    "HUNT must select distinct prey from the current maw shortlist within budget"
                )
            data["prey"] = prey
            data["prey_index"] = 0
        elif phase == "eat":
            self._eat(data, receipt)
        elif phase == "serve":
            urls = receipt.get("urls")
            if (
                not isinstance(urls, list)
                or not urls
                or any(not isinstance(url, str) for url in urls)
            ):
                raise CrabError("SERVE receipt requires issue URLs")
            for url in urls:
                if url not in data["issues"]:
                    raise CrabError(
                        "SERVE must publish through crab loop serve before recording ok"
                    )
                truth = self._provider().artifact(url)
                if "/issues/" not in url or not str(truth.get("body", "")).startswith("<!-- crab:"):
                    raise CrabError("SERVE must reference Hungry Crab issues in this maw")
            data["issues"] = list(dict.fromkeys([*data["issues"], *urls]))
        elif phase in WORK_PHASES:
            pr = next(
                (
                    row
                    for row in data["prs"]
                    if row["round"] == data["round"] and row["phase"] == phase
                ),
                None,
            )
            if pr is None:
                raise CrabError(
                    "work phases must publish through crab loop publish before recording ok"
                )
            if phase == "harden" and not pr.get("tagged"):
                raise CrabError(
                    "HARDEN finishes only after the release PR merges and loop tag succeeds"
                )
        elif phase == "trial":
            pr = next(
                (
                    row
                    for row in reversed(data["prs"])
                    if row["round"] == data["round"] and row["phase"] == "grow"
                ),
                None,
            )
            truth = self._provider().artifact(pr["url"]) if pr else {}
            if (
                pr is None
                or truth.get("head", {}).get("sha") != pr["sha"]
                or ci_state(truth) != "pass"
            ):
                raise CrabError("TRIAL needs passing CI on the recorded GROW head")
            if receipt.get("tests_passed") is not True:
                raise CrabError("TRIAL requires a tests_passed receipt")
            data["waiting_on"] = {"kind": "merge", "reason": "waiting for human GROW merge"}
        elif phase == "taste":
            data["notes"] = _text(receipt, "lesson")
            if receipt.get("goal"):
                data["goal"] = _text(receipt, "goal")

    def _advance(self, data: dict[str, Any], result: str) -> None:
        phase = data["phase"]
        if phase == "eat" and data["prey_index"] + 1 < len(data["prey"]):
            data["prey_index"] += 1
        elif phase == "grow" and result == "skip":
            data["phase"] = "taste"
        elif phase == "harden":
            data["round"] += 1
            data["phase"] = "hunt"
            data["prey"] = []
            data["prey_index"] = 0
            data["meals"] = []
            data.pop("discovery", None)
        else:
            data["phase"] = PHASES[PHASES.index(phase) + 1]
        data["attempt"] = 0

    def record(
        self,
        token: str,
        phase: str,
        result: str,
        *,
        note: str = "",
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if result not in {"ok", "fail", "skip"}:
            raise UsageError("loop result must be ok, fail or skip")
        payload = receipt or {}
        _metrics(payload)
        with state_lock(self.path):
            data = self._load()
            self.active(data, token, phase)
            if result == "ok":
                self._accept(data, payload)
            elif result == "skip":
                if not note.strip() or phase in {"crave", "hunt", "eat", "taste"}:
                    raise CrabError(
                        "this phase needs evidence; skip needs a reason and an optional phase"
                    )
                if phase == "trial":
                    self._drop_grow(data, _text(payload, "drop_pr"), note)
                if any(
                    pr["round"] == data["round"]
                    and pr["phase"] == phase
                    and pr["state"] != "closed"
                    for pr in data["prs"]
                ):
                    raise CrabError("cannot skip a published phase until its PR is closed")
                if phase in {"molt", "harden"} and phase == "harden" and self._landed(data):
                    raise CrabError("a round with landed changes must be hardened")
            self._event(data, result, note, payload)
            data["active"] = None
            if result == "fail" and phase == "molt":
                for pr in data["prs"]:
                    if (
                        pr["round"] == data["round"]
                        and pr["phase"] == "molt"
                        and pr["state"] == "open"
                    ):
                        truth = self._provider().artifact(pr["url"])
                        if truth.get("merged_at") or truth["head"]["sha"] != pr["sha"]:
                            raise CrabError("MOLT head changed or merged; human attention required")
                        self._provider().close_pr(pr["url"])
                        pr["state"] = "closed"
                data["notes"] += f"\nMOLT rejected: {note}"
                data["waiting_on"] = None
                self._advance(data, "skip")
            elif result != "fail":
                self._advance(data, result)
            elif data["attempt"] >= self.settings.max_attempts:
                data["waiting_on"] = {"kind": "human", "reason": "retry limit reached"}
            self._save(data)
            return self._view(data)

    def _landed(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            pr
            for pr in data["prs"]
            if pr["round"] == data["round"] and pr["state"] == "merged" and pr["phase"] != "harden"
        ]

    def pause(self, paused: bool) -> dict[str, Any]:
        with state_lock(self.path):
            data = self._load()
            data["paused"] = paused
            self._save(data)
            return self._view(data)

    def _drop_grow(self, data: dict[str, Any], url: str, note: str) -> None:
        pr = next(
            (
                row
                for row in data["prs"]
                if row["round"] == data["round"] and row["phase"] == "grow" and row["url"] == url
            ),
            None,
        )
        if pr is None:
            raise CrabError("drop requires this round's GROW pull request")
        truth = self._provider().artifact(url)
        if truth.get("merged_at") or truth["head"]["sha"] != pr["sha"]:
            raise CrabError("cannot drop a merged or changed GROW head")
        if truth.get("state") == "open":
            self._provider().close_pr(url)
        pr["state"] = "closed"
        data["notes"] += f"\nDropped GROW {url}: {note}"
        data["waiting_on"] = None

    def acknowledge(self, *, skip_work: bool = False, drop_pr: str | None = None) -> dict[str, Any]:
        """A human-only recovery command; the phase skill must never call it."""
        with state_lock(self.path):
            data = self._load()
            if data["active"]:
                raise CrabError("cannot acknowledge while a phase is leased")
            if drop_pr:
                if data["phase"] != "trial":
                    raise CrabError("human drop is available at TRIAL only")
                self._drop_grow(data, drop_pr, "human rejected implementation")
                data["phase"] = "taste"
            if data["phase"] == "harden" and any(
                pr["round"] == data["round"] and pr["phase"] == "harden" and pr["state"] == "closed"
                for pr in data["prs"]
            ):
                key = f"{data['round']}:harden"
                data["revisions"][key] = data["revisions"].get(key, 0) + 1
            if skip_work:
                if data["phase"] not in {"serve", "grow", "molt", "harden"} or self._landed(data):
                    raise CrabError("cannot skip required evidence or hardening of landed changes")
                if any(
                    pr["round"] == data["round"] and pr["state"] == "open" for pr in data["prs"]
                ):
                    raise CrabError("close pending round PRs before skipping work")
                data["phase"] = "taste" if data["phase"] in {"serve", "grow"} else "harden"
                if data["phase"] == "harden" and not self._landed(data):
                    self._advance(data, "skip")
            data["waiting_on"] = None
            data["attempt"] = 0
            data["history"].append(
                {
                    "round": data["round"],
                    "phase": data["phase"],
                    "result": "acknowledged",
                    "at": stamp(self.now),
                    "skip_work": skip_work,
                    "drop_pr": drop_pr,
                    "wall_seconds": 0,
                    "tokens": None,
                    "cost_usd": None,
                }
            )
            self._save(data)
            return self._view(data)


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CrabError(f"loop receipt requires a non-empty {key}")
    return value


def _require(condition: bool) -> None:
    if not condition:
        raise AssertionError("invalid loop state")


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _metrics(receipt: dict[str, Any]) -> None:
    import math

    for key in ("tokens", "cost_usd"):
        value = receipt.get(key)
        if value is not None and (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < 0
            or (key == "tokens" and type(value) is not int)
        ):
            raise UsageError(f"loop receipt {key} must be a non-negative finite number")
