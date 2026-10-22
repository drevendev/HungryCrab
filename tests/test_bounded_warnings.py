"""Miner warnings are prey-controlled, so the manifest keeps a bounded number of them (#324).

Four 256 KiB Gemfiles of `gem 1` lines gave one warning per line: a 70 MB manifest.json that the
digest reader refused, so the same commit was digested again, and published again, every time.
"""

from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW
from fixture_builder import git
from helpers import copy_repo, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest, run_miners
from hungry_crab.miners.base import (
    MAX_MINER_WARNINGS,
    MAX_WARNING_CHARS,
    MineContext,
    MinerResult,
    bounded_warnings,
)
from hungry_crab.miners.ruby import parse_gemfile, parse_gemfile_lock, parse_gemspec


def test_bounded_warnings_keeps_the_first_and_counts_the_rest() -> None:
    warnings = [f"warning {index}" for index in range(MAX_MINER_WARNINGS + 150)]
    kept = bounded_warnings(warnings)
    assert kept[:MAX_MINER_WARNINGS] == warnings[:MAX_MINER_WARNINGS]
    assert kept[MAX_MINER_WARNINGS:] == ["... and 150 more warnings"]
    assert bounded_warnings(warnings[:3]) == warnings[:3]


def test_bounded_warnings_clips_one_long_warning() -> None:
    long = "could not parse " + "a/" * 5_000 + "package.json: ValueError"
    (kept,) = bounded_warnings([long])
    assert len(kept) == MAX_WARNING_CHARS
    assert kept.startswith("could not parse a/a/") and kept.endswith("...")


class _NoisyMiner:
    name = "noisy"
    requires: tuple[str, ...] = ()
    json_file: str | None = None
    md_file: str | None = None

    def run(self, _ctx: MineContext) -> MinerResult:
        return MinerResult(self.name, {}, warnings=[f"line {n}" for n in range(10_000)])


def test_run_miners_caps_each_miners_warnings(tmp_path: Path) -> None:
    ctx = MineContext(root=tmp_path, sha="a" * 40, ref="fixture", label="fixture")
    (record,) = run_miners(ctx, [_NoisyMiner()], tmp_path)
    assert len(record["warnings"]) == MAX_MINER_WARNINGS + 1
    assert record["warnings"][-1] == f"... and {10_000 - MAX_MINER_WARNINGS} more warnings"


def test_repeated_ruby_warnings_collapse_into_one_line_per_manifest() -> None:
    packages, warnings = parse_gemfile("gem 1\n" * 10_000 + 'eval_gemfile "x"\n', "Gemfile")
    assert packages == []
    assert warnings == [
        "dynamic Ruby dependency not parsed in Gemfile on 10000 lines: 1, 2, 3, 4, 5 and 9995 more",
        "dynamic Ruby include not followed in Gemfile:10001",
    ]

    _, warnings = parse_gemspec("s.add_dependency name\n" * 3, "a.gemspec")
    assert warnings == ["dynamic Ruby dependency not parsed in a.gemspec on 3 lines: 1, 2, 3"]

    names = [f"gem{index:04d}" for index in range(1_000)]
    lock = "DEPENDENCIES\n" + "".join(f"  {name}\n" for name in names)
    rows, warnings = parse_gemfile_lock(lock, "Gemfile.lock")
    assert rows == []
    expected = (
        "Ruby lock resolution missing for 1000 dependencies in Gemfile.lock: "
        "gem0000, gem0001, gem0002, gem0003, gem0004 and 995 more"
    )
    assert warnings == [expected]


def test_a_noisy_gemfile_keeps_the_manifest_small_and_reusable(
    npm_app: Path, tmp_path: Path
) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    for index in range(4):
        write_tree(work, {f"{'a' * 40}/{'b' * 40}/pkg{index}/Gemfile": "gem 1\n" * 10_000})
    git(work, "add", "-A")
    git(work, "commit", "-m", "test: add noisy Gemfiles")
    options = DigestOptions(now=FIXED_NOW, cache_root=tmp_path / "cache")

    first = run_digest(Target(path=work), options)
    assert not first.cached
    warnings = first.manifest["warnings"]
    assert len([w for w in warnings if w.startswith("deps: ")]) == 4
    assert len(warnings) <= MAX_MINER_WARNINGS + 10
    assert first.manifest_path.stat().st_size < 200_000

    again = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert again.cached
