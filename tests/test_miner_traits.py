from __future__ import annotations

from helpers import read_json

from hungry_crab.digest import DigestResult


def test_npm_traits(npm_digest: DigestResult) -> None:
    traits = read_json(npm_digest, "traits.json")["traits"]
    expected = {
        "primary_language": "TypeScript",
        "ecosystems": ["npm"],
        "monorepo": False,
        "license_spdx": "MIT",
        "has_ci": True,
        "ci_cache": True,
        "ci_matrix": True,
        "ci_permissions": True,
        "ci_release_automation": True,
        "ci_runs_tests": True,
        "ci_windows_runner": True,
        "has_dependabot": True,
        "dependabot_ecosystems": ["github-actions", "npm"],
        "has_precommit": False,
        "has_editorconfig": True,
        "has_nvmrc": True,
        "package_managers": {"npm": "pnpm"},
        "has_lockfile": True,
        "linters": ["eslint"],
        "formatters": ["prettier"],
        "type_checkers": ["typescript"],
        "typescript_strict": True,
        "has_readme": True,
        "has_contributing": True,
        "has_security_md": False,
        "has_codeowners": False,
        "has_changelog": True,
        "changelog_format": "keep-a-changelog",
        "semver_tags": True,
        "tag_count": 4,
        "conventional_commits_ratio": 0.85,
        "bus_factor": 2,
        "active_last_90d": True,
        "stale_branches": 1,
        "has_tests": True,
        "has_e2e_tests": True,
        "has_property_tests": True,
        "coverage_configured": True,
        "coverage_threshold": None,
        "has_claude_md": True,
        "has_skills": True,
        "skills_count": 2,
        "has_claude_hooks": True,
        "has_mcp_config": False,
        "ai_tools": ["claude"],
        "stars": None,
    }
    for key, value in expected.items():
        assert traits[key] == value, key
    assert traits["suspicious_fragments"] >= 1, "the README injection comment"


def test_python_traits(py_digest: DigestResult) -> None:
    traits = read_json(py_digest, "traits.json")["traits"]
    expected = {
        "primary_language": "Python",
        "license_spdx": "Apache-2.0",
        "has_notice_file": True,
        "ci_cache": False,
        "ci_macos_runner": True,
        "ci_runs_lint": True,
        "has_dependabot": False,
        "has_precommit": True,
        "has_editorconfig": False,
        "has_python_version_file": True,
        "package_managers": {"python": "uv"},
        "python_build_backend": "hatchling.build",
        "linters": ["ruff"],
        "formatters": ["ruff-format"],
        "type_checkers": ["mypy"],
        "has_security_md": True,
        "has_codeowners": True,
        "changelog_format": "conventional-changelog",
        "has_issue_templates": True,
        "has_pr_template": True,
        "has_adr": True,
        "has_docs_site": True,
        "docs_site": "mkdocs",
        "coverage_threshold": 80,
        "has_agents_md": True,
        "has_cursor_rules": True,
        "has_claude_md": False,
        "ai_tools": ["agents", "cursor"],
        "conventional_commits_ratio": 0.6,
    }
    for key, value in expected.items():
        assert traits[key] == value, key


def test_dotnet_traits(dotnet_digest: DigestResult) -> None:
    traits = read_json(dotnet_digest, "traits.json")["traits"]
    expected = {
        "primary_language": "C#",
        "license_spdx": "GPL-3.0-only",
        "license_class": "gpl",
        "has_ci": True,
        "ci_matrix": False,
        "ci_cache": False,
        "target_frameworks": ["net8.0", "net9.0"],
        "has_benchmarks": True,
        "has_code_of_conduct": True,
        "has_contributing": False,
        "has_changelog": False,
        "has_editorconfig": True,
        "ai_tools": [],
        "has_claude_md": False,
        "conventional_commits_ratio": 0.0,
        "revert_count": 1,
        "semver_tags": True,
        "latest_tag": "1.1.0",
        "active_last_90d": False,
    }
    for key, value in expected.items():
        assert traits[key] == value, key
