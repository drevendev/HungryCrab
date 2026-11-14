"""Original receiving-only export declaration regressions, without CLI artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import hungry_crab.miners.python_exports as exports_module
from hungry_crab.miners.architecture import ArchitectureMiner, _python_visible_code
from hungry_crab.miners.base import FileInfo, MineContext, MinerResult
from hungry_crab.miners.python_exports import python_exports


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('__all__ = ["alpha", "beta"]\n', ["alpha", "beta"]),
        ('__all__ = ["alpha", # COMMENT_SENTINEL\n "beta"]\n', ["alpha", "beta"]),
        ('__all__ = ["al" "pha", "b\\u0065ta"]\n', ["alpha", "beta"]),
        ('__all__ = ("alpha", "beta",)\n', ["alpha", "beta"]),
        ('__all__ = ("alpha",)\n', ["alpha"]),
        ('__all__ = [r"a\\b", u"café"]\n', ["a\\b", "café"]),
        ('__all__ = ["a,b", "a]b", "a)b"]\n', ["a,b", "a]b", "a)b"]),
        ('__all__ = ["""alpha"""]\n', ["alpha"]),
        ('__all__ = \\\n ["alpha"]\n', ["alpha"]),
        ('value = 1; __all__ = ["alpha"]; other = 2\n', ["alpha"]),
        ("__all__ = []\n", []),
        ("__all__ = ()\n", []),
        ('__all__ = ["alpha"]', ["alpha"]),
    ],
)
def test_literal_exports(source: str, expected: list[str]) -> None:
    visible, complete = _python_visible_code(source)
    assert complete
    assert python_exports(source, visible) == (expected, None)
    windows = source.replace("\n", "\r\n")
    assert python_exports(windows, _python_visible_code(windows)[0]) == (expected, None)


@pytest.mark.parametrize(
    "source",
    [
        'def actual():\n    __all__ = ["local"]\n',
        'def actual(): __all__ = ["local"]\n',
        'async def actual():\n    __all__ = ["local"]\n',
        'class Actual:\n    __all__ = ["member"]\n',
        'if True:\n    def actual():\n        __all__ = ["local"]\n',
        "def actual(__all__): pass\n",
        "def actual(*__all__): pass\n",
        "def actual(**__all__): pass\n",
        "def actual(__all__: list[str] = ()): pass\n",
        "def actual(a, /, *, __all__=()): pass\n",
        "class Actual:\n    def method(self, __all__): pass\n",
        'obj.__all__ = ["attribute"]\n',
        'not__all__ = ["longer_name"]\n',
        'payload = "__all__ = [\\"inert\\"]"\n',
        '# __all__ = ["comment"]\n',
    ],
)
def test_non_module_declarations_are_absent(source: str) -> None:
    visible, complete = _python_visible_code(source)
    assert complete
    assert python_exports(source, visible) == (None, None)


@pytest.mark.parametrize(
    "source",
    [
        '__all__ = [factory("phantom")]\n',
        "__all__ = [name]\n",
        "__all__ = [x for x in names]\n",
        "__all__ = [*names]\n",
        '__all__ = [b"alpha"]\n',
        '__all__ = [f"alpha"]\n',
        '__all__ = [t"alpha"]\n',
        '__all__ = ["alpha".upper()]\n',
        '__all__ = ["alpha"] + ["beta"]\n',
        '__all__ = ("alpha")\n',
        '__all__ = "alpha", "beta"\n',
        '__all__ = (["alpha"])\n',
        '__all__ = "alpha"\n',
        '__all__: list[str] = ["alpha"]\n',
        '__all__ = {"alpha"}\n',
        "__all__ = [1]\n",
        '__all__ = ["alpha"]; __all__ = ["beta"]\n',
        '__all__ = ["alpha"]\n__all__ += ["beta"]\n',
        '__all__ = ["alpha"]\n__all__.append("beta")\n',
        'if True:\n    __all__ = ["alpha"]\n',
        'if True: __all__ = ["alpha"]\n',
        "from package import __all__\n",
        "def __all__(): pass\n",
        '__all__ = ["alpha"]\ndel __all__\n',
        '__all__ = ["alpha"]\ndef actual(x=__all__.append("beta")): pass\n',
        'def actual(x=(__all__ := ["alpha"])): pass\n',
        '__all__ = ["alpha"]\ndef actual(x: __all__): pass\n',
        '__all__ = ["alpha"]\ndef actual() -> (__all__): pass\n',
        '__all__ = ["alpha"]\ndef actual(x=2 * __all__): pass\n',
        '__all__ = ["alpha"]\ndef actual(x=2 ** __all__): pass\n',
        '__all__ = ["alpha"]\ndef actual(x: 2 * __all__): pass\n',
        '__all__ = ["alpha"]\ndef actual(x=Mutator() * __all__): pass\n',
        '__all__ = ["alpha"]\nclass Actual(factory(__all__)): pass\n',
        '__all__ = ["alpha"]\nclass Actual: __all__.append("beta")\n',
        '__all__ = ["alpha"]\nclass Actual:\n    __all__.append("beta")\n',
        'class Actual:\n    global __all__\n    __all__ = ["beta"]\n',
        '__all__ = ["alpha"]\nclass Actual:\n    global __all__\n    __all__ = ["beta"]\n',
        (
            '__all__ = ["alpha"]\nclass Actual:\n'
            '    def method(self, x=__all__.append("beta")): pass\n'
        ),
        (
            '__all__ = ["alpha"]\nclass Outer:\n    __all__ = ["local"]\n'
            '    class Inner:\n        __all__.append("beta")\n'
        ),
        '__all__ = ["\\ud800"]\n',
        '__all__ = ["\\udfff"]\n',
    ],
)
def test_unsupported_exports_are_unknown_without_expression_text(source: str) -> None:
    names, warning = python_exports(source, _python_visible_code(source)[0])
    assert names == []
    assert warning is not None
    assert "phantom" not in warning


def test_local_bindings_do_not_override_the_module_declaration() -> None:
    source = (
        '__all__ = ["actual"]\n'
        'def actual():\n    __all__ = ["local"]\n'
        'class Actual:\n    __all__ = ["member"]\n'
    )
    assert python_exports(source, _python_visible_code(source)[0]) == (["actual"], None)


@pytest.mark.parametrize("escape", [r"\ud800", r"\udfff", r"\ud800\udc00"])
def test_surrogates_never_reach_utf8_artifacts(tmp_path: Path, escape: str) -> None:
    source = f'__all__ = ["{escape}"]\ndef actual(): pass\n'
    names, warning = python_exports(source, _python_visible_code(source)[0])
    assert names == []
    assert warning == "decoded string is not valid UTF-8"
    path = tmp_path / "__init__.py"
    path.write_bytes(source.encode())
    info = FileInfo(
        path=path.name, name=path.name, ext=".py", size=path.stat().st_size,
        language="Python", is_code=True, vendored=False, generated=False,
        binary=False, loc=2, depth=1, lockfile=False, manifest_kind=None,
    )  # fmt: skip
    ctx = MineContext(root=tmp_path, sha="receiving-test", ref="HEAD", label="receiving-test")
    ctx.results["inventory"] = MinerResult("inventory", {}, extra={"files": [info]})
    result = ArchitectureMiner().run(ctx)
    assert result.data["public_surface"] == []
    assert result.doc is not None
    assert json.dumps(result.data, ensure_ascii=False).encode("utf-8")
    assert result.doc.render(3500).encode("utf-8")
    assert result.warnings and all(w.encode("utf-8") for w in result.warnings)


@pytest.mark.parametrize(
    ("limit", "value", "source", "message"),
    [
        ("MAX_TOKENS", 3, '__all__ = ["alpha"]\n', "token limit"),
        ("MAX_NAMES", 1, '__all__ = ["alpha", "beta"]\n', "element limit"),
        ("MAX_LITERAL_CHARS", 3, '__all__ = ["alpha"]\n', "string literal limit"),
        ("MAX_SCOPES", 1, 'if True:\n    __all__ = ["alpha"]\n', "scope limit"),
    ],
)
def test_export_work_is_bounded(
    monkeypatch: pytest.MonkeyPatch, limit: str, value: int, source: str, message: str
) -> None:
    monkeypatch.setattr(exports_module, limit, value)
    names, warning = python_exports(source, _python_visible_code(source)[0])
    assert names == []
    assert warning is not None and message in warning


@pytest.mark.parametrize(
    ("declaration", "expected", "warned"),
    [
        ('__all__ = ["alpha", # COMMENT_SENTINEL\n "beta"]\n', ["alpha", "beta"], False),
        ('__all__ = ["al" "pha", "b\\u0065ta"]\n', ["alpha", "beta"], False),
        ('__all__ = ["beta", "alpha", "alpha"]\n', ["alpha", "beta"], False),
        ("__all__ = []\n", [], False),
        ('__all__ = [factory("phantom")]\n', [], True),
        ('__all__ = ["alpha"]\n__all__ += ["beta"]\n', [], True),
        ('if True:\n    __all__ = ["alpha"]\n', [], True),
        ("", ["actual"], False),
    ],
)
def test_architecture_export_policy(
    tmp_path: Path, declaration: str, expected: list[str], warned: bool
) -> None:
    source = declaration + "def actual():\n    pass\n"
    path = tmp_path / "__init__.py"
    path.write_bytes(source.encode())
    info = FileInfo(
        path=path.name,
        name=path.name,
        ext=".py",
        size=path.stat().st_size,
        language="Python",
        is_code=True,
        vendored=False,
        generated=False,
        binary=False,
        loc=source.count("\n"),
        depth=1,
        lockfile=False,
        manifest_kind=None,
    )
    ctx = MineContext(root=tmp_path, sha="receiving-test", ref="HEAD", label="receiving-test")
    ctx.results["inventory"] = MinerResult("inventory", {}, extra={"files": [info]})
    result = ArchitectureMiner().run(ctx)
    assert result.data["public_surface"] == (
        [{"path": path.name, "names": expected}] if expected else []
    )
    assert result.data["totals"]["functions"] == 1
    assert result.data["lexical_uncertain_files"] == 0
    assert bool(result.warnings) is warned
    if warned:
        assert "public names omitted" in result.warnings[0]
        assert "phantom" not in result.warnings[0]
