"""Traits miner: a flat matrix of boolean/enum/number traits derived from the other miners.

Traits are the currency of ``crab compare``: the maw's matrix minus the prey's matrix is the
first, fully deterministic list of candidate nutrients. Keys are stable identifiers.
"""

from __future__ import annotations

from typing import Any

from ..typeutil import as_dict
from .base import MineContext, MinerResult

LINTER_PACKAGES: dict[str, str] = {
    "eslint": "eslint",
    "biome": "biome",
    "@biomejs/biome": "biome",
    "oxlint": "oxlint",
    "stylelint": "stylelint",
    "markdownlint-cli": "markdownlint",
    "markdownlint-cli2": "markdownlint",
    "ruff": "ruff",
    "flake8": "flake8",
    "pylint": "pylint",
    "bandit": "bandit",
    "golangci-lint": "golangci-lint",
    "rubocop": "rubocop",
    "htmlhint": "htmlhint",
    "commitlint": "commitlint",
    "@commitlint/cli": "commitlint",
    "cspell": "cspell",
    "knip": "knip",
    "dependency-cruiser": "dependency-cruiser",
    "madge": "madge",
    "vulture": "vulture",
    "deptry": "deptry",
}
FORMATTER_PACKAGES: dict[str, str] = {
    "prettier": "prettier",
    "biome": "biome",
    "@biomejs/biome": "biome",
    "dprint": "dprint",
    "black": "black",
    "isort": "isort",
    "autopep8": "autopep8",
    "yapf": "yapf",
    "csharpier": "csharpier",
    "shfmt": "shfmt",
}
TYPE_CHECKER_PACKAGES: dict[str, str] = {
    "typescript": "typescript",
    "mypy": "mypy",
    "pyright": "pyright",
    "basedpyright": "basedpyright",
    "pyre-check": "pyre",
    "flow-bin": "flow",
    "ty": "ty",
}
LINTER_CONFIGS: dict[str, str] = {
    "eslint.config.js": "eslint",
    "eslint.config.mjs": "eslint",
    "eslint.config.cjs": "eslint",
    "eslint.config.ts": "eslint",
    ".eslintrc": "eslint",
    ".eslintrc.js": "eslint",
    ".eslintrc.cjs": "eslint",
    ".eslintrc.json": "eslint",
    ".eslintrc.yml": "eslint",
    "biome.json": "biome",
    "biome.jsonc": "biome",
    ".stylelintrc": "stylelint",
    ".stylelintrc.json": "stylelint",
    ".markdownlint.json": "markdownlint",
    ".markdownlint.yaml": "markdownlint",
    ".markdownlint-cli2.jsonc": "markdownlint",
    ".flake8": "flake8",
    ".pylintrc": "pylint",
    "ruff.toml": "ruff",
    ".ruff.toml": "ruff",
    ".golangci.yml": "golangci-lint",
    ".golangci.yaml": "golangci-lint",
    "clippy.toml": "clippy",
    ".rubocop.yml": "rubocop",
    "commitlint.config.js": "commitlint",
    "commitlint.config.mjs": "commitlint",
    ".commitlintrc.json": "commitlint",
    "cspell.json": "cspell",
    "knip.json": "knip",
}
FORMATTER_CONFIGS: dict[str, str] = {
    ".prettierrc": "prettier",
    ".prettierrc.json": "prettier",
    ".prettierrc.js": "prettier",
    ".prettierrc.cjs": "prettier",
    ".prettierrc.mjs": "prettier",
    ".prettierrc.yml": "prettier",
    ".prettierrc.yaml": "prettier",
    ".prettierrc.toml": "prettier",
    "prettier.config.js": "prettier",
    "prettier.config.mjs": "prettier",
    "prettier.config.cjs": "prettier",
    ".dprint.json": "dprint",
    "dprint.json": "dprint",
    "rustfmt.toml": "rustfmt",
    ".rustfmt.toml": "rustfmt",
    ".csharpierrc": "csharpier",
    ".csharpierrc.json": "csharpier",
    ".csharpierrc.yaml": "csharpier",
}
_TEST_TOOLS = {
    "pytest",
    "vitest",
    "jest",
    "mocha",
    "playwright",
    "cypress",
    "npm test",
    "dotnet test",
    "cargo test",
    "go test",
    "tox",
    "nox",
}
_LINT_TOOLS = {
    "ruff",
    "black",
    "flake8",
    "isort",
    "mypy",
    "pyright",
    "eslint",
    "prettier",
    "biome",
    "tsc",
    "dotnet format",
    "cargo clippy",
    "cargo fmt",
    "go vet",
    "golangci-lint",
    "markdownlint",
    "shellcheck",
    "actionlint",
    "pre-commit",
}
_COMPOSE_FILES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}


def _tool_names(
    all_deps: set[str], python_tools: set[str], file_names: set[str]
) -> dict[str, list[str]]:
    linters = {LINTER_PACKAGES[d] for d in all_deps if d in LINTER_PACKAGES}
    linters |= {LINTER_CONFIGS[n] for n in file_names if n in LINTER_CONFIGS}
    linters |= {t for t in ("ruff", "pylint", "flake8") if t in python_tools}
    formatters = {FORMATTER_PACKAGES[d] for d in all_deps if d in FORMATTER_PACKAGES}
    formatters |= {FORMATTER_CONFIGS[n] for n in file_names if n in FORMATTER_CONFIGS}
    formatters |= {t for t in ("black", "isort") if t in python_tools}
    if "ruff" in python_tools and "black" not in python_tools:
        formatters.add("ruff-format")
    checkers = {TYPE_CHECKER_PACKAGES[d] for d in all_deps if d in TYPE_CHECKER_PACKAGES}
    checkers |= {t for t in ("mypy", "pyright") if t in python_tools}
    if "tsconfig.json" in file_names:
        checkers.add("typescript")
    return {
        "linters": sorted(linters),
        "formatters": sorted(formatters),
        "type_checkers": sorted(checkers),
    }


class TraitsMiner:
    name = "traits"
    requires: tuple[str, ...] = (
        "inventory",
        "license",
        "deps",
        "ci",
        "testing",
        "docs",
        "ai_config",
        "history",
        "branches",
    )
    json_file = "traits.json"
    md_file = None

    def run(self, ctx: MineContext) -> MinerResult:
        inventory = ctx.data("inventory")
        license_data = ctx.data("license")
        deps = ctx.data("deps")
        ci = ctx.data("ci")
        features = ci["features"]
        testing = ctx.data("testing")
        docs = ctx.data("docs")
        ai = ctx.data("ai_config")
        history = ctx.data("history")
        branches = ctx.data("branches")
        files = ctx.files()
        names_by_eco: dict[str, list[str]] = ctx.extra("deps")["names"]
        all_deps = {name for names in names_by_eco.values() for name in names}
        file_names = {f.name for f in files if f.depth <= 1 and not f.vendored}
        root_names = set(ctx.root_entries())
        python_tools = set(deps.get("python_tools", []))
        repo_meta = as_dict(ctx.api.get("repo"))
        tools = _tool_names(all_deps, python_tools, file_names)
        if "dotnet format" in features["tools"]:
            tools["formatters"] = sorted({*tools["formatters"], "dotnet format"})

        tsconfig_strict: bool | None = None
        if "tsconfig.json" in root_names:
            compact = ctx.read("tsconfig.json").replace(" ", "").replace("\n", "")
            tsconfig_strict = '"strict":true' in compact
        npm_scripts = deps.get("npm_scripts", {})
        policies = deps.get("policies", {})
        readme = docs.get("readme") or {}
        tags = history.get("tags", {})
        community = docs.get("community", {})
        instruction_tools = {g["tool"] for g in ai["instruction_files"]}
        dependabot_updates = as_dict(ci.get("dependabot")).get("updates", [])
        ci_tools = set(features["tools"])
        workspace_manifest = any(
            m["path"].startswith(("packages/", "apps/", "crates/")) for m in inventory["manifests"]
        )

        traits: dict[str, Any] = {
            # identity
            "primary_language": inventory["primary_language"],
            "languages": list(inventory["languages"])[:6],
            "loc": inventory["loc"],
            "files": inventory["files_counted"],
            "files_all": inventory["files"],
            "ecosystems": deps["ecosystems"],
            "monorepo": bool(deps.get("workspaces")) or workspace_manifest,
            # license
            "has_license": bool(license_data["license_files"]),
            "license_spdx": license_data["spdx"],
            "license_class": license_data["class"],
            "license_human_review": license_data["human_review"],
            "has_notice_file": bool(license_data["notice_files"]),
            # ci
            "has_ci": features["provider"] is not None,
            "ci_provider": features["provider"],
            "ci_workflows": features["workflows"],
            "ci_cache": features["cache"],
            "ci_matrix": features["matrix"],
            "ci_concurrency": features["concurrency"],
            "ci_permissions": features["permissions_top_level"] or features["permissions_per_job"],
            "ci_timeouts": features["timeouts"],
            "ci_reusable_workflows": features["reusable_workflows"]
            or features["calls_reusable_workflows"],
            "ci_composite_actions": features["composite_actions"] > 0,
            "ci_schedule": features["schedule"],
            "ci_workflow_dispatch": features["workflow_dispatch"],
            "ci_release_automation": features["release_automation"],
            "ci_codeql": features["codeql"],
            "ci_security_scanning": features["security_scanning"],
            "ci_actions_sha_pinned_ratio": features["actions_sha_pinned_ratio"],
            "ci_windows_runner": features["windows_runner"],
            "ci_macos_runner": features["macos_runner"],
            "ci_tools": features["tools"],
            "ci_runs_tests": bool(ci_tools & _TEST_TOOLS),
            "ci_runs_lint": bool(ci_tools & _LINT_TOOLS),
            "ci_pipe_to_shell": features["pipe_to_shell_steps"] > 0,
            # tooling
            "has_dependabot": features["dependabot"],
            "dependabot_ecosystems": sorted(
                {str(u["ecosystem"]) for u in dependabot_updates if u.get("ecosystem")}
            ),
            "has_renovate": features["renovate"],
            "has_precommit": ".pre-commit-config.yaml" in root_names,
            "has_husky": ".husky" in root_names or "husky" in all_deps,
            "has_lint_staged": "lint-staged" in all_deps or ".lintstagedrc" in root_names,
            "has_commitlint": "commitlint" in tools["linters"],
            "has_editorconfig": ".editorconfig" in root_names,
            "has_gitattributes": inventory["flags"]["has_gitattributes"],
            "has_gitignore": inventory["flags"]["has_gitignore"],
            "has_devcontainer": ai["devcontainer"],
            "has_dockerfile": any(
                f.name in ("Dockerfile", "Containerfile") and f.depth <= 1 for f in files
            ),
            "has_docker_compose": any(f.name in _COMPOSE_FILES and f.depth == 0 for f in files),
            "has_makefile": "Makefile" in root_names or "GNUmakefile" in root_names,
            "has_justfile": "justfile" in root_names or "Justfile" in root_names,
            "has_taskfile": "Taskfile.yml" in root_names or "Taskfile.yaml" in root_names,
            "has_nvmrc": ".nvmrc" in root_names or ".node-version" in root_names,
            "has_python_version_file": ".python-version" in root_names,
            "has_tool_versions": bool(root_names & {".tool-versions", ".mise.toml", "mise.toml"}),
            "has_nix": "flake.nix" in root_names or "shell.nix" in root_names,
            "package_managers": {eco: p["package_manager"] for eco, p in policies.items()},
            "has_lockfile": bool(deps["lockfiles"]),
            "lockfiles": [lock["path"] for lock in deps["lockfiles"]],
            "deps_pinned_ratio": {eco: p["pinned_ratio"] for eco, p in policies.items()},
            "dependency_count": deps["package_count"],
            "linters": tools["linters"],
            "formatters": tools["formatters"],
            "type_checkers": tools["type_checkers"],
            "typescript_strict": tsconfig_strict,
            "npm_scripts": sorted(npm_scripts)[:30],
            "python_build_backend": deps.get("build_backend"),
            "requires_python": deps.get("requires_python"),
            "target_frameworks": deps.get("target_frameworks", []),
            # hygiene
            "has_readme": bool(readme),
            "readme_sections": readme.get("sections", []),
            "readme_badges": readme.get("badges", 0),
            "readme_words": readme.get("words", 0),
            "has_contributing": "contributing" in community,
            "has_code_of_conduct": "code_of_conduct" in community,
            "has_security_md": "security" in community,
            "has_support_md": "support" in community,
            "has_codeowners": docs["codeowners"] is not None,
            "has_changelog": docs["changelog"] is not None,
            "changelog_format": docs["changelog"]["format"] if docs["changelog"] else None,
            "has_issue_templates": bool(docs["issue_templates"]),
            "has_pr_template": docs["pr_template"] is not None,
            "has_funding": docs["funding"] is not None,
            "has_citation": "citation" in community,
            "has_adr": docs["adr"]["files"] > 0,
            "has_docs_site": docs["docs_site"] is not None,
            "docs_site": docs["docs_site"],
            "docs_dir": docs["docs_dir"],
            "semver_tags": bool(tags.get("semver_count")),
            "tag_count": tags.get("count", 0),
            "latest_tag": tags.get("latest"),
            "release_cadence_days": tags.get("release_cadence_days"),
            "releases_last_year": tags.get("releases_last_year", 0),
            "conventional_commits_ratio": history.get("conventional_commits_ratio"),
            "pr_style_commits_ratio": history.get("pr_style_ratio"),
            "fix_ratio": history.get("fix_ratio"),
            "revert_count": history.get("revert_count"),
            "bus_factor": history.get("bus_factor"),
            "authors": history.get("authors"),
            "commits": history.get("commits"),
            "commits_last_90d": history.get("commits_last_90d"),
            "active_last_90d": bool(history.get("commits_last_90d")),
            "age_days": history.get("age_days"),
            "stale_branches": branches.get("stale"),
            "active_unmerged_branches": branches.get("active_unmerged"),
            # tests
            "has_tests": testing["has_tests"],
            "test_files": testing["test_files"],
            "test_to_src_ratio": testing["test_to_src_ratio"],
            "test_frameworks": sorted(testing["frameworks"]),
            "has_e2e_tests": testing["special"]["e2e"],
            "has_property_tests": testing["special"]["property"],
            "has_snapshot_tests": testing["special"]["snapshot"],
            "has_fuzz_tests": testing["special"]["fuzz"],
            "has_mutation_tests": testing["special"]["mutation"],
            "has_benchmarks": testing["special"]["benchmarks"],
            "has_integration_tests": testing["special"]["integration"],
            "coverage_configured": testing["coverage"]["configured"],
            "coverage_threshold": testing["coverage"]["threshold"],
            "coverage_service": testing["coverage"]["service"],
            # ai
            "has_claude_md": "claude" in instruction_tools,
            "has_agents_md": "agents" in instruction_tools,
            "has_cursor_rules": "cursor" in instruction_tools or bool(ai["cursor_rules"]),
            "has_copilot_instructions": "copilot" in instruction_tools
            or bool(ai["copilot_instructions"]),
            "has_gemini_md": "gemini" in instruction_tools,
            "has_llms_txt": "llms" in instruction_tools,
            "has_claude_settings": bool(ai["settings"]),
            "has_skills": bool(ai["skills"]),
            "skills_count": len(ai["skills"]),
            "has_claude_agents": bool(ai["agents"]),
            "has_claude_hooks": bool(ai["hooks"])
            or any(g.get("hook_events") for g in ai["settings"]),
            "has_claude_commands": bool(ai["commands"]),
            "has_mcp_config": bool(ai["mcp"]),
            "has_claude_plugin": bool(ai["plugin"]),
            "ai_tools": ai["present"],
            # security
            "security_fix_commits": history.get("security_commit_count"),
            "suspicious_fragments": ai["suspicious_fragments"] + len(readme.get("suspicious", [])),
            # forge metadata (when sniffed)
            "stars": repo_meta.get("stargazers_count"),
            "forks": repo_meta.get("forks_count"),
            "open_issues": repo_meta.get("open_issues_count"),
            "archived": repo_meta.get("archived"),
            "is_fork": repo_meta.get("fork"),
            "topics": repo_meta.get("topics"),
            "has_wiki": repo_meta.get("has_wiki"),
            "has_discussions": repo_meta.get("has_discussions"),
            "default_branch": repo_meta.get("default_branch") or branches.get("default_branch"),
        }
        return MinerResult(self.name, {"schema": "hungry-crab.traits/1", "traits": traits})
