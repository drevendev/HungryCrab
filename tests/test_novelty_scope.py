"""Pending code is implementation evidence; unknown scope is not global absence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixture_builder import git
from test_discovery_inputs import repository

from hungry_crab.attribution import GitSourceReader
from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.novelty import implementation_scope
from hungry_crab.source_find import find_source
from hungry_crab.source_inspection import body_excluded, source_packet


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("tools/readme_tools.py", "def inspect_format():\n    return 'format witness'\n"),
        ("src/ReadMemory.cpp", "int inspect_format() { return 1; }\n"),
        ("readme-check.rs", "pub fn inspect_format() -> bool { true }\n"),
    ],
)
def test_readme_prefixed_implementation_is_source_not_documentation(
    tmp_path: Path, path: str, body: str
) -> None:
    maw = repository(tmp_path / "owned", {path: body})
    pin = git(maw, "rev-parse", "HEAD").strip()
    assert not body_excluded(path)
    found = find_source(maw, pin, ["inspect_format"])
    assert any(row["path"] == path for row in found["matches"])
    packet = source_packet(maw, pin, [path], [], need="inspect format", related=False)
    assert packet["files"][0]["path"] == path
    scope = implementation_scope(
        maw,
        {
            "prefixes": [path],
            "default_sha": pin,
            "pending_collection": {"complete": True, "as_of": "2026-11-02T00:00:00Z"},
        },
    )
    assert scope["implementation_scope_complete"]
    assert all(target["files"][0]["path"] == path for target in scope["targets"])


@pytest.mark.parametrize(
    "path",
    [
        "README",
        "readme.md",
        "Readme.fr.md",
        "readme_notes.txt",
        "readme.unknown",
        "README.py",
        "readme.md.py",
        "AGENTS.md",
        "skills/example/SKILL.md",
        ".github/instructions/readme_tools.py",
        ".cursor/rules/readme_tools.py",
    ],
)
def test_readme_source_exception_keeps_documents_and_instructions_excluded(path: str) -> None:
    assert body_excluded(path)


def test_public_novelty_returns_bounded_declaration_view(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def flow():\n    return 1\n"})
    pin = git(maw, "rev-parse", "HEAD").strip()
    plan = tmp_path / "scope.json"
    plan.write_text(
        json.dumps(
            {
                "prefixes": ["app.py"],
                "default_sha": pin,
                "symbols": [{"path": "app.py", "qualified_name": "flow"}],
                "include_differences": True,
            }
        ),
        encoding="utf-8",
    )
    assert main(["discover", "novelty", "--maw", str(maw), "--input", str(plan)]) == 0
    result = json.loads(capsys.readouterr().out)
    view = result["declaration_change_view"]
    assert view["declaration_comparisons"]["comparisons"][0]["exact_declaration_bytes_equal"]
    assert not view["changed_declarations"]
    assert "sources" in result and view["raw_scope_required"]
    assert result["semantic_novelty"] == "requires-agent-comparison"


def test_added_file_presence_does_not_repeat_or_hide_the_retained_body(tmp_path: Path) -> None:
    maw = repository(tmp_path / "receiving", {"existing.py": "VALUE = 1\n"})
    before = git(maw, "rev-parse", "HEAD").strip()
    body = "VALUE = 'ORIGINAL_RETAINED_SOURCE'\n" * 300
    (maw / "new.py").write_text(body, encoding="utf-8", newline="\n")
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: added implementation")
    result = implementation_scope(
        maw,
        {
            "prefixes": ["new.py"],
            "default_sha": before,
            "include_differences": True,
            "pending_collection": {"complete": True, "as_of": "2026-11-02T00:00:00Z"},
        },
    )
    row = result["implementation_differences"]["comparisons"][0]
    assert row["change"] == "other-path-absent"
    assert not row["hunks"] and not row["complete"]
    assert row["omitted_ranges"][0]["selected"]["end"] == 300
    assert result["sources"][row["selected_content_sha256"]]["content"] == body
    assert result["implementation_scope_complete"]


@pytest.mark.parametrize("path", ["added.py", "new_package/added.py"])
def test_absent_source_has_an_immutable_tree_witness(tmp_path: Path, path: str) -> None:
    maw = repository(tmp_path / "receiving", {"existing.py": "VALUE = 1\n"})
    before = git(maw, "rev-parse", "HEAD").strip()
    source = maw / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def flow():\n    return 1\n", encoding="utf-8", newline="\n")
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: add scoped source")
    result = implementation_scope(
        maw,
        {
            "prefixes": [path],
            "default_sha": before,
            "symbols": [{"path": path, "qualified_name": "flow"}],
            "pending_collection": {"complete": True, "as_of": "2026-11-02T00:00:00Z"},
        },
    )
    selected, default = result["targets"]
    assert selected["declarations"][0]["status"] == "declaration-available"
    absent = default["declarations"][0]
    assert absent["status"] == "source-absent-at-pin"
    assert absent["absence_witness"] == default["path_absence_witnesses"][0]
    assert absent["absence_witness"]["sha"] == before
    assert absent["absence_witness"]["missing_path"] == path.split("/")[0]
    comparison = result["declaration_change_view"]["declaration_comparisons"]["comparisons"][0]
    assert comparison["other_status"] == "source-absent-at-pin"
    assert (
        comparison["bindings"][0]["location"]["other_absence_witness"] == absent["absence_witness"]
    )
    assert result["implementation_scope_complete"]
    assert not result["global_historical_absence_proven"]
    assert result["semantic_novelty"] == "requires-agent-comparison"


@pytest.mark.parametrize("failure", ["tree", "file", "nonregular"])
def test_unavailable_source_never_becomes_a_path_absence_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    maw = repository(tmp_path / "receiving", {"app.py": "def flow():\n    return 1\n"})
    if failure == "tree":
        monkeypatch.setattr(GitSourceReader, "tree_entries", lambda *args: None)
    elif failure == "file":
        monkeypatch.setattr(GitSourceReader, "read", lambda *args: None)
    else:
        blob = git(maw, "rev-parse", "HEAD:app.py").strip()
        git(maw, "update-index", "--cacheinfo", "120000," + blob + ",app.py")
        git(maw, "commit", "-m", "test: nonregular scoped material")
    pin = git(maw, "rev-parse", "HEAD").strip()
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": pin,
            "symbols": [{"path": "app.py", "qualified_name": "flow"}],
        },
    )
    assert result["omissions"] and not result["implementation_scope_complete"]
    for target in result["targets"]:
        assert not target["path_absence_witnesses"]
        assert target["declarations"][0]["status"] == "source-unavailable-in-acquired-scope"


def test_present_parsed_file_without_symbol_is_not_a_missing_source(tmp_path: Path) -> None:
    maw = repository(tmp_path / "receiving", {"app.py": "VALUE = 1\n"})
    pin = git(maw, "rev-parse", "HEAD").strip()
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": pin,
            "symbols": [{"path": "app.py", "qualified_name": "flow"}],
        },
    )
    for target in result["targets"]:
        assert not target["path_absence_witnesses"]
        assert target["declarations"][0]["status"] == "declaration-absent"


def test_declaration_projection_retains_decorators_and_helper_uncertainty(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving",
        {"app.py": "def helper(fn): return fn\n@helper\ndef flow():\n    return 1\n"},
    )
    before = git(maw, "rev-parse", "HEAD").strip()
    (maw / "app.py").write_text(
        "def helper(fn): return None\n@helper\ndef flow():\n    return 1\n",
        encoding="utf-8",
        newline="\n",
    )
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: changed helper behind identical declaration")
    plan = {
        "prefixes": ["app.py"],
        "default_sha": before,
        "symbols": [{"path": "app.py", "qualified_name": "flow"}],
        "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
    }
    result = implementation_scope(maw, plan)
    selected, default = result["targets"]
    declaration = selected["declarations"][0]
    assert declaration["status"] == "declaration-available"
    assert declaration["start"] == 2 and declaration["end"] == 4
    assert declaration["content_sha256"] == default["declarations"][0]["content_sha256"]
    assert selected["files"] != default["files"]
    assert "never runtime equivalence" in result["declaration_scope"]
    assert result["semantic_novelty"] == "requires-agent-comparison"
    assert len(result["sources"]) == 2
    projected = implementation_scope(maw, {**plan, "include_differences": True})
    delta = projected["implementation_differences"]
    assert delta["complete"]
    comparison = delta["comparisons"][0]
    assert "return None" in comparison["hunks"][0]["selected"]["content"]
    assert "return fn" in comparison["hunks"][0]["other"]["content"]
    assert comparison["bindings"][0]["role"] == "receiving-default"
    assert "Not syntax or runtime equivalence" in delta["interpretation"]
    declarations = projected["declaration_comparisons"]
    assert declarations["complete"]
    declaration_delta = declarations["comparisons"][0]
    assert declaration_delta["exact_declaration_bytes_equal"]
    assert (
        declaration_delta["bindings"][0]["location"]["selected_file_sha256"]
        != (declaration_delta["bindings"][0]["location"]["other_file_sha256"])
    )
    view = projected["declaration_change_view"]
    assert not view["changed_declarations"]
    assert not view["other_file_changes"]["projected"]
    assert "imports, globals, helpers" in declarations["interpretation"]
    # The helper change remains in the full retained diff, not an equal-runtime claim.
    assert "return None" in comparison["hunks"][0]["selected"]["content"]
    (maw / "app.py").write_text(
        "@other\ndef flow():\n    return 1\n", encoding="utf-8", newline="\n"
    )
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: different decorator")
    changed = implementation_scope(maw, plan)
    assert (
        changed["targets"][0]["declarations"][0]["content_sha256"] != declaration["content_sha256"]
    )
    view = changed["declaration_change_view"]
    assert not view["declaration_comparisons"]["comparisons"][0]["exact_declaration_bytes_equal"]
    hunks = view["changed_declarations"][0]["differences"]["comparisons"][0]["hunks"]
    assert hunks[0]["selected"]["content"].startswith("@other\n")
    assert hunks[0]["other"]["content"].startswith("@helper\n")
    changed_declaration = view["changed_declarations"][0]
    assert changed_declaration["origins"][1]["role"] == "receiving-default"
    assert changed_declaration["differences"]["comparisons"][0]["bindings"][0]["role"] == (
        "receiving-default"
    )


def test_change_projection_bounds_large_gaps_and_does_not_infer_missing_source(
    tmp_path: Path,
) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "VALUE = 1\n", "README.md": "excluded body\n"})
    before = git(maw, "rev-parse", "HEAD").strip()
    (maw / "app.py").write_text("VALUE = 2\n" * 1000, encoding="utf8")
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: large non-unique changed region")
    plan = {"prefixes": ["app.py"], "default_sha": before, "include_differences": True}
    projected = implementation_scope(maw, plan)["implementation_differences"]
    assert not projected["complete"]
    assert projected["comparisons"][0]["omitted_hunks"] == 1
    assert projected["comparisons"][0]["hunks"] == []
    with pytest.raises(CrabError, match="boolean"):
        implementation_scope(maw, {**plan, "include_differences": "yes"})
    excluded = implementation_scope(maw, {**plan, "prefixes": ["README.md"]})
    assert not excluded["implementation_differences"]["complete"]
    assert "incomplete" in excluded["implementation_differences"]["reason"]
    absent = implementation_scope(maw, {**plan, "prefixes": ["absent.py"]})
    assert absent["implementation_differences"]["complete"]


def test_declaration_absence_ambiguity_and_scope_are_explicit(tmp_path: Path) -> None:
    maw = repository(
        tmp_path / "receiving",
        {"app.py": "def flow(): return 1\ndef flow(): return 2\n"},
    )
    pin = git(maw, "rev-parse", "HEAD").strip()
    plan = {
        "prefixes": ["app.py"],
        "default_sha": pin,
        "symbols": [{"path": "app.py", "qualified_name": n} for n in ("flow", "missing")],
        "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
    }
    result = implementation_scope(maw, plan)
    assert [d["status"] for d in result["targets"][0]["declarations"]] == [
        "declaration-ambiguous",
        "declaration-absent",
    ]
    assert all(
        not row["exact_declaration_bytes_equal"]
        for row in result["declaration_comparisons"]["comparisons"]
    )
    plan["symbols"] = [{"path": "elsewhere.py", "qualified_name": "flow"}]
    with pytest.raises(CrabError, match="in scope"):
        implementation_scope(maw, plan)


def test_scope_retains_selected_default_and_pending_without_conflating_roles(
    tmp_path: Path,
) -> None:
    maw = repository(
        tmp_path / "receiving", {"app.py": "def consume(stream):\n    return stream.read()\n"}
    )
    before = git(maw, "rev-parse", "HEAD").strip()
    (maw / "app.py").write_text(
        "def consume(stream):\n    return stream.read(4)\n", encoding="utf-8"
    )
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: pending implementation")
    after = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "update-ref", "refs/crab/qualification/pr-1", after)
    git(maw, "checkout", "--detach", before)
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": before,
            "pending": [{"sha": after, "ref": "refs/crab/qualification/pr-1"}],
            "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
        },
    )
    assert result["implementation_scope_complete"] and len(result["sources"]) == 2
    assert [r["role"] for r in result["targets"]] == [
        "receiving-selected",
        "receiving-default",
        "receiving-pending",
    ]
    assert not result["global_historical_absence_proven"]
    assert result["semantic_novelty"] == "requires-agent-comparison"
    assert not result["donor_executed"] and result["provider_requests"] == 0


def test_unacquired_pending_pin_stays_unknown_without_implicit_fetch(tmp_path: Path) -> None:
    maw = repository(tmp_path / "receiving", {"app.py": "def correct():\n    return True\n"})
    pin = git(maw, "rev-parse", "HEAD").strip()
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": pin,
            "pending": [{"sha": "f" * 40, "ref": "refs/crab/qualification/pr-2"}],
            "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
        },
    )
    assert not result["implementation_scope_complete"]
    assert result["omissions"][0]["reason"] == "immutable-head-unavailable"


def test_unrelated_nonregular_tree_does_not_poison_checked_module_scope(tmp_path: Path) -> None:
    maw = repository(tmp_path / "receiving", {"app.py": "def correct():\n    return True\n"})
    first = git(maw, "rev-parse", "HEAD").strip()
    git(maw, "update-index", "--add", "--cacheinfo", "160000," + first + ",vendor/component")
    git(maw, "commit", "-m", "test: unrelated submodule metadata")
    pin = git(maw, "rev-parse", "HEAD").strip()
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": pin,
            "pending_collection": {"complete": True, "as_of": "2026-11-01T00:00:00Z"},
        },
    )
    assert result["implementation_scope_complete"]
    assert len(result["sources"]) == 1 and not result["omissions"]


def test_changed_declaration_view_bounds_bodies_without_deleting_raw_source(tmp_path: Path) -> None:
    maw = repository(tmp_path / "maw", {"app.py": "def flow():\n    return 1\n"})
    before = git(maw, "rev-parse", "HEAD").strip()
    body = "def flow():\n" + "    # retained original large body\n" * 400 + "    return 2\n"
    (maw / "app.py").write_text(body, encoding="utf-8", newline="\n")
    git(maw, "add", ".")
    git(maw, "commit", "-m", "test: large changed declaration")
    result = implementation_scope(
        maw,
        {
            "prefixes": ["app.py"],
            "default_sha": before,
            "symbols": [{"path": "app.py", "qualified_name": "flow"}],
        },
    )
    view = result["declaration_change_view"]
    assert not view["declaration_comparisons"]["comparisons"][0]["exact_declaration_bytes_equal"]
    delta = view["changed_declarations"][0]["differences"]
    assert not delta["complete"]
    assert delta["comparisons"][0]["omitted_hunks"] > 0
    assert any(source["content"] == body for source in result["sources"].values())
