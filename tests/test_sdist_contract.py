"""Source-archive and pre-commit parity regressions for issue #231."""

from __future__ import annotations

import io
import runpy
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
verify_sdist = runpy.run_path(str(ROOT / "scripts" / "check_sdist.py"))["verify_sdist"]
REQUIRED = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "CODE_OF_CONDUCT.md",
    "src/hungry_crab/__init__.py",
    "src/hungry_crab/data/report.css",
)


def _archive(tmp_path: Path, *names: str) -> Path:
    archive = tmp_path / "hungry_crab-0.3.0.dev0.tar.gz"
    with tarfile.open(archive, mode="w:gz") as output:
        for name in names:
            content = b"fixture only\n"
            item = tarfile.TarInfo(f"hungry_crab-0.3.0.dev0/{name}")
            item.size = len(content)
            output.addfile(item, io.BytesIO(content))
    return archive


def test_valid_source_archive_has_cli_and_no_redundant_test_checkout(tmp_path: Path) -> None:
    verify_sdist(_archive(tmp_path, *REQUIRED))


def test_source_archive_rejects_uncollectable_tests(tmp_path: Path) -> None:
    archive = _archive(tmp_path, *REQUIRED, "tests/test_hooks.py")
    with pytest.raises(ValueError, match="without their repository fixtures"):
        verify_sdist(archive)


def test_source_archive_rejects_missing_runtime_files(tmp_path: Path) -> None:
    archive = _archive(tmp_path, "README.md", "LICENSE", "pyproject.toml")
    with pytest.raises(ValueError, match=r"src/hungry_crab/__init__\.py"):
        verify_sdist(archive)


def test_precommit_ruff_comes_from_uv_lock_without_an_extra_remote_pin() -> None:
    settings = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    repos = settings["repos"]
    assert not any("ruff-pre-commit" in str(item["repo"]) for item in repos)
    local = next(item for item in repos if item["repo"] == "local")
    hooks = {item["id"]: item for item in local["hooks"]}
    assert set(hooks) == {"ruff-check", "ruff-format"}
    for item in hooks.values():
        assert item["language"] == "system"
        assert item["entry"].startswith("uv run --locked ruff ")
        assert "tests/fixtures" in item["exclude"]
