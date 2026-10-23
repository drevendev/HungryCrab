"""Prey-controlled manifest patterns stay linear in the bytes a miner reads.

Each hostile shape below took from a minute to several minutes per file at the read limit before
its pattern was rewritten; the miners read up to forty manifests and every workflow in a prey, in
process, with no deadline. The bound is generous: a linear scan takes milliseconds.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from hungry_crab.licensing.detect import manifest_license
from hungry_crab.miners.ci import parse_workflow
from hungry_crab.miners.deps import parse_requirements, split_python_requirement
from hungry_crab.miners.ruby import parse_gemfile, parse_gemfile_lock, parse_gemspec

LIMIT = 262_144
SHA = "a" * 40

HOSTILE: dict[str, Callable[[], object]] = {
    "requirement with a long inner blank run": lambda: split_python_requirement(
        "a x" + " " * LIMIT + "y"
    ),
    "multi-line requirement after a blank run": lambda: split_python_requirement(
        "a" + " " * LIMIT + "x\nz"
    ),
    "Gemfile group block with a blank run": lambda: parse_gemfile(
        "group a" + " " * LIMIT + "x\n", "Gemfile"
    ),
    "Gemfile inline groups without a closing bracket": lambda: parse_gemfile(
        "gem 'a', " + "group:[" * (LIMIT // 7), "Gemfile"
    ),
    "Gemfile with one requirement per argument": lambda: parse_gemfile(
        "gem 'a'" + ", 'b'" * (LIMIT // 5), "Gemfile"
    ),
    "gemspec with one requirement per argument": lambda: parse_gemspec(
        "s.add_dependency 'a'" + ", 'b'" * (LIMIT // 5), "a.gemspec"
    ),
    "Gemfile.lock dependency with trailing blanks": lambda: parse_gemfile_lock(
        "DEPENDENCIES\n  a" + " " * (2 * LIMIT) + "x\n", "Gemfile.lock"
    ),
    "csproj licence expression with a blank run": lambda: manifest_license(
        "a.csproj", "<PackageLicenseExpression>a" + " " * 500_000 + "x"
    ),
    "setup.cfg of blank lines": lambda: manifest_license("setup.cfg", "\n" * 500_000 + "x"),
    "workflow comment of uses: without a version": lambda: parse_workflow(
        "on: push\n# " + "uses:" * (LIMIT // 5) + "\n", "w.yml"
    ),
    "workflow run of curl without a pipe": lambda: parse_workflow(
        "on: push\njobs:\n  a:\n    steps:\n      - run: '" + "curl " * (LIMIT // 6) + "'\n",
        "w.yml",
    ),
}


@pytest.mark.parametrize("label", sorted(HOSTILE))
def test_hostile_manifest_shapes_parse_in_linear_time(label: str) -> None:
    started = time.perf_counter()
    HOSTILE[label]()
    assert time.perf_counter() - started < 1, label


@pytest.mark.parametrize(
    ("req", "expected"),
    [
        ("requests [security] >= 2.0 , < 3", ("requests", ">= 2.0 , < 3")),
        ("a\n>=1", ("a", ">=1")),
        ("a >=1\n<2", None),
        ("name", ("name", "")),
    ],
)
def test_python_requirement_split_is_unchanged(req: str, expected: tuple[str, str] | None) -> None:
    assert split_python_requirement(req) == expected


def test_requirements_file_lines_still_parse() -> None:
    packages, _ = parse_requirements("flask   ==  3.0.0   \nrich[jupyter]>=13\n", "r.txt")
    assert [(p.name, p.spec, p.pinned) for p in packages] == [
        ("flask", "==  3.0.0", True),
        ("rich", ">=13", False),
    ]


def test_gemfile_groups_are_read_as_before() -> None:
    packages, warnings = parse_gemfile(
        "group :development, :test do # tools\n"
        '  gem "rspec"\n'
        "end\n"
        "group   do\n"
        '  gem "inner"\n'
        "end\n"
        'gem "a", group: :development\n'
        'gem "b", groups: [:production, :test], require: false\n'
        'gem "c", group: [:production]\n'
        'gem "d", "~> 1.0", ">= 1.0.2", require: false\n'
        "gem \"e\", '1.0', VERSION\n",
        "Gemfile",
    )
    kinds = {p.name: (p.kind, p.spec) for p in packages}
    assert kinds == {
        "rspec": ("dev", ""),
        "inner": ("runtime", ""),
        "a": ("dev", ""),
        "b": ("dev", ""),
        "c": ("runtime", ""),
        "d": ("runtime", "~> 1.0, >= 1.0.2"),
        "e": ("runtime", "1.0"),
    }
    assert warnings == ["dynamic Ruby requirement not parsed in Gemfile:11"]


def test_gemfile_lock_dependencies_are_read_as_before() -> None:
    rows, warnings = parse_gemfile_lock(
        "GEM\n  specs:\n    rack (3.1.8)\n    rake (13.2.1)\n\n"
        "DEPENDENCIES\n  rack (~> 3.0)!  \n  rake\t\n",
        "Gemfile.lock",
    )
    assert [row["name"] for row in rows] == ["rack", "rake"]
    assert warnings == []


def test_manifest_licences_are_read_as_before() -> None:
    assert manifest_license(
        "a.csproj",
        "<PackageLicenseExpression></PackageLicenseExpression>"
        "<PackageLicenseExpression>\n  MIT\n</PackageLicenseExpression>",
    ) == ("MIT", "")
    assert manifest_license("setup.cfg", "[metadata]\n\n\n  license = Apache-2.0\n") == (
        "Apache-2.0",
        "",
    )


def test_workflow_comments_and_pipes_are_read_as_before() -> None:
    parsed = parse_workflow(
        "on: push\njobs:\n  a:\n    steps:\n"
        f"      - uses: actions/checkout@{SHA} # v4.1.0\n"
        f"      - uses: 'actions/setup-python@{SHA}' # v5\n"
        "      - run: curl -fsSL https://x.test/a.sh | sudo bash\n"
        "      - run: curl a curl b | sh\n"
        "      - run: curl https://x.test/a | tee a.txt\n",
        "w.yml",
    )
    assert parsed is not None
    assert [action["version_comment"] for action in parsed["actions"]] == ["v4.1.0", "v5"]
    assert parsed["pipe_to_shell"] == 2
