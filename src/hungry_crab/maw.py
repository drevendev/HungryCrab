"""Maw-side configuration (``.crab.yml``) and the identity of the maw repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import yaml

from .budget import BUDGET_POLICIES
from .cache import Slug, Target, maw_paths
from .config_edit import atomic_text, replace_section
from .config_validation import validate
from .errors import CrabError, UsageError
from .fetch.git import GitRunner
from .hunt_config import HuntSettings
from .licensing import Relationship
from .loop_config import LoopSettings
from .memory import MemorySettings
from .profiles import hunger_for
from .typeutil import as_dict, as_list

CONFIG_FILE = ".crab.yml"
LEDGER_MODES = ("repo", "cache", "none")
SERVE_MODES = ("auto", "ask", "off")
MAW_MODES = ("normal", "strict")
DEFAULT_LABEL = "hungry-crab"

DEFAULT_HUNGER: dict[str, Any] = {
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
# Hungry Crab maw configuration. Every key is optional; these are the defaults.
license: null              # SPDX id of this repository; detected from LICENSE when null
mode: normal               # normal | strict. Strict downgrades COPY code to clean-room
                           # REIMPLEMENT; configs and templates remain copyable.
profile: balanced          # balanced | library | cli | service | frontend; explicit hunger wins
memory:
  enabled: true            # confirmed decisions influence ranking; explicit weights still win
  min_decisions: 3
  strength: 0.3            # bounded multiplier; never changes license or hunger ceilings
hunt:
  queries: []              # derive up to four GitHub searches from enabled maw gaps
  exclude: []
  licenses: []
  min_stars: 20
  max_repo_kb: 307200
  max_candidates: 50
  limit: 10
  include_seen: false
  allow_unknown_size: false
hunger:                    # per nutrient category: true | false | issues-only | ideas-only
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
  code: ideas-only         # source-linked behavior findings from crab explore register
ignore: []                 # globs excluded from this repository's own digest, so that test
                           # fixtures and vendored trees are not mistaken for your code, e.g.
                           # [tests/fixtures/**, examples/**]. Patterns are case-sensitive on
                           # every platform, like the git paths they match.
budget:
  policy: warn             # warn | enforce | off for the whole digest Markdown budget
serve:
  issues: ask              # auto | ask | off
  prs: ask                 # auto | ask | off; gates --as pr-branch (REIMPLEMENT and COPY)
  max_prs_per_run: 3
  publication_allow: []    # exact content-bound finding/reason entries; commit before use
  labels: [hungry-crab]
  assignees: []
  token_env: ""            # environment variable holding the token to file issues as, e.g.
                           # CRAB_BOT_TOKEN with a GitHub App installation token. Empty means
                           # gh's own login: your issues carry your name, not the crab's.
trust:                     # a license is a promise to strangers; these are not strangers
  same_owner: true         # prey owned by the account that owns this repository is your own code
  owners: []               # other accounts whose repositories count as your own, e.g. [acme-inc]
  bypass_license: false    # last resort: treat every prey as COPY. Every card says so, and the
                           # verdict is flagged for human review, because this is not a finding
                           # about the license but a decision to stop asking.
attribution_file: THIRD_PARTY_NOTICES.md
                           # where COPY records its sources: rendered by `crab attribution`
                           # from the receipts in .crab/attributions.json, which
                           # `crab serve --as pr-branch` writes when it carries prey files in.
ledger: repo               # repo (.crab/ledger.json, committed) | cache | none
scoring: {}                # overrides for data/scoring.yml sections; `crab tune` suggests them
loop:
  cadence: daily           # cadence belongs to your scheduler; the CLI never starts one
  autonomy: read           # read | serve | work; merging always belongs to a human
  work_authorized: false   # the maw owner must explicitly authorize work, including tags
  discovery: false         # opt in to a lease-bound HUNT shortlist
  prey: []                 # fixed allowlist; empty requires discovery
  budget:
    phases_per_day: 4
    prey_per_round: 2
    open_issues_max: 10
    open_prs_max: 2
"""


def _reject_unknown_keys(
    values: dict[str, Any], allowed: set[str], path: Path, section: str
) -> None:
    """A maw typo must never silently broaden the crab's appetite."""
    for raw_key in values:
        key = str(raw_key)
        if key in allowed:
            continue
        suggestion = get_close_matches(key, sorted(allowed), n=1, cutoff=0.45)
        hint = (
            f"did you mean {suggestion[0]!r}?"
            if suggestion
            else f"allowed keys: {', '.join(sorted(allowed))}"
        )
        raise UsageError(f"{path}: unknown {section} key {key!r}", hint=hint)


def _required_mapping_section(data: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    """An explicit wrong-shaped section must never silently restore defaults."""
    if name not in data:
        return {}
    value = data[name]
    if not isinstance(value, dict):
        raise UsageError(f"{path}: {name} must be a mapping")
    return value


def _strict_trust_bool(values: dict[str, Any], key: str, default: bool, path: Path) -> bool:
    """A quoted false must never silently grant same-owner trust."""
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise UsageError(
            f"{path}: trust.{key} must be a boolean",
            hint="use unquoted true or false",
        )
    return value


def _hunger_value(value: object) -> Any:
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
        f"invalid hunger value {value!r}",
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
class BudgetSettings:
    policy: str = "warn"


@dataclass
class ServeSettings:
    issues: str = "ask"
    prs: str = "ask"
    max_prs_per_run: int = 3
    labels: list[str] = field(default_factory=lambda: [DEFAULT_LABEL])
    assignees: list[str] = field(default_factory=list)
    token_env: str = ""
    publication_allow: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.labels[0] if self.labels else DEFAULT_LABEL


@dataclass
class TrustSettings:
    """Who counts as ourselves. A license governs strangers, not the owner of the code.

    ``same_owner`` covers the common case without configuration: the maw's ``origin`` and the
    prey belong to one account. ``owners`` extends that to an organisation a solo maintainer also
    publishes under. ``bypass_license`` is the escape hatch and is never silent.
    """

    same_owner: bool = True
    owners: list[str] = field(default_factory=list)
    bypass_license: bool = False

    def trusts(self, owner: str | None) -> bool:
        if not owner:
            return False
        return owner.lower() in {name.lower() for name in self.owners}


@dataclass
class MawConfig:
    root: Path
    exists: bool = False
    license: str | None = None
    mode: str = "normal"
    profile: str = "balanced"
    memory: MemorySettings = field(default_factory=MemorySettings)
    hunt: HuntSettings = field(default_factory=HuntSettings)
    hunger: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_HUNGER))
    ignore: list[str] = field(default_factory=list)
    budget: BudgetSettings = field(default_factory=BudgetSettings)
    serve: ServeSettings = field(default_factory=ServeSettings)
    trust: TrustSettings = field(default_factory=TrustSettings)
    attribution_file: str = "THIRD_PARTY_NOTICES.md"
    ledger: str = "repo"
    scoring: dict[str, Any] = field(default_factory=dict)
    loop: LoopSettings = field(default_factory=LoopSettings)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.root / CONFIG_FILE

    @classmethod
    def load(cls, root: Path) -> MawConfig:
        config = cls(root=root.resolve())
        path = config.path
        if not path.is_file():
            return config
        source_text = path.read_text(encoding="utf-8")
        try:
            loaded = yaml.safe_load(source_text)
        except yaml.YAMLError as exc:
            raise UsageError(f"{path} is not valid YAML: {exc}") from exc
        if loaded is None and all(
            not line.strip() or line.lstrip().startswith("#") for line in source_text.splitlines()
        ):
            loaded = {}
        if not isinstance(loaded, dict):
            raise UsageError(f"{path}: configuration must be a YAML mapping")
        return cls.from_data(root, loaded)

    @classmethod
    def from_data(cls, root: Path, data: dict[str, Any]) -> MawConfig:
        """Validate an in-memory configuration through exactly the normal read path."""
        config = cls(root=root.resolve())
        path = config.path
        config.exists = True
        config.raw = data
        license_value = data.get("license")
        if isinstance(license_value, str) and license_value.strip():
            config.license = license_value.strip()
        config.mode = _choice(data.get("mode", "normal"), MAW_MODES, "mode")
        if "appetite" in data:
            # Silently ignoring it would leave a maw eating things it had switched off.
            raise UsageError(
                f"{path} uses the old key 'appetite'",
                hint="rename it to 'hunger'; the values are unchanged",
            )
        validate(data)
        config.profile = str(data.get("profile", "balanced"))
        hunger = hunger_for(config.profile)
        config.memory = MemorySettings.load(data.get("memory", {}))
        config.hunt = HuntSettings.load(data.get("hunt", {}))
        for key, value in as_dict(data.get("hunger")).items():
            hunger[str(key)] = _hunger_value(value)
        config.hunger = hunger
        config.ignore = [str(pattern) for pattern in as_list(data.get("ignore"))]
        budget = _required_mapping_section(data, "budget", path)
        _reject_unknown_keys(budget, {"policy"}, path, "budget")
        config.budget = BudgetSettings(
            policy=_choice(budget.get("policy", "warn"), BUDGET_POLICIES, "budget.policy")
        )
        serve = _required_mapping_section(data, "serve", path)
        _reject_unknown_keys(
            serve,
            {
                "issues",
                "prs",
                "max_prs_per_run",
                "labels",
                "assignees",
                "token_env",
                "publication_allow",
            },
            path,
            "serve",
        )
        max_prs = serve.get("max_prs_per_run", 3)
        if isinstance(max_prs, bool) or not isinstance(max_prs, int) or max_prs < 0:
            raise UsageError(
                f"{path}: serve.max_prs_per_run must be a non-negative integer",
                hint="use 0, 1, 2, ...",
            )
        labels = [str(label) for label in as_list(serve.get("labels")) if str(label).strip()]
        from .publication_policy import parse_publication_allow

        config.serve = ServeSettings(
            issues=_choice(serve.get("issues", "ask"), SERVE_MODES, "serve.issues"),
            prs=_choice(serve.get("prs", "ask"), SERVE_MODES, "serve.prs"),
            max_prs_per_run=max_prs,
            labels=labels or [DEFAULT_LABEL],
            assignees=[str(a) for a in as_list(serve.get("assignees"))],
            token_env=str(serve.get("token_env") or "").strip(),
            publication_allow=parse_publication_allow(serve.get("publication_allow", [])),
        )
        trust = _required_mapping_section(data, "trust", path)
        _reject_unknown_keys(trust, {"same_owner", "owners", "bypass_license"}, path, "trust")
        config.trust = TrustSettings(
            same_owner=_strict_trust_bool(trust, "same_owner", True, path),
            owners=[
                str(name).strip() for name in as_list(trust.get("owners")) if str(name).strip()
            ],
            bypass_license=_strict_trust_bool(trust, "bypass_license", False, path),
        )
        attribution = data.get("attribution_file")
        if isinstance(attribution, str) and attribution.strip():
            config.attribution_file = attribution.strip()
        config.ledger = _choice(data.get("ledger", "repo"), LEDGER_MODES, "ledger mode")
        config.scoring = as_dict(data.get("scoring"))
        config.loop = LoopSettings.load(data.get("loop", {}))
        return config

    def ledger_path(self, cache_root: Path | None = None) -> Path | None:
        if self.ledger == "repo":
            return self.root / ".crab" / "ledger.json"
        if self.ledger == "cache":
            return maw_paths(self.root, cache_root).ledger_file
        return None

    def write_scoring(self, scoring: dict[str, Any]) -> Path:
        """Atomically replace only scoring, preserving the owner's surrounding policy."""
        data = dict(self.raw) if self.exists else as_dict(yaml.safe_load(DEFAULT_CONFIG_TEXT))
        data["scoring"] = scoring
        MawConfig.from_data(self.root, data)
        if self.exists and scoring == self.scoring:
            return self.path
        text = DEFAULT_CONFIG_TEXT
        if self.exists:
            with self.path.open(encoding="utf-8", newline="") as source:
                text = source.read()
        rendered = replace_section(text, "scoring", scoring)
        _validated_rewrite(self.root, rendered, data)
        atomic_text(self.path, rendered)
        self.raw = data
        self.scoring = scoring
        self.exists = True
        return self.path


def write_default_config(root: Path, *, force: bool = False, profile: str = "balanced") -> Path:
    path = root / CONFIG_FILE
    if path.exists() and not force:
        raise CrabError(f"{path} already exists", hint="pass --force to overwrite it")
    text = DEFAULT_CONFIG_TEXT.replace("profile: balanced", "profile: " + profile, 1)
    intended = as_dict(yaml.safe_load(text))
    intended["hunger"] = hunger_for(profile)
    text = replace_section(text, "hunger", hunger_for(profile))
    _validated_rewrite(root, text, intended)
    atomic_text(path, text)
    return path


def _validated_rewrite(root: Path, text: str, intended: dict[str, Any]) -> None:
    MawConfig.from_data(root, intended)
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UsageError(
            "configuration rewrite is not valid YAML", hint="edit scoring manually"
        ) from exc
    if not isinstance(loaded, dict):
        raise UsageError("configuration rewrite must remain a mapping")
    MawConfig.from_data(root, loaded)
    # Canonical YAML preserves the safe loader's scalar types (including dates), while
    # distinguishing booleans from equal-valued integers. No unsupported JSON conversion.
    if yaml.safe_dump(loaded, sort_keys=True) != yaml.safe_dump(intended, sort_keys=True):
        raise UsageError("configuration rewrite changed owner policy", hint="edit scoring manually")


def prey_owner(target: Target | Slug | None) -> str | None:
    """The GitHub account a prey belongs to, or None for a local directory."""
    if isinstance(target, Slug):
        return target.owner if target.host == "github.com" else f"{target.host}/{target.owner}"
    if isinstance(target, Target) and target.slug is not None:
        return prey_owner(target.slug)
    return None


def relationship_for(
    target: Target | Slug | None,
    config: MawConfig,
    *,
    maw_owner: str | None = None,
    repository: dict[str, Any] | None = None,
) -> Relationship:
    """Whether this prey is a stranger's code, our own, or something we stopped asking about.

    The license matrix answers "may we take this from someone else". It has no way to say that
    both repositories belong to the same person, and answering `IDEAS_ONLY` when a maintainer
    eats their own unlicensed repository is a wrong answer to a question nobody asked.

    Without repository metadata this is a preliminary account relationship. Sniff and compare
    resolve it again after fetching fork provenance; unknown final metadata cannot grant OWN.
    """
    if config.trust.bypass_license:
        return Relationship.BYPASS
    owner = prey_owner(target)
    if owner is None:
        return Relationship.FOREIGN
    mine = maw_owner
    if config.trust.same_owner and mine is None:
        mine = prey_owner(maw_slug(config.root))

    def trusted(account: str) -> bool:
        return config.trust.trusts(account) or bool(
            config.trust.same_owner and mine and mine.lower() == account.lower()
        )

    if not trusted(owner):
        return Relationship.FOREIGN
    if repository is not None:
        if repository.get("fork") is not False:
            if repository.get("fork") is not True:
                return Relationship.FOREIGN
            # GitHub's source is the ultimate upstream; parent alone can be another fork.
            upstreams = [repository.get("parent"), repository.get("source")]
            target_slug = target.slug if isinstance(target, Target) else target
            assert isinstance(target_slug, Slug)
            for upstream in upstreams:
                name = as_dict(upstream).get("full_name")
                if not isinstance(name, str) or not name:
                    return Relationship.FOREIGN
                try:
                    upstream_slug = Slug.parse(f"https://{target_slug.host}/{name}")
                except UsageError:
                    return Relationship.FOREIGN
                account = prey_owner(upstream_slug)
                if account is None or not trusted(account):
                    return Relationship.FOREIGN
        elif any(repository.get(key) is not None for key in ("parent", "source")):
            return Relationship.FOREIGN
    return Relationship.OWN


def maw_slug(root: Path) -> Slug | None:
    """The forge repository behind this worktree root's ``origin``, if there is one."""
    if not GitRunner.available():
        return None
    git = GitRunner(root)
    if not git.is_repo_root():
        return None
    url = git.try_run("remote", "get-url", "origin")
    if not url or not url.strip():
        return None
    try:
        return Slug.parse(url.strip())
    except UsageError:
        return None
