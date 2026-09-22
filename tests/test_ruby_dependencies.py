from __future__ import annotations

from pathlib import Path

from hungry_crab.miners.base import MineContext, MinerResult
from hungry_crab.miners.inventory import InventoryMiner
from hungry_crab.miners.scope import ProjectDepsMiner


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _deps(root: Path) -> MinerResult:
    ctx = MineContext(root=root, sha="a" * 40, ref="fixture", label="fixture")
    ctx.results["inventory"] = InventoryMiner().run(ctx)
    return ProjectDepsMiner().run(ctx)


def test_ruby_application_keeps_declared_specs_separate_from_lock_resolution(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "Gemfile",
        'source "https://rubygems.org"\n'
        'gem "rack", "~> 3.0"\n'
        'gem "minitest", "5.25.1", group: :development\n',
    )
    _write(
        tmp_path,
        "Gemfile.lock",
        """GEM
  remote: https://rubygems.org/
  specs:
    minitest (5.25.1)
    rack (3.1.8)

DEPENDENCIES
  minitest (= 5.25.1)
  rack (~> 3.0)

BUNDLED WITH
   2.5.18
""",
    )

    result = _deps(tmp_path)
    assert result.data["ecosystems"] == ["ruby"]
    packages = {row["name"]: row for row in result.data["packages"]}
    assert packages["rack"]["spec"] == "~> 3.0"
    assert packages["rack"]["pinned"] is False
    assert packages["rack"]["kind"] == "runtime"
    assert packages["minitest"]["spec"] == "5.25.1"
    assert packages["minitest"]["pinned"] is True
    assert packages["minitest"]["kind"] == "dev"
    assert result.data["ruby_resolutions"] == [
        {"name": "minitest", "version": "5.25.1", "lockfile": "Gemfile.lock"},
        {"name": "rack", "version": "3.1.8", "lockfile": "Gemfile.lock"},
    ]
    assert result.data["policies"]["ruby"]["package_manager"] == "bundler"
    assert result.data["policies"]["ruby"]["lockfiles"] == ["Gemfile.lock"]
    assert result.data["policies"]["ruby"]["pinned_ratio"] == 0.5


def test_gemspec_only_library_is_ruby_and_keeps_nested_tooling_auxiliary(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "example.gemspec",
        """Gem::Specification.new do |spec|
  spec.add_dependency "rugged", "~> 1.7"
  spec.add_development_dependency("minitest", "~> 5.0")
end
""",
    )
    _write(
        tmp_path,
        "tools/worker/go.mod",
        "module example.com/worker\n\ngo 1.22\n\nrequire github.com/stretchr/testify v1.9.0\n",
    )

    result = _deps(tmp_path)
    assert result.data["ecosystems"] == ["ruby"]
    packages = {row["name"]: row for row in result.data["packages"]}
    assert packages["rugged"]["kind"] == "runtime"
    assert packages["minitest"]["kind"] == "dev"
    assert result.data["policies"]["ruby"]["lockfiles"] == []
    assert result.data["policies"]["ruby"]["package_manager"] == "rubygems"
    assert {row["ecosystem"] for row in result.data["auxiliary_manifests"]} == {"go"}


def test_dynamic_ruby_declarations_are_not_followed_or_guessed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "Gemfile",
        """VERSION = ENV.fetch("RACK_VERSION")
gem "rack", VERSION
gem dependency_name, "1.0.0"
eval_gemfile ENV.fetch("OTHER_GEMFILE")
""",
    )

    result = _deps(tmp_path)
    assert result.data["ecosystems"] == ["ruby"]
    assert result.data["packages"] == [
        {
            "name": "rack",
            "spec": "",
            "kind": "runtime",
            "ecosystem": "ruby",
            "manifest": "Gemfile",
            "pinned": None,
            "role": "project",
        }
    ]
    assert result.data["policies"]["ruby"]["pinned_ratio"] is None
    assert any("dynamic Ruby requirement not parsed" in warning for warning in result.warnings)
    assert any("dynamic Ruby dependency not parsed" in warning for warning in result.warnings)
    assert any("dynamic Ruby include not followed" in warning for warning in result.warnings)
