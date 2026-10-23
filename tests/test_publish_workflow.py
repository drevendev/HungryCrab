"""#395: PyPI publication from the release tag, and install instructions that name the package."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from hungry_crab import updater

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
SHA_PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")
# An install command a reader is told to run, and its requirement (quoted or bare).
INSTALL = re.compile(
    r"(?:uv tool|pipx) install\s+(?:--?[\w-]+\s+)*(?P<requirement>\"[^\"]+\"|'[^']+'|[^\s`]+)"
)


def _normalized(name: str) -> str:
    """A distribution name as PyPI compares it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _distribution() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["name"])


def _workflow() -> dict[Any, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _check_release_tag() -> ModuleType:
    script = ROOT / "scripts" / "check_release_tag.py"
    spec = importlib.util.spec_from_file_location("check_release_tag", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("document", ["README.md", "docs/everywhere.md"])
def test_install_instructions_name_the_declared_distribution(document: str) -> None:
    text = (ROOT / document).read_text(encoding="utf-8")
    distribution = _normalized(_distribution())
    requirements = [match.group("requirement").strip("\"'") for match in INSTALL.finditer(text)]
    named = [
        requirement
        for requirement in requirements
        if not requirement.startswith(("./", "/", "file:"))  # a wheel the reader built
    ]
    assert named, f"{document} tells nobody how to install the CLI"
    for requirement in named:
        name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        assert name is not None and _normalized(name.group(0)) == distribution, requirement
    from_index = [requirement for requirement in named if "@" not in requirement]
    from_git = [requirement for requirement in named if "@ git+" in requirement]
    assert from_index, "the released package on PyPI is the stable path"
    assert from_git, "the git URL is the way to track master"
    assert "not on PyPI" not in text and "not published to PyPI" not in text
    assert "is not ours" not in text


def test_the_updater_names_the_declared_distribution() -> None:
    distribution = _distribution()
    assert distribution == updater.DISTRIBUTION
    assert f"{distribution} @ git+{updater.GIT_URL}" == updater.REQUIREMENT
    assert f"https://pypi.org/pypi/{distribution}/json" == updater.PYPI_JSON_URL
    assert updater.upgrade_command("uv-tool") == ["uv", "tool", "upgrade", distribution]


def test_publish_runs_on_a_published_release_and_keeps_no_secret() -> None:
    workflow = _workflow()
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["release"] == {"types": ["published"]}
    assert triggers["workflow_dispatch"]["inputs"]["tag"]["required"] is True
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text and "password" not in text


def test_only_the_publish_job_can_request_an_oidc_token() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert set(jobs) == {"build", "publish"}
    assert "permissions" not in jobs["build"]
    publish = jobs["publish"]
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    assert publish["needs"] == "build"
    environment = publish["environment"]
    assert (environment["name"] if isinstance(environment, dict) else environment) == "pypi"
    assert WORKFLOW.read_text(encoding="utf-8").count("id-token") == 1


def test_the_publish_job_runs_no_code() -> None:
    steps = _workflow()["jobs"]["publish"]["steps"]
    uses = [step["uses"] for step in steps]
    assert any(action.startswith("pypa/gh-action-pypi-publish@") for action in uses)
    assert all("run" not in step for step in steps), "the job holding the token runs no code"


def test_every_action_is_pinned_by_commit_with_its_release() -> None:
    """What the workflow builds is the release, so no moved tag may change any step of it."""
    jobs = _workflow()["jobs"]
    uses = [step["uses"] for job in jobs.values() for step in job["steps"] if "uses" in step]
    assert len(uses) >= 5
    assert all(SHA_PINNED.fullmatch(action) for action in uses), uses
    lines = [line.strip() for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    pinned = [line for line in lines if line.startswith("- uses:")]
    assert len(pinned) == len(uses)
    for line in pinned:
        assert re.fullmatch(r"- uses: \S+@[0-9a-f]{40} # v\d+\.\d+\.\d+", line), line


def test_the_build_job_checks_the_tag_before_it_builds() -> None:
    build = _workflow()["jobs"]["build"]
    steps = build["steps"]
    checkout = steps[0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "refs/tags/${{ env.TAG }}"
    assert checkout["with"]["persist-credentials"] is False
    runs = [str(step.get("run", "")) for step in steps]
    # The tag reaches the scripts through the environment, never spliced into a command line.
    assert all("${{" not in run for run in runs)

    def position(needle: str) -> int:
        return next(index for index, run in enumerate(runs) if needle in run)

    assert position("check_release_tag.py") < position("uv build") < position("check --strict")
    assert position("uv build") < position("check_sdist.py")
    upload = steps[-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["if-no-files-found"] == "error"


@pytest.mark.parametrize(
    ("tag", "version", "allowed"),
    [
        ("v0.6.2", "0.6.2", True),
        ("v1.10.0", "1.10.0", True),
        ("v0.6.2", "0.6.1", False),
        ("0.6.2", "0.6.2", False),
        ("v0.6.2.dev0", "0.6.2.dev0", False),
        ("v0.6.2", "0.6.2.dev0", False),
        ("v0.6.2rc1", "0.6.2rc1", False),
        ("v0.6.2\nv0.6.3", "0.6.2", False),
    ],
)
def test_only_a_release_tag_of_the_packaged_version_publishes(
    tag: str, version: str, allowed: bool
) -> None:
    assert (_check_release_tag().tag_problem(tag, version) is None) is allowed


def test_a_manual_run_must_be_started_from_the_tag_it_publishes() -> None:
    tag_problem = _check_release_tag().tag_problem
    assert tag_problem("v0.6.2", "0.6.2", "refs/tags/v0.6.2") is None
    assert tag_problem("v0.6.2", "0.6.2", "refs/heads/master") is not None
    assert tag_problem("v0.6.2", "0.6.2", "refs/tags/v0.6.1") is not None


def test_the_tag_check_reads_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _check_release_tag()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "hungry-crab"\nversion = "0.6.2"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    monkeypatch.setenv("TAG", "v0.6.2")
    assert module.main() == 0
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v0.6.2")
    assert module.main() == 0
    monkeypatch.setenv("GITHUB_REF", "refs/heads/master")
    assert module.main() == 1
    assert "not from refs/tags/v0.6.2" in capsys.readouterr().err
    monkeypatch.delenv("GITHUB_REF")
    monkeypatch.setenv("TAG", "v0.6.1")
    assert module.main() == 1
    assert "does not name the packaged version 0.6.2" in capsys.readouterr().err
    monkeypatch.chdir(ROOT)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    monkeypatch.setenv("TAG", f"v{project['version']}")
    if ".dev" in project["version"]:
        assert module.main() == 1, "master carries a development version and never publishes"
