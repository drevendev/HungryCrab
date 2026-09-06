"""What the live meals taught the menu: no noise, no duplicates, no alternatives, no blind spots.

Every test here reproduces a defect the crab served to its own maintainer. Eating ``pypa/pipx``
gave an unbounded pile of issue lessons, a type checker proposed to a host that already had
one, and a dependency card for the library implementing a nutrient already on the menu. Eating
``anthropics/skills`` gave nothing at all in the one category it was chosen to exercise.
"""

from __future__ import annotations

from typing import Any

import pytest

from hungry_crab.compare.candidates import (
    MAX_ISSUE_CLUSTERS,
    MAX_TOP_ISSUES,
    Side,
    ai_config_candidates,
    build_candidates,
    deps_candidates,
    issue_candidates,
    tool_candidates,
)


def side(label: str, traits: dict[str, Any], **rest: Any) -> Side:
    return Side(label=label, sha="0" * 40, url=None, root=None, traits=traits, **rest)


def python_deps(*names: str) -> dict[str, Any]:
    return {"packages": [{"ecosystem": "python", "name": n, "kind": "dev"} for n in names]}


def test_a_tool_the_host_already_has_a_kind_of_is_not_a_gap() -> None:
    """`ty` ranked first on a host running mypy --strict. It is a swap, not a nutrient."""
    prey = side("prey", {"ecosystems": ["python"], "type_checkers": ["ty"], "linters": ["ruff"]})
    host = side("host", {"ecosystems": ["python"], "type_checkers": ["mypy"], "linters": []})
    keys = {c.key for c in tool_candidates(prey, host)}
    assert "tooling.type_checker.ty" not in keys
    assert "tooling.linter.ruff" in keys, "an empty kind is still a real gap"


def test_a_tool_of_a_kind_the_host_lacks_entirely_is_proposed() -> None:
    prey = side("prey", {"ecosystems": ["python"], "type_checkers": ["mypy", "pyright"]})
    host = side("host", {"ecosystems": ["python"], "type_checkers": []})
    keys = {c.key for c in tool_candidates(prey, host)}
    assert keys == {"tooling.type_checker.mypy", "tooling.type_checker.pyright"}
    assert all(c.host_state == "none" for c in tool_candidates(prey, host))


def test_deps_see_tools_configured_by_a_file() -> None:
    """pre-commit was proposed to a host with .pre-commit-config.yaml in its root."""
    prey = side("prey", {"ecosystems": ["python"]}, deps=python_deps("pre-commit", "tox"))
    host = side("host", {"ecosystems": ["python"], "has_precommit": True}, deps=python_deps())
    out, _ = deps_candidates(prey, host)
    keys = {c.key for c in out}
    assert "deps.python.pre-commit" not in keys
    assert "deps.python.tox" in keys
    others = next(c for c in out if c.key == "deps.python.others")
    assert "pre-commit" in others.what, "still listed as a fact, just not as its own card"


def test_a_dependency_that_only_implements_another_nutrient_is_dropped() -> None:
    """pytest-cov and tests.coverage are the same change; the menu proposed both."""
    prey = side(
        "prey",
        {"ecosystems": ["python"], "coverage_configured": True},
        deps=python_deps("pytest-cov"),
    )
    host = side(
        "host",
        {"ecosystems": ["python"], "has_tests": True, "coverage_configured": False},
        deps=python_deps(),
    )
    keys = {c.key for c in build_candidates(prey, host)[0]}
    assert "tests.coverage" in keys
    assert "deps.python.pytest-cov" not in keys, "the library that implements it is the same card"


def test_the_implementing_dependency_survives_when_the_nutrient_is_not_on_the_menu() -> None:
    """Both sides write property tests, so hypothesis is a library choice, not a duplicate."""
    prey = side(
        "prey",
        {"ecosystems": ["python"], "has_property_tests": True},
        deps=python_deps("hypothesis"),
    )
    host = side("host", {"ecosystems": ["python"], "has_property_tests": True}, deps=python_deps())
    keys = {c.key for c in build_candidates(prey, host)[0]}
    assert "tests.property" not in keys
    assert "deps.python.hypothesis" in keys


def test_a_wider_skills_corpus_is_a_candidate_even_though_the_host_has_skills() -> None:
    """Eating anthropics/skills produced no ai-config card: every rule was a boolean."""
    prey = side("anthropics/skills", {"has_skills": True, "skills_count": 20})
    host = side("host", {"has_skills": True, "skills_count": 3})
    out = ai_config_candidates(prey, host)
    assert [c.key for c in out] == ["ai-config.skills-corpus"]
    assert out[0].title == "Measure your 3 skills against anthropics/skills's 20"
    assert out[0].prey_state == "20 skills" and out[0].host_state == "3 skills"
    assert out[0].artifact == "idea"


@pytest.mark.parametrize(
    ("prey_count", "host_count"),
    [
        (7, 3),  # only four ahead: not worth reading a corpus for
        (8, 3),  # five ahead but not three times as many
        (9, 0),  # the host has none, and "Add skills" already covers that
    ],
)
def test_a_narrow_lead_is_not_a_corpus(prey_count: int, host_count: int) -> None:
    prey = side("prey", {"has_skills": True, "skills_count": prey_count})
    host = side("host", {"has_skills": host_count > 0, "skills_count": host_count})
    assert ai_config_candidates(prey, host) == []


def test_issue_clusters_are_capped_and_titled_by_their_largest_issue() -> None:
    clusters = [
        {
            "size": 30 - index,
            "reactions": index,
            "terms": [f"term{index}", "install"],
            "sample_titles": [f"pipx install fails on {index}", "another one"],
        }
        for index in range(8)
    ]
    prey = side(
        "prey",
        {},
        issues={
            "available": True,
            "clusters": [*clusters, {"size": 2, "terms": ["tiny"], "sample_titles": []}],
            "top_by_reactions": [
                {"number": n, "title": f"issue {n}", "reactions": 50, "state": "open"}
                for n in range(6)
            ],
        },
    )
    host = side("host", {})
    out = issue_candidates(prey, host)
    clustered = [c for c in out if "cluster-" in c.key]
    assert len(clustered) == MAX_ISSUE_CLUSTERS
    assert len(out) == MAX_ISSUE_CLUSTERS + MAX_TOP_ISSUES
    assert clustered[0].title == "Recurring pain in prey: pipx install fails on 0"
    assert "term0, install" in clustered[0].what
    assert clustered[0].prey_state == "30 issues, 0 reactions"
    sizes = [int(c.prey_state.split()[0]) for c in clustered]
    assert sizes == sorted(sizes, reverse=True), "the biggest clusters win the cap"


def test_a_cluster_without_sample_titles_falls_back_to_its_terms() -> None:
    prey = side(
        "prey",
        {},
        issues={"available": True, "clusters": [{"size": 9, "terms": ["windows", "path"]}]},
    )
    out = issue_candidates(prey, side("host", {}))
    assert out[0].title == "Recurring pain in prey: windows, path"
