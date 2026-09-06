"""Host-side configuration (``.crab.yml``) and the identity of the host repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .cache import Slug, host_paths
from .errors import CrabError, UsageError
from .fetch.git import GitRunner
from .typeutil import as_dict, as_list

CONFIG_FILE = ".crab.yml"
LEDGER_MODES = ("repo", "cache", "none")
SERVE_MODES = ("auto", "ask", "off")
HOST_MODES = ("normal", "strict")
DEFAULT_LABEL = "hungry-crab"

DEFAULT_APPETITE: dict[str, Any] = {
    "security": True,
    "ci": True,
    "tests": True,
    "tooling": True,
    "ai-config": True,
    "hygiene": True,
    "docs": True,
    "deps": True,
    "history-lesson": True,
    "issue-lesson": True,
    "architecture": "issues-only",
    "code": "ideas-only",
}

DEFAULT_CONFIG_TEXT = """\
# Hungry Crab host configuration. Every key is optional; these are the defaults.
license: null              # SPDX id of this repository; detected from LICENSE when null
mode: normal               # normal | strict (strict never copies code, only configs and templates)
appetite:                  # per nutrient category: true | false | issues-only | ideas-only
  security: true
  ci: true
  tests: true
  tooling: true
  ai-config: true
  hygiene: true
  docs: true
  deps: true
  history-lesson: true
  issue-lesson: true
  architecture: issues-only
  code: ideas-only
ignore: []                 # globs excluded from this repository's own digest, so that test
                           # fixtures and vendored trees are not mistaken for your code, e.g.
                           # [tests/fixtures/**, examples/**]
serve:
  issues: ask              # auto | ask | off
  prs: ask                 # auto | ask | off (pull requests arrive with 0.3)
  max_prs_per_run: 3
  labels: [hungry-crab]
  assignees: []
  token_env: ""            # environment variable holding the token to file issues as, e.g.
                           # CRAB_BOT_TOKEN with a GitHub App installation token. Empty means
                           # gh's own login: your issues carry your name, not the crab's.
attribution_file: THIRD_PARTY_NOTICES.md
ledger: repo               # repo (.crab/ledger.json, committed) | cache | none
scoring: {}                # overrides for data/scoring.yml sections; `crab tune` suggests them
"""


def _appetite_value(value: object) -> Any:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "on"):
        return True
    if text in ("false", "no", "off"):
        return False
    if text in ("issues-only", "ideas-only"):
        return text
    raise UsageError(
        f"invalid appetite value {value!r}",
        hint="use true, false, issues-only or ideas-only",
    )


def _choice(value: object, allowed: tuple[str, ...], what: str) -> str:
    # YAML 1.1 reads `off`/`on` as booleans; map them back to the serve modes.
    if value is False and "off" in allowed:
        return "off"
    if value is True and "auto" in allowed:
        return "auto"
    text = str(value).strip().lower()
    if text not in allowed:
        raise UsageError(f"invalid {what} {value!r}", hint=f"use one of: {', '.join(allowed)}")
    return text


@dataclass
class ServeSettings:
    issues: str = "ask"
    prs: str = "ask"
    max_prs_per_run: int = 3
    labels: list[str] = field(default_factory=lambda: [DEFAULT_LABEL])
    assignees: list[str] = field(default_factory=list)
    token_env: str = ""

    @property
    def label(self) -> str:
        return self.labels[0] if self.labels else DEFAULT_LABEL


@dataclass
class HostConfig:
    root: Path
    exists: bool = False
    license: str | None = None
    mode: str = "normal"
    appetite: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_APPETITE))
    ignore: list[str] = field(default_factory=list)
    serve: ServeSettings = field(default_factory=ServeSettings)
    attribution_file: str = "THIRD_PARTY_NOTICES.md"
    ledger: str = "repo"
    scoring: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.root / CONFIG_FILE

    @classmethod
    def load(cls, root: Path) -> HostConfig:
        config = cls(root=root.resolve())
        path = config.path
        if not path.is_file():
            return config
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise UsageError(f"{path} is not valid YAML: {exc}") from exc
        data = as_dict(loaded)
        config.exists = True
        config.raw = data
        license_value = data.get("license")
        if isinstance(license_value, str) and license_value.strip():
            config.license = license_value.strip()
        config.mode = _choice(data.get("mode", "normal"), HOST_MODES, "mode")
        appetite = dict(DEFAULT_APPETITE)
        for key, value in as_dict(data.get("appetite")).items():
            appetite[str(key)] = _appetite_value(value)
        config.appetite = appetite
        config.ignore = [str(pattern) for pattern in as_list(data.get("ignore"))]
        serve = as_dict(data.get("serve"))
        max_prs = serve.get("max_prs_per_run", 3)
        labels = [str(label) for label in as_list(serve.get("labels")) if str(label).strip()]
        config.serve = ServeSettings(
            issues=_choice(serve.get("issues", "ask"), SERVE_MODES, "serve.issues"),
            prs=_choice(serve.get("prs", "ask"), SERVE_MODES, "serve.prs"),
            max_prs_per_run=max_prs
            if isinstance(max_prs, int) and not isinstance(max_prs, bool)
            else 3,
            labels=labels or [DEFAULT_LABEL],
            assignees=[str(a) for a in as_list(serve.get("assignees"))],
            token_env=str(serve.get("token_env") or "").strip(),
        )
        attribution = data.get("attribution_file")
        if isinstance(attribution, str) and attribution.strip():
            config.attribution_file = attribution.strip()
        config.ledger = _choice(data.get("ledger", "repo"), LEDGER_MODES, "ledger mode")
        config.scoring = as_dict(data.get("scoring"))
        return config

    def ledger_path(self, cache_root: Path | None = None) -> Path | None:
        if self.ledger == "repo":
            return self.root / ".crab" / "ledger.json"
        if self.ledger == "cache":
            return host_paths(self.root, cache_root).ledger_file
        return None

    def write_scoring(self, scoring: dict[str, Any]) -> Path:
        """Persist scoring overrides. Comments in an existing file are not preserved."""
        data = dict(self.raw) if self.exists else as_dict(yaml.safe_load(DEFAULT_CONFIG_TEXT))
        data["scoring"] = scoring
        self.path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
            newline="\n",
        )
        self.raw = data
        self.scoring = scoring
        self.exists = True
        return self.path


def write_default_config(root: Path, *, force: bool = False) -> Path:
    path = root / CONFIG_FILE
    if path.exists() and not force:
        raise CrabError(f"{path} already exists", hint="pass --force to overwrite it")
    path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8", newline="\n")
    return path


def host_slug(root: Path) -> Slug | None:
    """The GitHub repository behind ``origin``, if there is one."""
    if not GitRunner.available():
        return None
    url = GitRunner(root).try_run("remote", "get-url", "origin")
    if not url or not url.strip():
        return None
    try:
        return Slug.parse(url.strip())
    except UsageError:
        return None
