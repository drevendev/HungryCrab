"""A digest has to be an answer to the question that was asked, and to admit what it missed.

Three faults with one shape: the crab knew something and the thing that answered did not ask.
The cache key knew the commit and not the worktree (#65); the manifest knew a miner had crashed
and the cache, the compare and the exit code did not (#67, #61).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXED_NOW
from helpers import copy_repo

from hungry_crab.cache import Target
from hungry_crab.compare import CompareOptions, compare_digests
from hungry_crab.compare.candidates import Side
from hungry_crab.digest import (
    DigestOptions,
    DigestResult,
    failed_miners,
    run_digest,
    worktree_fingerprint,
)
from hungry_crab.errors import CrabError
from hungry_crab.fetch.git import GitRunner


def _options(tmp_path: Path) -> DigestOptions:
    return DigestOptions(out=tmp_path / "out", now=FIXED_NOW, cache_root=tmp_path / "cache")


def _break_a_miner(digest_dir: Path, name: str = "deps") -> None:
    """Rewrite a finished manifest as though ``name`` had raised, and take its output away."""
    path = digest_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for record in manifest["miners"]:
        if record["name"] == name:
            record["ok"] = False
            record["error"] = "RuntimeError: pretend"
            break
    else:  # pragma: no cover - the fixture would have to lose a miner for this to happen
        raise AssertionError(f"no {name} miner in the manifest")
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (digest_dir / f"{name}.json").unlink(missing_ok=True)


def test_a_clean_worktree_says_so(npm_app: Path, tmp_path: Path) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    assert worktree_fingerprint(GitRunner(work), work) == "clean"
    assert worktree_fingerprint(None, work) == ""


def test_an_edited_worktree_is_not_the_commit_it_sits_on(npm_app: Path, tmp_path: Path) -> None:
    """The miners read files; the cache key read the commit. Two checkouts, one answer (#65)."""
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    options = _options(tmp_path)

    first = run_digest(Target(path=work), options)
    assert not first.cached
    assert first.manifest["prey"]["worktree"] == "clean"

    again = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert again.cached, "nothing moved, so nothing should be recomputed"

    tracked = (GitRunner(work).run("ls-files").splitlines() or ["README.md"])[0]
    (work / tracked).write_text("changed by the test\n", encoding="utf-8")
    edited = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not edited.cached, "an edited worktree is a different question"
    assert edited.manifest["prey"]["worktree"] not in ("", "clean")


def test_an_untracked_file_is_part_of_the_question(npm_app: Path, tmp_path: Path) -> None:
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    options = _options(tmp_path)
    run_digest(Target(path=work), options)

    (work / "scratch.py").write_text("print('hello')\n", encoding="utf-8")
    after = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not after.cached


def test_every_git_worktree_is_fingerprinted(npm_digest: DigestResult) -> None:
    """Including a prey clone, which is expected to be clean and is asked anyway.

    An empty diff costs almost nothing, and "a clone is never edited" is an assumption about a
    directory on someone's disk that an interrupted fetch is enough to break.
    """
    assert npm_digest.manifest["prey"]["worktree"] == "clean"


def test_a_digest_missing_a_producer_is_not_reused(npm_app: Path, tmp_path: Path) -> None:
    """A failed miner writes no file, and an absent file reads as an absent fact (#67)."""
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    options = _options(tmp_path)
    first = run_digest(Target(path=work), options)
    assert not first.cached
    assert failed_miners(first.manifest) == []

    _break_a_miner(first.out_dir)
    again = run_digest(Target(path=work), DigestOptions(**options.__dict__))
    assert not again.cached, "the crashed producer has to be given another chance"
    assert failed_miners(again.manifest) == []


def test_compare_refuses_a_digest_that_is_missing_a_producer(
    npm_digest: DigestResult, py_digest: DigestResult, tmp_path: Path
) -> None:
    prey = copy_repo(npm_digest.out_dir, tmp_path / "prey")
    _break_a_miner(prey)

    side = Side.load(prey)
    assert side.failed_miners == ["deps"]

    with pytest.raises(CrabError, match="missing a producer"):
        compare_digests(prey, py_digest.out_dir)

    allowed = compare_digests(prey, py_digest.out_dir, options=CompareOptions(allow_partial=True))
    assert allowed.menu["prey"]["label"]


def test_a_healthy_digest_has_no_failed_miners(npm_digest: DigestResult) -> None:
    assert failed_miners(npm_digest.manifest) == []
    assert Side.load(npm_digest.out_dir).failed_miners == []


def test_the_exit_code_can_be_asked_to_notice_a_failed_miner(
    npm_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed miner is printed and the process exits 0, so CI cannot see it (#61).

    Lenient stays the default: a digest missing one producer is still worth reading, and a human
    has the FAILED lines in front of them. A machine that is about to compare it does not.
    """
    from hungry_crab.cli import main
    from hungry_crab.miners import deps

    def explode(self: object, ctx: object) -> None:
        raise RuntimeError("pretend")

    monkeypatch.setattr(deps.DepsMiner, "run", explode)
    work = copy_repo(npm_app, tmp_path / "work", with_git=True)
    argv = [
        "--cache-dir",
        str(tmp_path / "cache"),
        "-q",
        "digest",
        str(work),
        "--out",
        str(tmp_path / "out"),
        "--maw-license",
        "MIT",
    ]

    assert main(argv) == 0, "the default is lenient, and stays lenient"
    assert main([*argv, "--force", "--fail-on-miner-error"]) != 0
