"""Turn two digests (prey and host) into candidate nutrients. Facts only, no judgment."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..fs import read_text
from ..nutrients import Candidate, Evidence, slugify
from ..safety import is_suspicious
from ..typeutil import as_dict, as_list
from .rules import NOTABLE_DEPS, TEST_KIND_TRAITS, TOOL_ECOSYSTEM, TRAIT_RULES, TraitRule

MIN_CLUSTER = 5
MAX_ISSUE_CLUSTERS = 3
MAX_TOP_ISSUES = 3

# Tools a host uses through a config file rather than a pinned dependency. Without this the
# dependency diff proposes pre-commit to a repository whose .pre-commit-config.yaml is right
# there in the root.
TRAIT_IMPLIES_PACKAGE: dict[str, str] = {
    "has_precommit": "pre-commit",
    "has_husky": "husky",
    "has_commitlint": "commitlint",
    "has_renovate": "renovate",
    "coverage_configured": "pytest-cov",
    "has_benchmarks": "pytest-benchmark",
}

# A dependency that only exists to implement a nutrient already proposed as a trait. Proposing
# both puts the same change on the menu twice.
PACKAGE_IMPLEMENTS_KEY: dict[str, str] = {
    "pytest-cov": "tests.coverage",
    "coverage": "tests.coverage",
    "pre-commit": "tooling.pre-commit",
    "husky": "tooling.git-hooks",
    "hypothesis": "tests.property",
    "fast-check": "tests.property",
    "pytest-benchmark": "tests.bench",
    "syrupy": "tests.snapshot",
    "mutmut": "tests.mutation",
}

_DIGEST_FILES = {
    "traits": "traits.json",
    "deps": "deps.json",
    "history": "history.json",
    "testing": "tests.json",
    "ci": "ci.json",
    "docs": "docs.json",
    "ai": "ai.json",
    "license": "license.json",
    "issues": "issues.json",
    "architecture": "architecture.json",
    "manifest": "manifest.json",
}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(read_text(path, limit=50_000_000))
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


@dataclass
class Side:
    """One digest, loaded. ``root`` is the working tree (for evidence), when available."""

    label: str
    sha: str
    url: str | None
    root: Path | None
    traits: dict[str, Any] = field(default_factory=dict)
    deps: dict[str, Any] = field(default_factory=dict)
    history: dict[str, Any] = field(default_factory=dict)
    testing: dict[str, Any] = field(default_factory=dict)
    ci: dict[str, Any] = field(default_factory=dict)
    docs: dict[str, Any] = field(default_factory=dict)
    ai: dict[str, Any] = field(default_factory=dict)
    license: dict[str, Any] = field(default_factory=dict)
    issues: dict[str, Any] = field(default_factory=dict)
    architecture: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, digest_dir: Path, *, root: Path | None = None) -> Side:
        data = {name: _load(digest_dir / file_name) for name, file_name in _DIGEST_FILES.items()}
        manifest = data["manifest"]
        prey = as_dict(manifest.get("prey"))
        traits = as_dict(data["traits"].get("traits"))
        recorded_root = prey.get("root")
        side_root = root
        if side_root is None and isinstance(recorded_root, str) and Path(recorded_root).is_dir():
            side_root = Path(recorded_root)
        return cls(
            label=str(prey.get("label") or digest_dir.parent.name),
            sha=str(prey.get("sha") or ""),
            url=prey.get("url") if isinstance(prey.get("url"), str) else None,
            root=side_root,
            traits=traits,
            deps=data["deps"],
            history=data["history"],
            testing=data["testing"],
            ci=data["ci"],
            docs=data["docs"],
            ai=data["ai"],
            license=data["license"],
            issues=data["issues"],
            architecture=data["architecture"],
            manifest=manifest,
        )

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def ecosystems(self) -> set[str]:
        return {str(e) for e in as_list(self.traits.get("ecosystems"))}

    @property
    def spdx(self) -> str | None:
        value = self.license.get("spdx")
        return value if isinstance(value, str) else None

    def trait(self, name: str) -> Any:
        return self.traits.get(name)

    def blob_url(self, path: str) -> str | None:
        if self.url and self.sha and not self.sha.startswith("nogit-"):
            return f"{self.url}/blob/{self.sha}/{path}"
        return None

    def evidence(self, path: str, note: str = "") -> Evidence:
        return Evidence(path=path, url=self.blob_url(path), note=note)

    def find_files(self, pattern: str, *, limit: int = 3) -> list[str]:
        """Existing files in the working tree matching a glob, POSIX-relative."""
        if self.root is None:
            return []
        try:
            matches = sorted(p for p in self.root.glob(pattern) if p.is_file())
        except (OSError, ValueError):
            return []
        return [p.relative_to(self.root).as_posix() for p in matches[:limit]]


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return value > 0
    if isinstance(value, str | list | dict | tuple):
        return len(value) > 0
    return bool(value)


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "none"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value[:8]) or "none"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in list(value.items())[:8]) or "none"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class _Format(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "?"


def _fill(template: str, traits: dict[str, Any]) -> str:
    values = _Format({key: _fmt(value) for key, value in traits.items()})
    return template.format_map(values)


def _workflow_evidence(prey: Side, feature: str) -> list[Evidence]:
    workflows = as_list(prey.ci.get("workflows"))
    found: list[Evidence] = []
    for workflow in workflows:
        wf = as_dict(workflow)
        if _workflow_has(wf, feature):
            found.append(prey.evidence(str(wf.get("path", "")), note=str(wf.get("name", ""))))
        if len(found) >= 3:
            break
    return found


def _workflow_has(wf: dict[str, Any], feature: str) -> bool:
    triggers = {str(t) for t in as_list(wf.get("triggers"))}
    tools = {str(t) for t in as_list(wf.get("tools"))}
    actions = {str(as_dict(a).get("action", "")) for a in as_list(wf.get("actions"))}
    runners = " ".join(str(r) for r in as_list(wf.get("runners"))).lower()
    name = f"{wf.get('name', '')} {wf.get('path', '')}".lower()
    if feature in ("cache", "matrix", "timeouts", "reusable"):
        return _truthy(wf.get(feature))
    if feature == "concurrency":
        return wf.get("concurrency") is not None
    if feature == "permissions":
        return wf.get("permissions") is not None or _truthy(wf.get("job_permissions"))
    if feature == "release":
        details = as_dict(wf.get("trigger_details"))
        return (
            "release" in triggers
            or "tags" in as_dict(details.get("push"))
            or "release" in name
            or "publish" in name
        )
    if feature in ("schedule", "workflow_dispatch"):
        return feature in triggers
    if feature in ("windows", "macos"):
        return feature in runners
    if feature == "codeql":
        return any(a.startswith("github/codeql-action") for a in actions)
    if feature == "security":
        return "codeql" in tools or any(
            a.startswith(("gitleaks/", "trufflesecurity/", "aquasecurity/", "ossf/", "snyk/"))
            for a in actions
        )
    if feature == "tests":
        return bool(
            tools
            & {"pytest", "vitest", "jest", "mocha", "playwright", "cypress", "npm test",
               "dotnet test", "cargo test", "go test", "tox", "nox"}
        )  # fmt: skip
    if feature == "lint":
        return bool(
            tools
            & {"ruff", "black", "flake8", "isort", "mypy", "pyright", "eslint", "prettier",
               "biome", "tsc", "dotnet format", "cargo clippy", "cargo fmt", "go vet",
               "golangci-lint", "pre-commit"}
        )  # fmt: skip
    return False


def _rule_evidence(rule: TraitRule, prey: Side) -> list[Evidence]:
    if rule.evidence.startswith("workflows:"):
        return _workflow_evidence(prey, rule.evidence.split(":", 1)[1])
    if rule.evidence.startswith("files:"):
        return [prey.evidence(path) for path in prey.find_files(rule.evidence.split(":", 1)[1])]
    return []


def applicability_kind(prey: Side, host: Side, *, stack_bound: bool) -> str:
    if not stack_bound:
        return "same_stack"
    return "same_stack" if prey.ecosystems & host.ecosystems else "other_stack"


def trait_rule_candidates(prey: Side, host: Side) -> list[Candidate]:
    out: list[Candidate] = []
    for rule in TRAIT_RULES:
        prey_value = prey.trait(rule.trait)
        host_value = host.trait(rule.trait)
        if not _truthy(prey_value) or _truthy(host_value):
            continue
        if any(not _truthy(host.trait(name)) for name in rule.needs_host):
            continue
        if rule.stack_bound and not (prey.ecosystems & host.ecosystems):
            continue
        out.append(
            Candidate(
                category=rule.category,
                key=rule.key,
                title=rule.title,
                what=f"{prey.label} {_fill(rule.what, prey.traits)}",
                prey_state=_fmt(prey_value),
                host_state=_fmt(host_value),
                artifact=rule.artifact,
                effort=rule.effort,
                risk=rule.risk,
                value=rule.value,
                evidence=_rule_evidence(rule, prey),
                tags=[rule.trait],
            )
        )
    return out


def tool_candidates(prey: Side, host: Side) -> list[Candidate]:
    out: list[Candidate] = []
    for kind, title in (
        ("linters", "Adopt the linter {tool}"),
        ("formatters", "Adopt the formatter {tool}"),
        ("type_checkers", "Adopt the type checker {tool}"),
    ):
        prey_tools = {str(t) for t in as_list(prey.trait(kind))}
        host_tools = {str(t) for t in as_list(host.trait(kind))}
        if host_tools:
            # The host already has a tool of this kind, so the prey's is an alternative, not a
            # gap. Swapping mypy for ty is a decision, not a nutrient, and proposing it buries
            # the candidates that fill an actual hole.
            continue
        for tool in sorted(prey_tools):
            ecosystem = TOOL_ECOSYSTEM.get(tool)
            if ecosystem is None or ecosystem not in host.ecosystems:
                continue
            out.append(
                Candidate(
                    category="tooling",
                    key=f"tooling.{kind[:-1] if kind.endswith('s') else kind}.{slugify(tool)}",
                    title=title.format(tool=tool),
                    what=f"{prey.label} uses {tool} ({kind.replace('_', ' ')})",
                    prey_state=", ".join(sorted(prey_tools)),
                    host_state="none",
                    artifact="pr",
                    effort="M" if kind == "type_checkers" else "S",
                    risk="low",
                    value=0.7,
                    tags=[kind, tool],
                )
            )
    prey_strict = prey.trait("typescript_strict")
    host_strict = host.trait("typescript_strict")
    if prey_strict is True and host_strict is False:
        out.append(
            Candidate(
                category="tooling",
                key="tooling.typescript-strict",
                title="Enable TypeScript strict mode",
                what=f"{prey.label} compiles with strict: true",
                prey_state="strict",
                host_state="not strict",
                artifact="pr",
                effort="M",
                risk="medium",
                value=0.6,
                evidence=[prey.evidence("tsconfig.json")],
                tags=["typescript_strict"],
            )
        )
    task_runners = ("has_makefile", "has_justfile", "has_taskfile")
    if any(_truthy(prey.trait(t)) for t in task_runners) and not any(
        _truthy(host.trait(t)) for t in task_runners
    ):
        which = [t.removeprefix("has_") for t in task_runners if _truthy(prey.trait(t))]
        out.append(
            Candidate(
                category="tooling",
                key="tooling.task-runner",
                title="Add a task runner for common commands",
                what=f"{prey.label} uses {', '.join(which)}",
                prey_state=", ".join(which),
                host_state="none",
                artifact="issue",
                effort="S",
                risk="low",
                value=0.4,
                tags=list(which),
            )
        )
    pins = {
        "has_nvmrc": ("npm", "tooling.node-version-file", "Pin the Node version with .nvmrc"),
        "has_python_version_file": (
            "python", "tooling.python-version-file", "Pin the Python version with .python-version"
        ),
        "has_tool_versions": ("*", "tooling.tool-versions", "Pin tool versions with mise or asdf"),
    }  # fmt: skip
    for trait, (ecosystem, key, title) in pins.items():
        if not _truthy(prey.trait(trait)) or _truthy(host.trait(trait)):
            continue
        if ecosystem != "*" and ecosystem not in host.ecosystems:
            continue
        out.append(
            Candidate(
                category="tooling",
                key=key,
                title=title,
                what=f"{prey.label} pins its runtime version ({trait.removeprefix('has_')})",
                prey_state="yes",
                host_state="no",
                artifact="pr",
                effort="S",
                risk="low",
                value=0.4,
                tags=[trait],
            )
        )
    return out


def readme_candidates(prey: Side, host: Side) -> list[Candidate]:
    if not _truthy(host.trait("has_readme")) or not _truthy(prey.trait("has_readme")):
        return []
    prey_sections = {str(s) for s in as_list(prey.trait("readme_sections"))}
    host_sections = {str(s) for s in as_list(host.trait("readme_sections"))}
    missing = sorted(prey_sections - host_sections)
    out: list[Candidate] = []
    if len(missing) >= 2:
        out.append(
            Candidate(
                category="hygiene",
                key="hygiene.readme-sections",
                title=f"README: add {', '.join(missing)} sections",
                what=f"{prey.label}'s README covers {', '.join(sorted(prey_sections))}",
                prey_state=", ".join(sorted(prey_sections)),
                host_state=", ".join(sorted(host_sections)) or "none",
                artifact="pr",
                effort="S",
                risk="low",
                value=0.5,
                evidence=[prey.evidence(path) for path in prey.find_files("README*", limit=1)],
                tags=missing,
            )
        )
    prey_badges = prey.trait("readme_badges")
    if (
        isinstance(prey_badges, int)
        and prey_badges >= 2
        and not _truthy(host.trait("readme_badges"))
    ):
        out.append(
            Candidate(
                category="hygiene",
                key="hygiene.readme-badges",
                title="README: add status badges",
                what=f"{prey.label}'s README shows {prey_badges} badges",
                prey_state=str(prey_badges),
                host_state="0",
                artifact="pr",
                effort="S",
                risk="low",
                value=0.3,
                tags=["readme_badges"],
            )
        )
    return out


def test_candidates(prey: Side, host: Side) -> list[Candidate]:
    out: list[Candidate] = []
    if not _truthy(host.trait("has_tests")):
        return out
    frameworks = as_dict(prey.testing.get("frameworks"))
    same_stack = bool(prey.ecosystems & host.ecosystems)
    for trait, (kind, effort, title) in TEST_KIND_TRAITS.items():
        if not _truthy(prey.trait(trait)) or _truthy(host.trait(trait)):
            continue
        used = sorted(name for name, k in frameworks.items() if k == kind)
        out.append(
            Candidate(
                category="tests",
                key=f"tests.{kind}",
                title=title,
                what=f"{prey.label} has {kind} tests" + (f" ({', '.join(used)})" if used else ""),
                prey_state="yes",
                host_state="no",
                artifact="issue",
                effort=effort,
                risk="medium" if kind in ("e2e", "mutation") else "low",
                value=0.7 if kind in ("e2e", "property", "integration") else 0.5,
                applicability=1.0 if same_stack else 0.6,
                tags=[trait, *used],
            )
        )
    prey_threshold = prey.trait("coverage_threshold")
    if (
        isinstance(prey_threshold, int)
        and _truthy(host.trait("coverage_configured"))
        and not _truthy(host.trait("coverage_threshold"))
    ):
        out.append(
            Candidate(
                category="tests",
                key="tests.coverage-threshold",
                title=f"Fail CI below a coverage threshold ({prey_threshold}%)",
                what=f"{prey.label} enforces {prey_threshold}% coverage",
                prey_state=f"{prey_threshold}%",
                host_state="coverage measured, no threshold",
                artifact="pr",
                effort="S",
                risk="low",
                value=0.6,
                tags=["coverage_threshold"],
            )
        )
    return out


def commit_candidates(prey: Side, host: Side) -> list[Candidate]:
    out: list[Candidate] = []
    host_commits = host.trait("commits")
    if not isinstance(host_commits, int) or host_commits < 10:
        return out
    prey_ratio = prey.trait("conventional_commits_ratio")
    host_ratio = host.trait("conventional_commits_ratio")
    if (
        isinstance(prey_ratio, float)
        and prey_ratio >= 0.7
        and isinstance(host_ratio, float)
        and host_ratio < 0.4
    ):
        out.append(
            Candidate(
                category="hygiene",
                key="hygiene.conventional-commits",
                title="Adopt Conventional Commits",
                what=f"{prey.label} writes {prey_ratio * 100:.0f}% conventional commit subjects",
                prey_state=f"{prey_ratio * 100:.0f}%",
                host_state=f"{host_ratio * 100:.0f}%",
                artifact="issue",
                effort="S",
                risk="low",
                value=0.6,
                tags=["conventional_commits_ratio"],
            )
        )
    if _truthy(prey.trait("semver_tags")) and not _truthy(host.trait("semver_tags")):
        latest = prey.trait("latest_tag")
        out.append(
            Candidate(
                category="hygiene",
                key="hygiene.semver-tags",
                title="Tag releases with semantic versions",
                what=(
                    f"{prey.label} tags releases ({prey.trait('tag_count')} tags, latest {latest})"
                ),
                prey_state=str(latest),
                host_state="no semver tags",
                artifact="issue",
                effort="S",
                risk="low",
                value=0.5,
                tags=["semver_tags"],
            )
        )
    return out


def changelog_candidates(prey: Side, host: Side) -> list[Candidate]:
    structured = {
        "keep-a-changelog",
        "conventional-changelog",
        "towncrier",
        "changesets",
        "git-cliff",
    }
    prey_format = prey.trait("changelog_format")
    host_format = host.trait("changelog_format")
    if (
        isinstance(prey_format, str)
        and prey_format in structured
        and isinstance(host_format, str)
        and host_format not in structured
    ):
        return [
            Candidate(
                category="hygiene",
                key="hygiene.changelog-format",
                title=f"Structure the changelog ({prey_format})",
                what=f"{prey.label} keeps a {prey_format} changelog",
                prey_state=prey_format,
                host_state=host_format,
                artifact="issue",
                effort="S",
                risk="low",
                value=0.4,
                evidence=[prey.evidence(p) for p in prey.find_files("CHANGELOG*", limit=1)],
                tags=["changelog_format"],
            )
        ]
    return []


def deps_candidates(prey: Side, host: Side) -> tuple[list[Candidate], dict[str, list[str]]]:
    """Notable dependencies get their own card; everything else is one grouped idea card."""
    out: list[Candidate] = []
    only_in_prey: dict[str, list[str]] = {}
    prey_packages = [as_dict(p) for p in as_list(prey.deps.get("packages"))]
    host_names = {
        (str(p.get("ecosystem")), str(p.get("name")).lower())
        for p in (as_dict(x) for x in as_list(host.deps.get("packages")))
    }
    host_tools = {
        str(t).lower()
        for kind in ("linters", "formatters", "type_checkers", "test_frameworks")
        for t in as_list(host.trait(kind))
    }
    # A tool configured by a file rather than pinned as a dependency is still in use here.
    host_tools |= {
        package for trait, package in TRAIT_IMPLIES_PACKAGE.items() if _truthy(host.trait(trait))
    }
    for ecosystem in sorted(prey.ecosystems & host.ecosystems):
        names = sorted(
            {
                str(p.get("name"))
                for p in prey_packages
                if p.get("ecosystem") == ecosystem
                and p.get("kind") not in ("central", "indirect")
                and (ecosystem, str(p.get("name")).lower()) not in host_names
            }
        )
        if not names:
            continue
        only_in_prey[ecosystem] = names
        notable = NOTABLE_DEPS.get(ecosystem, {})
        rest: list[str] = []
        for name in names:
            description = notable.get(name)
            if description is None or name.lower() in host_tools:
                rest.append(name)
                continue
            out.append(
                Candidate(
                    category="deps",
                    key=f"deps.{ecosystem}.{slugify(name)}",
                    title=f"Consider {name} ({description})",
                    what=f"{prey.label} depends on {name}: {description}",
                    prey_state=name,
                    host_state="not used",
                    artifact="issue",
                    effort="S",
                    risk="low",
                    value=0.5,
                    tags=[ecosystem, name],
                )
            )
        if rest:
            out.append(
                Candidate(
                    category="deps",
                    key=f"deps.{ecosystem}.others",
                    title=f"{len(rest)} {ecosystem} dependencies the prey uses and you do not",
                    what=f"{prey.label} also uses: {', '.join(rest[:30])}"
                    + (" ..." if len(rest) > 30 else ""),
                    prey_state=f"{len(rest)} packages",
                    host_state="not used",
                    artifact="idea",
                    effort="M",
                    risk="low",
                    value=0.3,
                    tags=[ecosystem],
                )
            )
    return out, only_in_prey


def history_candidates(prey: Side, host: Side) -> list[Candidate]:
    if not _truthy(prey.history.get("available")):
        return []
    slug = slugify(prey.label)
    out: list[Candidate] = []
    fix_prone = [
        as_dict(f)
        for f in as_list(prey.history.get("fix_prone"))
        if as_dict(f).get("commits", 0) >= 10 and as_dict(f).get("fix_ratio", 0) > 0.3
    ][:5]
    reverts = prey.history.get("revert_count")
    if fix_prone or (isinstance(reverts, int) and reverts >= 3):
        lines = [f"{f['path']} ({f['fixes']}/{f['commits']} fix commits)" for f in fix_prone]
        what = f"{prey.label} has fix-prone areas: " + "; ".join(lines) if lines else ""
        if isinstance(reverts, int) and reverts >= 3:
            what = (what + "; " if what else f"{prey.label} ") + f"{reverts} reverts in history"
        out.append(
            Candidate(
                category="history-lesson",
                key=f"history-lesson.{slug}.fix-prone",
                title=f"Lessons from {prey.label}'s history: fix-prone areas",
                what=what,
                prey_state=f"{len(fix_prone)} fix-prone files, {reverts} reverts",
                host_state="n/a (lesson, not a gap)",
                artifact="issue",
                effort="M",
                risk="low",
                value=0.6,
                evidence=[prey.evidence(str(f["path"])) for f in fix_prone[:3]],
                tags=["history"],
            )
        )
    security = prey.history.get("security_commit_count")
    if isinstance(security, int) and security >= 3:
        out.append(
            Candidate(
                category="security",
                key=f"security.{slug}.history",
                title=f"Security fixes in {prey.label}'s history ({security})",
                what=(
                    f"{prey.label} has {security} security-related commits; "
                    "their subjects are in history.json"
                ),
                prey_state=str(security),
                host_state="n/a (lesson, not a gap)",
                artifact="idea",
                effort="M",
                risk="low",
                value=0.6,
                tags=["security", "history"],
            )
        )
    return out


def issue_candidates(prey: Side, host: Side) -> list[Candidate]:
    """Issue lessons are the noisiest category, so only the strongest few are proposed.

    An unbounded list of term-cluster candidates floods the menu: the first live meal produced
    thirteen of twenty-four, all scored identically, all about the prey's own users.
    """
    if not _truthy(prey.issues.get("available")):
        return []
    slug = slugify(prey.label)
    out: list[Candidate] = []
    clusters = [as_dict(c) for c in as_list(prey.issues.get("clusters"))]
    strong = [c for c in clusters if isinstance(c.get("size"), int) and c["size"] >= MIN_CLUSTER]
    strong.sort(key=lambda c: (-int(c["size"]), -int(c.get("reactions") or 0)))
    for index, data in enumerate(strong[:MAX_ISSUE_CLUSTERS], start=1):
        size = int(data["size"])
        terms = ", ".join(str(t) for t in as_list(data.get("terms"))[:5])
        samples = [str(t) for t in as_list(data.get("sample_titles")) if str(t).strip()]
        headline = samples[0][:70] if samples else terms
        out.append(
            Candidate(
                category="issue-lesson",
                key=f"issue-lesson.{slug}.cluster-{index}",
                title=f"Recurring pain in {prey.label}: {headline}",
                what=f"{size} issues cluster around {terms}; the largest is: {headline}",
                prey_state=f"{size} issues, {data.get('reactions') or 0} reactions",
                host_state="n/a (lesson, not a gap)",
                artifact="idea",
                effort="M",
                risk="low",
                value=0.5,
                tags=["issues"],
            )
        )
    for top in as_list(prey.issues.get("top_by_reactions"))[:MAX_TOP_ISSUES]:
        data = as_dict(top)
        reactions = data.get("reactions")
        title = str(data.get("title", ""))
        if not isinstance(reactions, int) or reactions < 20 or is_suspicious(title):
            continue
        number = data.get("number")
        out.append(
            Candidate(
                category="issue-lesson",
                key=f"issue-lesson.{slug}.top-{number}",
                title=f"Popular request in {prey.label}: {title[:80]}",
                what=f"issue #{number} has {reactions} reactions",
                prey_state=f"{reactions} reactions",
                host_state="n/a (lesson, not a gap)",
                artifact="idea",
                effort="M",
                risk="low",
                value=0.5,
                evidence=[
                    Evidence(path=f"issue #{number}", url=str(data.get("url") or "") or None)
                ],
                tags=["issues"],
            )
        )
    return out


def architecture_candidates(prey: Side, host: Side) -> list[Candidate]:
    if not _truthy(prey.architecture.get("available")):
        return []
    graph = as_dict(prey.architecture.get("graph"))
    hubs = [as_dict(h) for h in as_list(graph.get("hubs"))[:5]]
    if not hubs:
        return []
    return [
        Candidate(
            category="architecture",
            key=f"architecture.{slugify(prey.label)}.raw",
            title=f"Architecture of {prey.label}: hubs and layering (raw material)",
            what="import-graph hubs: " + ", ".join(str(h.get("path")) for h in hubs),
            prey_state=f"{len(hubs)} hubs",
            host_state="n/a (raw material for the architect)",
            artifact="idea",
            effort="L",
            risk="low",
            value=0.4,
            evidence=[prey.evidence(str(h.get("path"))) for h in hubs[:3]],
            tags=["architecture"],
        )
    ]


def build_candidates(prey: Side, host: Side) -> tuple[list[Candidate], dict[str, Any]]:
    """All candidates (deduplicated by id) plus extra facts for gap.md."""
    deps, only_in_prey = deps_candidates(prey, host)
    groups = [
        trait_rule_candidates(prey, host),
        tool_candidates(prey, host),
        readme_candidates(prey, host),
        test_candidates(prey, host),
        commit_candidates(prey, host),
        changelog_candidates(prey, host),
        deps,
        history_candidates(prey, host),
        issue_candidates(prey, host),
        architecture_candidates(prey, host),
    ]
    seen: set[str] = set()
    out: list[Candidate] = []
    for group in groups:
        for candidate in group:
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            out.append(candidate)
    keys = {candidate.key for candidate in out}
    out = [c for c in out if _implemented_elsewhere(c) not in keys]
    return out, {"deps_only_in_prey": only_in_prey}


def _implemented_elsewhere(candidate: Candidate) -> str | None:
    """The trait key a dependency card merely implements, if there is one."""
    if candidate.category != "deps" or not candidate.tags:
        return None
    return PACKAGE_IMPLEMENTS_KEY.get(candidate.tags[-1].lower())
