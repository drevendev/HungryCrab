"""CI miner: GitHub Actions workflows (triggers, jobs, actions, caching, permissions) plus the
presence of other CI systems and dependency-update bots."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from ..mdutil import MdDoc
from ..typeutil import as_dict, as_list
from .base import MineContext, MinerResult

WORKFLOW_DIR = ".github/workflows/"
_ACTION_RE = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)(?P<path>/[^@\s]+)?@(?P<ref>\S+)$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_USES_COMMENT_RE = re.compile(r"uses:\s*['\"]?(\S+?)['\"]?\s*#\s*(v?\d[\w.-]*)")
_SECRET_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")

CACHE_ACTIONS = {
    "actions/cache",
    "actions/cache/restore",
    "actions/cache/save",
    "Swatinem/rust-cache",
    "gradle/actions/setup-gradle",
    "gradle/gradle-build-action",
}
SETUP_WITH_CACHE: dict[str, str] = {
    "actions/setup-node": "cache",
    "actions/setup-python": "cache",
    "actions/setup-java": "cache",
    "actions/setup-dotnet": "cache",
    "astral-sh/setup-uv": "enable-cache",
    "ruby/setup-ruby": "bundler-cache",
    "pdm-project/setup-pdm": "cache",
}
RELEASE_ACTIONS = {
    "softprops/action-gh-release",
    "googleapis/release-please-action",
    "google-github-actions/release-please-action",
    "changesets/action",
    "ncipollo/release-action",
    "pypa/gh-action-pypi-publish",
    "cycjimmy/semantic-release-action",
    "marvinpinto/action-automatic-releases",
    "release-drafter/release-drafter",
    "goreleaser/goreleaser-action",
    "JS-DevTools/npm-publish",
    "orhun/git-cliff-action",
}
CODEQL_ACTIONS = {"github/codeql-action/analyze", "github/codeql-action/init"}
SECURITY_ACTIONS = {
    "gitleaks/gitleaks-action",
    "zricethezav/gitleaks-action",
    "trufflesecurity/trufflehog",
    "aquasecurity/trivy-action",
    "snyk/actions",
    "ossf/scorecard-action",
    "actions/dependency-review-action",
    "step-security/harden-runner",
    "returntocorp/semgrep-action",
    "semgrep/semgrep-action",
}
TOOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("ruff", r"\bruff\b"),
        ("black", r"\bblack\b"),
        ("flake8", r"\bflake8\b"),
        ("isort", r"\bisort\b"),
        ("mypy", r"\bmypy\b"),
        ("pyright", r"\bpyright\b"),
        ("pytest", r"\bpytest\b"),
        ("tox", r"\btox\b"),
        ("nox", r"\bnox\b"),
        ("uv", r"\buv (?:sync|run|pip|lock|build|publish)\b"),
        ("eslint", r"\beslint\b"),
        ("prettier", r"\bprettier\b"),
        ("biome", r"\bbiome\b"),
        ("tsc", r"\btsc\b"),
        ("vitest", r"\bvitest\b"),
        ("jest", r"\bjest\b"),
        ("mocha", r"\bmocha\b"),
        ("playwright", r"\bplaywright\b"),
        ("cypress", r"\bcypress\b"),
        ("npm test", r"\b(?:npm|pnpm|yarn|bun) (?:run )?test\b"),
        ("npm build", r"\b(?:npm|pnpm|yarn|bun) (?:run )?build\b"),
        ("dotnet build", r"\bdotnet build\b"),
        ("dotnet test", r"\bdotnet test\b"),
        ("dotnet format", r"\bdotnet format\b"),
        ("dotnet pack", r"\bdotnet pack\b"),
        ("cargo test", r"\bcargo test\b"),
        ("cargo clippy", r"\bcargo clippy\b"),
        ("cargo fmt", r"\bcargo fmt\b"),
        ("go test", r"\bgo test\b"),
        ("go vet", r"\bgo vet\b"),
        ("golangci-lint", r"\bgolangci-lint\b"),
        ("markdownlint", r"\bmarkdownlint\b"),
        ("shellcheck", r"\bshellcheck\b"),
        ("actionlint", r"\bactionlint\b"),
        ("pre-commit", r"\bpre-commit\b"),
        ("codecov", r"\bcodecov\b"),
        ("coverage", r"\bcoverage\b|--cov\b"),
        ("docker build", r"\bdocker (?:build|buildx)\b"),
        ("pip-audit", r"\bpip-audit\b"),
        ("npm audit", r"\bnpm audit\b"),
        ("bandit", r"\bbandit\b"),
    )
)
# Marketplace actions that stand for a tool (matched by prefix on the action name).
ACTION_TOOLS: dict[str, str] = {
    "codecov/codecov-action": "codecov",
    "coverallsapp/github-action": "coveralls",
    "github/codeql-action": "codeql",
    "pre-commit/action": "pre-commit",
    "astral-sh/ruff-action": "ruff",
    "chartboost/ruff-action": "ruff",
    "golangci/golangci-lint-action": "golangci-lint",
    "super-linter/super-linter": "super-linter",
    "github/super-linter": "super-linter",
    "raven-actions/actionlint": "actionlint",
    "reviewdog/action-actionlint": "actionlint",
    "ludeeus/action-shellcheck": "shellcheck",
    "DavidAnson/markdownlint-cli2-action": "markdownlint",
    "hadolint/hadolint-action": "hadolint",
    "pypa/gh-action-pypi-publish": "pypi publish",
    "docker/build-push-action": "docker build",
}
OTHER_CI_FILES: dict[str, str] = {
    ".gitlab-ci.yml": "gitlab-ci",
    ".circleci/config.yml": "circleci",
    "azure-pipelines.yml": "azure-pipelines",
    "Jenkinsfile": "jenkins",
    ".travis.yml": "travis",
    "appveyor.yml": "appveyor",
    ".appveyor.yml": "appveyor",
    "bitbucket-pipelines.yml": "bitbucket",
    ".drone.yml": "drone",
    ".woodpecker.yml": "woodpecker",
    "cloudbuild.yaml": "cloud-build",
    ".buildkite/pipeline.yml": "buildkite",
}
_PIPE_TO_SHELL_RE = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash)\b")
_RUNNER_RE = re.compile(r"ubuntu|windows|macos", re.IGNORECASE)


def parse_action(uses: str) -> dict[str, Any]:
    text = uses.strip()
    if text.startswith("./"):
        return {"kind": "local", "action": text, "ref": None, "pinned_sha": False}
    if text.startswith("docker://"):
        return {"kind": "docker", "action": text, "ref": None, "pinned_sha": False}
    match = _ACTION_RE.match(text)
    if not match:
        return {"kind": "unknown", "action": text, "ref": None, "pinned_sha": False}
    action = match.group("repo") + (match.group("path") or "")
    ref = match.group("ref")
    return {
        "kind": "marketplace",
        "action": action,
        "ref": ref,
        "pinned_sha": bool(_SHA_RE.match(ref)),
    }


def _triggers(raw: object) -> tuple[list[str], dict[str, Any]]:
    details: dict[str, Any] = {}
    if isinstance(raw, str):
        return [raw], details
    if isinstance(raw, list):
        return [str(item) for item in raw], details
    if isinstance(raw, dict):
        names = [str(key) for key in raw]
        for key, value in raw.items():
            if isinstance(value, dict):
                summary: dict[str, Any] = {}
                for sub in (
                    "branches",
                    "tags",
                    "paths",
                    "types",
                    "paths-ignore",
                    "branches-ignore",
                ):
                    if sub in value:
                        summary[sub] = value[sub]
                if key == "schedule":
                    summary["cron"] = value
                if summary:
                    details[str(key)] = summary
            elif isinstance(value, list) and key == "schedule":
                details["schedule"] = [
                    item.get("cron") if isinstance(item, dict) else item for item in value
                ]
        return names, details
    return [], details


def _permissions(raw: object) -> dict[str, str] | str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return None


def _runs_on(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return ", ".join(str(item) for item in raw)
    if isinstance(raw, dict):
        return str(raw.get("labels") or raw.get("group") or "custom")
    return "?"


def _matrix(strategy: object) -> dict[str, Any] | None:
    if not isinstance(strategy, dict):
        return None
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return {"expression": str(matrix)[:80]} if matrix else None
    axes: dict[str, int] = {}
    size = 1
    for key, value in matrix.items():
        if key in ("include", "exclude"):
            continue
        count = len(value) if isinstance(value, list) else 1
        axes[str(key)] = count
        size *= count
    include = matrix.get("include")
    return {
        "axes": axes,
        "size": size + (len(include) if isinstance(include, list) else 0),
        "fail_fast": strategy.get("fail-fast"),
    }


def _step_cache(action: str, with_block: object) -> bool:
    if action in CACHE_ACTIONS:
        return True
    key = SETUP_WITH_CACHE.get(action)
    if key and isinstance(with_block, dict):
        value = with_block.get(key)
        return bool(value) and str(value).lower() not in ("false", "no", "off", "none")
    if action == "actions/setup-go":
        # setup-go v4+ caches by default unless `cache: false` is set explicitly.
        return not isinstance(with_block, dict) or (
            str(with_block.get("cache", "true")).lower() != "false"
        )
    return False


def parse_workflow(text: str, path: str) -> dict[str, Any] | None:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        return None
    raw_on = loaded.get("on", loaded.get(True))
    triggers, trigger_details = _triggers(raw_on)
    jobs_raw = loaded.get("jobs")
    jobs: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    tools: set[str] = set()
    cache = False
    reusable_calls = 0
    timeouts = 0
    job_permissions = 0
    pipe_to_shell = 0
    runners: list[str] = []
    if isinstance(jobs_raw, dict):
        for job_id, job in jobs_raw.items():
            if not isinstance(job, dict):
                continue
            steps = as_list(job.get("steps"))
            job_uses = job.get("uses")
            if isinstance(job_uses, str):
                reusable_calls += 1
            if job.get("timeout-minutes") is not None:
                timeouts += 1
            if job.get("permissions") is not None:
                job_permissions += 1
            runs_on = _runs_on(job.get("runs-on"))
            runners.append(runs_on)
            matrix_block = as_dict(as_dict(job.get("strategy")).get("matrix"))
            for values in matrix_block.values():
                runners.extend(
                    value
                    for value in as_list(values)
                    if isinstance(value, str) and _RUNNER_RE.search(value)
                )
            step_uses = 0
            step_runs = 0
            for step in steps:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses")
                if isinstance(uses, str):
                    step_uses += 1
                    parsed = parse_action(uses)
                    parsed["job"] = str(job_id)
                    actions.append(parsed)
                    tools.update(
                        tool
                        for prefix, tool in ACTION_TOOLS.items()
                        if parsed["action"].startswith(prefix)
                    )
                    if _step_cache(parsed["action"], step.get("with")):
                        cache = True
                run = step.get("run")
                if isinstance(run, str):
                    step_runs += 1
                    for name, pattern in TOOL_PATTERNS:
                        if pattern.search(run):
                            tools.add(name)
                    if _PIPE_TO_SHELL_RE.search(run):
                        pipe_to_shell += 1
            jobs.append(
                {
                    "id": str(job_id),
                    "name": str(job.get("name")) if job.get("name") else None,
                    "runs_on": runs_on,
                    "needs": job.get("needs") if isinstance(job.get("needs"), list | str) else None,
                    "matrix": _matrix(job.get("strategy")),
                    "uses": job_uses if isinstance(job_uses, str) else None,
                    "steps": len(steps),
                    "step_uses": step_uses,
                    "step_runs": step_runs,
                    "timeout_minutes": job.get("timeout-minutes"),
                    "has_permissions": job.get("permissions") is not None,
                    "container": bool(job.get("container")),
                    "services": len(job["services"])
                    if isinstance(job.get("services"), dict)
                    else 0,
                    "has_if": job.get("if") is not None,
                }
            )
    comment_versions = dict(_USES_COMMENT_RE.findall(text))
    for parsed in actions:
        if parsed["pinned_sha"]:
            full = f"{parsed['action']}@{parsed['ref']}"
            parsed["version_comment"] = comment_versions.get(full)
    concurrency = loaded.get("concurrency")
    concurrency_info: dict[str, Any] | None = None
    if isinstance(concurrency, dict):
        concurrency_info = {
            "group": str(concurrency.get("group", ""))[:80],
            "cancel_in_progress": bool(concurrency.get("cancel-in-progress")),
        }
    elif isinstance(concurrency, str):
        concurrency_info = {"group": concurrency[:80], "cancel_in_progress": False}
    env = loaded.get("env")
    workflow_name = loaded.get("name")
    return {
        "path": path,
        "name": str(workflow_name) if workflow_name else path.rsplit("/", 1)[-1],
        "triggers": triggers,
        "trigger_details": trigger_details,
        "reusable": "workflow_call" in triggers,
        "permissions": _permissions(loaded.get("permissions")),
        "concurrency": concurrency_info,
        "env": sorted(str(k) for k in env) if isinstance(env, dict) else [],
        "jobs": jobs,
        "actions": actions,
        "tools": sorted(tools),
        "cache": cache,
        "matrix": any(j["matrix"] for j in jobs),
        "reusable_calls": reusable_calls,
        "timeouts": timeouts,
        "job_permissions": job_permissions,
        "runners": sorted(set(runners)),
        "secrets": sorted(set(_SECRET_RE.findall(text))),
        "pipe_to_shell": pipe_to_shell,
    }


def _parse_dependabot(text: str) -> dict[str, Any] | None:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None
    updates = loaded.get("updates")
    entries: list[dict[str, Any]] = []
    if isinstance(updates, list):
        for item in updates:
            if isinstance(item, dict):
                schedule = as_dict(item.get("schedule"))
                entries.append(
                    {
                        "ecosystem": item.get("package-ecosystem"),
                        "directory": item.get("directory") or item.get("directories"),
                        "interval": schedule.get("interval"),
                        "groups": sorted(item["groups"])
                        if isinstance(item.get("groups"), dict)
                        else [],
                    }
                )
    return {"version": loaded.get("version"), "updates": entries}


class CiMiner:
    name = "ci"
    requires: tuple[str, ...] = ("inventory",)
    json_file = "ci.json"
    md_file = "ci.md"

    def run(self, ctx: MineContext) -> MinerResult:
        files = ctx.files()
        warnings: list[str] = []
        workflows: list[dict[str, Any]] = []
        for info in files:
            if not info.path.startswith(WORKFLOW_DIR) or info.ext not in {".yml", ".yaml"}:
                continue
            if info.path.count("/") != 2:
                continue
            text = ctx.read(info.path, limit=262_144)
            try:
                parsed = parse_workflow(text, info.path)
            except yaml.YAMLError as exc:
                warnings.append(f"{info.path}: YAML error ({type(exc).__name__})")
                continue
            if parsed is not None:
                workflows.append(parsed)
        composite_actions = sorted(
            f.path
            for f in files
            if f.path.startswith(".github/actions/") and f.name in {"action.yml", "action.yaml"}
        )
        other_ci = sorted(
            {provider for path, provider in OTHER_CI_FILES.items() if ctx.exists(path)}
        )
        dependabot = None
        for candidate in (".github/dependabot.yml", ".github/dependabot.yaml"):
            if ctx.exists(candidate):
                dependabot = _parse_dependabot(ctx.read(candidate))
                break
        renovate_files = [
            f.path
            for f in files
            if f.depth <= 1
            and (
                f.name
                in {
                    "renovate.json",
                    "renovate.json5",
                    ".renovaterc",
                    ".renovaterc.json",
                    ".renovaterc.json5",
                }
                or f.path == ".github/renovate.json"
            )
        ]
        renovate: dict[str, Any] | None = None
        if renovate_files:
            renovate = {"path": renovate_files[0], "extends": []}
            try:
                loaded = json.loads(ctx.read(renovate_files[0]))
                if isinstance(loaded, dict) and isinstance(loaded.get("extends"), list):
                    renovate["extends"] = [str(e) for e in loaded["extends"]][:10]
            except ValueError:
                pass

        action_index: dict[str, dict[str, Any]] = {}
        for wf in workflows:
            for action in wf["actions"]:
                if action["kind"] != "marketplace":
                    continue
                entry = action_index.setdefault(
                    action["action"], {"refs": set(), "count": 0, "pinned_sha": False}
                )
                entry["count"] += 1
                entry["refs"].add(action.get("version_comment") or action["ref"] or "")
                entry["pinned_sha"] = entry["pinned_sha"] or action["pinned_sha"]
        actions_summary = {
            name: {"refs": sorted(v["refs"]), "count": v["count"], "pinned_sha": v["pinned_sha"]}
            for name, v in sorted(action_index.items())
        }
        marketplace = [a for wf in workflows for a in wf["actions"] if a["kind"] == "marketplace"]
        pinned_ratio = (
            round(sum(1 for a in marketplace if a["pinned_sha"]) / len(marketplace), 2)
            if marketplace
            else None
        )
        used_actions = set(action_index)
        release = any(
            "release" in wf["triggers"] or ("tags" in wf["trigger_details"].get("push", {}))
            for wf in workflows
        ) or bool(used_actions & RELEASE_ACTIONS)
        features = {
            "provider": "github-actions" if workflows else (other_ci[0] if other_ci else None),
            "workflows": len(workflows),
            "cache": any(wf["cache"] for wf in workflows),
            "matrix": any(wf["matrix"] for wf in workflows),
            "concurrency": any(wf["concurrency"] for wf in workflows),
            "permissions_top_level": any(wf["permissions"] is not None for wf in workflows),
            "permissions_per_job": any(wf["job_permissions"] for wf in workflows),
            "timeouts": any(wf["timeouts"] for wf in workflows),
            "reusable_workflows": any(wf["reusable"] for wf in workflows),
            "calls_reusable_workflows": any(wf["reusable_calls"] for wf in workflows),
            "composite_actions": len(composite_actions),
            "schedule": any("schedule" in wf["triggers"] for wf in workflows),
            "workflow_dispatch": any("workflow_dispatch" in wf["triggers"] for wf in workflows),
            "release_automation": release,
            "codeql": bool(used_actions & CODEQL_ACTIONS),
            "security_scanning": sorted(used_actions & SECURITY_ACTIONS),
            "actions_sha_pinned_ratio": pinned_ratio,
            "windows_runner": any("windows" in r for wf in workflows for r in wf["runners"]),
            "macos_runner": any("macos" in r for wf in workflows for r in wf["runners"]),
            "tools": sorted({t for wf in workflows for t in wf["tools"]}),
            "secrets": sorted({s for wf in workflows for s in wf["secrets"]}),
            "pipe_to_shell_steps": sum(wf["pipe_to_shell"] for wf in workflows),
            "dependabot": dependabot is not None,
            "renovate": renovate is not None,
            "other_ci": other_ci,
        }
        if features["pipe_to_shell_steps"]:
            warnings.append("workflow steps pipe downloads into a shell")
        data: dict[str, Any] = {
            "features": features,
            "workflows": workflows,
            "actions": actions_summary,
            "composite_actions": composite_actions,
            "dependabot": dependabot,
            "renovate": renovate,
        }
        return MinerResult(self.name, data, doc=self._doc(ctx, data), warnings=warnings)

    def _doc(self, ctx: MineContext, data: dict[str, Any]) -> MdDoc:
        doc = MdDoc(f"CI: {ctx.label}", source=ctx.source_line())
        features = data["features"]
        summary = doc.section("Summary", priority=1)
        if not data["workflows"] and not features["other_ci"]:
            summary.para("No CI configuration found.")
        summary.kv(
            [
                ("Provider", features["provider"] or "none"),
                ("Workflows", features["workflows"]),
                ("Cache", features["cache"]),
                ("Matrix builds", features["matrix"]),
                ("Concurrency groups", features["concurrency"]),
                (
                    "Permissions declared",
                    features["permissions_top_level"] or features["permissions_per_job"],
                ),
                ("Job timeouts", features["timeouts"]),
                (
                    "Reusable workflows",
                    features["reusable_workflows"] or features["calls_reusable_workflows"],
                ),
                ("Composite actions", features["composite_actions"]),
                ("Scheduled runs", features["schedule"]),
                ("Release automation", features["release_automation"]),
                ("CodeQL", features["codeql"]),
                ("Security scanning", ", ".join(features["security_scanning"]) or "none"),
                (
                    "Actions pinned to SHA",
                    features["actions_sha_pinned_ratio"]
                    if features["actions_sha_pinned_ratio"] is not None
                    else "n/a",
                ),
                (
                    "Runners",
                    "windows "
                    + ("yes" if features["windows_runner"] else "no")
                    + ", macos "
                    + ("yes" if features["macos_runner"] else "no"),
                ),
                ("Tools run in CI", ", ".join(features["tools"]) or "none detected"),
                (
                    "Dependabot / Renovate",
                    (
                        f"{'yes' if features['dependabot'] else 'no'} / "
                        f"{'yes' if features['renovate'] else 'no'}"
                    ),
                ),
                ("Other CI systems", ", ".join(features["other_ci"]) or "none"),
            ]
        )
        if data["workflows"]:
            table = doc.section("Workflows", priority=1)
            table.table(
                ["Workflow", "Triggers", "Jobs", "Matrix", "Cache", "Permissions", "Concurrency"],
                (
                    [
                        wf["name"],
                        ", ".join(wf["triggers"]),
                        len(wf["jobs"]),
                        wf["matrix"],
                        wf["cache"],
                        "top"
                        if wf["permissions"] is not None
                        else ("job" if wf["job_permissions"] else "no"),
                        bool(wf["concurrency"]),
                    ]
                    for wf in data["workflows"]
                ),
                max_rows=25,
            )
            jobs = doc.section("Jobs", priority=3)
            jobs.table(
                ["Workflow", "Job", "Runs on", "Matrix", "Steps", "Timeout"],
                (
                    [
                        wf["name"],
                        job["name"] or job["id"],
                        job["runs_on"],
                        ", ".join(f"{k}x{v}" for k, v in job["matrix"]["axes"].items())
                        if job["matrix"] and "axes" in job["matrix"]
                        else "",
                        job["steps"],
                        job["timeout_minutes"] or "",
                    ]
                    for wf in data["workflows"]
                    for job in wf["jobs"]
                ),
                max_rows=40,
            )
            actions = doc.section("Actions used", priority=2)
            actions.table(
                ["Action", "Refs", "Uses", "SHA pinned"],
                (
                    [name, ", ".join(info["refs"]), info["count"], info["pinned_sha"]]
                    for name, info in data["actions"].items()
                ),
                max_rows=40,
            )
            if features["secrets"]:
                secrets = doc.section("Secrets referenced (names only)", priority=4)
                secrets.bullets(features["secrets"], max_items=20)
        if data["dependabot"]:
            bots = doc.section("Dependency updates", priority=3)
            bots.bullets(
                (
                    f"dependabot: {u['ecosystem']} in {u['directory']} ({u['interval'] or 'n/a'})"
                    + (f", groups: {', '.join(u['groups'])}" if u["groups"] else "")
                    for u in data["dependabot"]["updates"]
                ),
                max_items=15,
            )
        if data["renovate"]:
            bots = doc.section("Renovate", priority=3)
            extends = ", ".join(data["renovate"]["extends"]) or "default"
            bots.bullets([f"{data['renovate']['path']}: extends {extends}"])
        return doc
