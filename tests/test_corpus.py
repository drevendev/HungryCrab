"""A repository's test corpus is not the repository.

`github-linguist/linguist` is 3390 sample files in four hundred languages against 32 files of
Ruby. The crab read it as an Objective-C project with the ecosystems dotnet, go, python and
rust, none of which is Ruby: every manifest it found lived under `samples/`.
"""

from __future__ import annotations

from pathlib import Path

from conftest import FIXED_NOW
from helpers import copy_repo, read_json, write_tree

from hungry_crab.cache import Target
from hungry_crab.digest import DigestOptions, run_digest
from hungry_crab.miners.base import FileInfo
from hungry_crab.miners.inventory import mark_sample_corpora


def info(path: str) -> FileInfo:
    return FileInfo(
        path=path,
        name=path.rsplit("/", 1)[-1],
        ext=".py",
        size=100,
        language="Python",
        is_code=True,
        vendored=False,
        generated=False,
        binary=False,
        loc=10,
        depth=path.count("/"),
        lockfile=False,
        manifest_kind=None,
    )


def test_a_corpus_is_marked_when_the_repository_survives_without_it() -> None:
    files = [info(f"src/mod{i}.py") for i in range(12)]
    files += [info(f"samples/Ruby/case{i}.py") for i in range(300)]
    files += [info("testdata/golden.py"), info("test/fixtures/one.py")]
    mark_sample_corpora(files)
    excluded = {f.path for f in files if f.vendored}
    assert len(excluded) == 302
    assert "samples/Ruby/case0.py" in excluded
    assert "testdata/golden.py" in excluded
    assert "test/fixtures/one.py" in excluded, "a corpus is a corpus at any depth"
    assert "src/mod0.py" not in excluded


def test_a_repository_that_is_its_corpus_keeps_it() -> None:
    """Otherwise a repository of examples digests as an empty repository."""
    files = [info("README.py")] + [info(f"samples/case{i}.py") for i in range(300)]
    mark_sample_corpora(files)
    assert not any(f.vendored for f in files)


def test_a_file_named_like_a_corpus_is_not_one() -> None:
    files = [info(f"src/mod{i}.py") for i in range(12)] + [info("src/fixtures.py")]
    mark_sample_corpora(files)
    assert not any(f.vendored for f in files), "the rule reads directories, not file names"


def test_the_prey_stack_is_read_from_its_own_code(npm_app: Path, tmp_path: Path) -> None:
    """The linguist shape end to end: a Python project whose samples are an npm application."""
    prey = tmp_path / "prey"
    write_tree(
        prey,
        {
            "pyproject.toml": '[project]\nname = "thing"\nversion = "0.1.0"\n',
            **{f"src/thing/mod{i}.py": "x = 1\n" for i in range(12)},
        },
    )
    copy_repo(npm_app, prey / "samples" / "npm-app")
    result = run_digest(Target(path=prey), DigestOptions(out=tmp_path / "d", now=FIXED_NOW))
    traits = read_json(result, "traits.json")["traits"]
    assert traits["ecosystems"] == ["python"], "the sample corpus is not this project's stack"
    assert traits["linters"] == [] and traits["test_frameworks"] == []
    inventory = read_json(result, "inventory.json")
    assert inventory["primary_language"] == "Python"
    assert not any(m["path"].startswith("samples/") for m in inventory["manifests"])
    assert inventory["files_counted"] < inventory["files"]
