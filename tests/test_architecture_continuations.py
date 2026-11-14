"""Receiving-owned regressions; no CLI, serialized digest or donor execution."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hungry_crab.miners.architecture import (
    ArchitectureMiner,
    _python_statement_code,
    _python_visible_code,
)
from hungry_crab.miners.base import FileInfo, MineContext, MinerResult


def _mine_python_sources(tmp_path: Path, sources: dict[str, str]) -> MinerResult:
    files = []
    for rel, text in sources.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        files.append(
            FileInfo(
                path=rel,
                name=path.name,
                ext=".py",
                size=len(text.encode("utf-8")),
                language="Python",
                is_code=True,
                vendored=False,
                generated=False,
                binary=False,
                loc=text.count("\n"),
                depth=2,
                lockfile=False,
                manifest_kind=None,
            )
        )
    ctx = MineContext(root=tmp_path, sha="receiving-test", ref="HEAD", label="receiving-test")
    ctx.results["inventory"] = MinerResult("inventory", {}, extra={"files": files})
    return ArchitectureMiner().run(ctx)


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("final_newline", [False, True], ids=["no-eol", "eol"])
def test_continued_python_imports_and_symbols(
    tmp_path: Path, newline: str, final_newline: bool
) -> None:
    source = (
        "from \\\n"
        "    .dependency \\\n"
        "    import VALUE\n"
        "import \\\n"
        "    os\n"
        "class \\\n"
        "    ActualClass:\n"
        "    pass\n"
        "async \\\n"
        "    def \\\n"
        "    actual():\n"
        "    pass\n"
    )
    if not final_newline:
        source = source.removesuffix("\n")
    source = source.replace("\n", newline)
    ast.parse(source)  # These are valid declarations, not merely tokenizer-accepted input.
    sources = {"pkg/__init__.py": source, "pkg/dependency.py": "VALUE = 1\n"}
    result = _mine_python_sources(tmp_path, sources)
    assert result.data["edges"] == [["pkg/__init__.py", "pkg/dependency.py"]]
    assert result.data["graph"]["external_top"] == [{"name": "os", "imports": 1}]
    assert result.data["totals"]["functions"] == 1
    assert result.data["totals"]["classes"] == 1
    assert result.data["public_surface"] == [
        {"path": "pkg/__init__.py", "names": ["ActualClass", "actual"]}
    ]
    assert result.data["lexical_uncertain_files"] == 0
    assert result.warnings == []
    assert (tmp_path / "pkg/__init__.py").read_bytes() == source.encode("utf-8")


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize(
    ("prefix", "statement", "internal"),
    [
        ("if True:", "import os", False),
        ("if True:", "from .dependency import VALUE", True),
        ("value = 1;", "import os", False),
        ("value = 1;", "from .dependency import VALUE", True),
    ],
    ids=["suite-import", "suite-from", "semicolon-import", "semicolon-from"],
)
def test_continued_simple_suites_keep_import_boundaries(
    tmp_path: Path, newline: str, prefix: str, statement: str, internal: bool
) -> None:
    source = f"{prefix} \\\n    {statement}\n".replace("\n", newline)
    ast.parse(source)
    result = _mine_python_sources(
        tmp_path, {"pkg/__init__.py": source, "pkg/dependency.py": "VALUE = 1\n"}
    )
    assert result.data["edges"] == ([["pkg/__init__.py", "pkg/dependency.py"]] if internal else [])
    assert result.data["graph"]["external_top"] == (
        [] if internal else [{"name": "os", "imports": 1}]
    )
    assert result.data["lexical_uncertain_files"] == 0


def test_inline_import_boundaries_exclude_literal_and_comment_lookalikes(tmp_path: Path) -> None:
    source = (
        'payload = "if True: import ghost; from .phantom import VALUE"\n'
        "# if True: import ghost; from .phantom import VALUE\n"
        "if True: import os; \\\n"
        "    from .dependency import VALUE\n"
    )
    ast.parse(source)
    result = _mine_python_sources(
        tmp_path, {"pkg/__init__.py": source, "pkg/dependency.py": "VALUE = 1\n"}
    )
    assert result.data["edges"] == [["pkg/__init__.py", "pkg/dependency.py"]]
    assert result.data["graph"]["external_top"] == [{"name": "os", "imports": 1}]
    assert result.data["lexical_uncertain_files"] == 0


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_continuation_view_preserves_character_offsets(newline: str) -> None:
    source = ('label = "café"\nfrom pkg.real \\\n    import VALUE\n').replace("\n", newline)
    physical, complete = _python_visible_code(source)
    visible = _python_statement_code(physical)
    assert complete
    assert len(visible) == len(source)
    assert visible.index("from") == source.index("from")
    assert visible.index("import") == source.index("import")
    assert [i for i, c in enumerate(physical) if c == "\n"] == [
        i for i, c in enumerate(source) if c == "\n"
    ]
    start = source.index("\\")
    end = start + 1 + len(newline)
    assert visible[start:end] == " " * (end - start)


@pytest.mark.parametrize("prefix", ["", "f", "t"])
def test_continuations_inside_literals_remain_inert(prefix: str) -> None:
    source = (
        f"payload = {prefix}'''hello\\\n"
        "from pkg.ghost import VALUE\n"
        "def phantom():\n    pass\n"
        "'''\n"
        "import os\n"
    )
    physical, complete = _python_visible_code(source)
    visible = _python_statement_code(physical)
    assert complete
    assert "ghost" not in visible
    assert "phantom" not in visible
    assert "import os" in visible
    assert [i for i, c in enumerate(visible) if c == "\n"] == [
        i for i, c in enumerate(source) if c == "\n"
    ]


def test_comment_backslash_does_not_join_the_next_statement() -> None:
    source = "# a comment ending in a backslash \\\nimport os\n"
    physical, complete = _python_visible_code(source)
    visible = _python_statement_code(physical)
    assert complete
    assert visible.endswith("\nimport os\n")


@pytest.mark.parametrize("source", ["from \\\n", 'value = "unfinished\\\n'])
def test_incomplete_continuations_still_report_uncertain_coverage(source: str) -> None:
    physical, complete = _python_visible_code(source)
    visible = _python_statement_code(physical)
    assert not complete
    assert visible == " " * len(source)
