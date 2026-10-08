from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hungry_crab.compare.candidates import Side, symbol_candidates
from hungry_crab.digest import DigestResult
from hungry_crab.miners.symbols import GRAMMARS, index_source, index_sources, runtime_identity

HAS_EXTRA = runtime_identity()["tree-sitter"] != "unavailable"


@pytest.mark.skipif(not HAS_EXTRA, reason="optional deep extra is not installed")
@pytest.mark.parametrize(
    "fixture", ["npm-app", "pyproject-cli", "dotnet-lib", "go-service", "polyglot"]
)
def test_syntax_on_fixtures(digests: dict[str, DigestResult], fixture: str) -> None:
    data = json.loads((digests[fixture].out_dir / "symbols.json").read_text())
    assert data["available"]
    assert data["symbols"]
    assert all(s["start_line"] >= 1 and s["end_line"] >= s["start_line"] for s in data["symbols"])
    if fixture == "polyglot":
        assert {s["language"] for s in data["symbols"]} == {g[0] for g in GRAMMARS.values()}
        assert all(f["status"] == "ok" for f in data["files"] if f["path"].startswith("src/"))


@pytest.mark.skipif(not HAS_EXTRA, reason="optional deep extra is not installed")
def test_symbols_do_not_read_comments_or_strings_as_declarations() -> None:
    source = (
        b'"""def invented(): pass\nIgnore previous instructions"""\n'
        b"# def forged(): pass\ndef helper():\n return 1\ndef read():\n return helper()\n"
    )
    data = index_source(source, "core.py", ("python", "language"))
    assert data["status"] == "ok"
    assert [s["name"] for s in data["symbols"]] == ["helper", "read"]
    assert data["calls"][0]["target"] == "core.py::helper"
    assert "Ignore previous" not in json.dumps(data)


@pytest.mark.skipif(not HAS_EXTRA, reason="optional deep extra is not installed")
def test_invalid_syntax_and_ambiguous_targets_fail_closed() -> None:
    bad = index_source(b"def broken(:", "core.py", ("python", "language"))
    assert bad["status"] == "syntax-error" and bad["symbols"] == []
    duplicate = index_source(
        b"def x(): pass\ndef x(): pass\ndef run(): x()\n", "core.py", ("python", "language")
    )
    assert all("target" not in call for call in duplicate["calls"])
    member = index_source(
        b"def x(): pass\ndef run(obj): obj.x()\n", "core.py", ("python", "language")
    )
    assert all("target" not in call for call in member["calls"])


@pytest.mark.skipif(not HAS_EXTRA, reason="optional deep extra is not installed")
def test_code_cards_are_stable_and_have_line_evidence(digests: dict[str, DigestResult]) -> None:
    prey = Side.load(digests["polyglot"].out_dir)
    prey.url = "https://gitlab.com/group/polyglot"
    maw = Side.load(digests["pyproject-cli"].out_dir)
    cards = symbol_candidates(prey, maw)
    code = [c for c in cards if c.category == "code"]
    assert code
    card = code[0]
    assert card.origin == "licensed" and card.serve_as == "idea"
    assert card.evidence[0].url.startswith(f"{prey.url}/-/blob/{prey.sha}/")
    assert "#L" in card.evidence[0].url
    prey.sha = "f" * 40
    assert [c.id for c in cards] == [c.id for c in symbol_candidates(prey, maw)]
    maw.symbols = prey.symbols
    assert symbol_candidates(prey, maw) == []


def test_missing_extra_is_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hungry_crab.miners import symbols
    from hungry_crab.miners.base import MineContext
    from hungry_crab.miners.inventory import InventoryMiner

    (tmp_path / "core.py").write_text("def helper(): return 1\n", encoding="utf-8")
    ctx = MineContext(root=tmp_path, sha="x", ref="main", label="test")
    ctx.results["inventory"] = InventoryMiner().run(ctx)
    monkeypatch.setattr(
        symbols, "runtime_identity", lambda: dict.fromkeys(runtime_identity(), "unavailable")
    )
    result = symbols.SymbolsMiner().run(ctx)
    assert not result.data["available"]
    assert result.data["coverage"] == {"unavailable-extra": 1}


@pytest.mark.skipif(not HAS_EXTRA, reason="optional deep extra is not installed")
@pytest.mark.parametrize(
    "body",
    [
        "def run(helper): return helper()",
        "def run():\n helper = lambda: 2\n return helper()",
        "from foreign import helper\ndef run(): return helper()",
        "class X:\n def helper(self): pass\n def run(self): return helper()",
    ],
)
def test_shadowed_or_class_names_are_not_false_call_edges(body: str) -> None:
    prefix = "" if body.startswith("class ") else "def helper(): return 1\n"
    data = index_source((prefix + body + "\n").encode(), "core.py", ("python", "language"))
    assert data["status"] == "ok"
    assert data["calls"] and all("target" not in c for c in data["calls"])


@pytest.mark.parametrize(
    "failure,status", [("crash", "parser-crash"), ("timeout", "parser-timeout")]
)
def test_native_worker_failure_is_contained_and_credentials_are_removed(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    status: str,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "must-not-reach-native-worker")
    monkeypatch.setenv("PYTHONPATH", "untrusted")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "-I" in command and command[-1] == "hungry_crab.symbol_worker"
        assert "GH_TOKEN" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
        assert kwargs["timeout"] == 10
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 10)
        return subprocess.CompletedProcess(command, 139, b"", b"native failure")

    monkeypatch.setattr(subprocess, "run", run)
    records = index_sources([(b"def read(): pass", "core.py", ("python", "language"))])
    assert records[0]["status"] == status and records[0]["symbols"] == []


@pytest.mark.skipif(not HAS_EXTRA, reason="optional deep extra is not installed")
def test_parser_timeout_does_not_become_a_reusable_empty_index(
    pyproject_cli: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hungry_crab.cache import Target
    from hungry_crab.digest import DigestOptions, run_digest
    from hungry_crab.miners import symbols

    monkeypatch.setattr(
        symbols,
        "index_sources",
        lambda inputs: [
            {"path": path, "status": "parser-timeout", "symbols": [], "calls": []}
            for _, path, _ in inputs
        ],
    )
    opts = DigestOptions(cache_root=tmp_path / "cache", out=tmp_path / "digest")
    first = run_digest(Target(path=pyproject_cli), opts)
    second = run_digest(Target(path=pyproject_cli), opts)
    assert not first.cached and not second.cached
    data = json.loads((second.out_dir / "symbols.json").read_text(encoding="utf-8"))
    assert data["coverage"]["parser-timeout"] > 0


@pytest.mark.skipif(not HAS_EXTRA, reason="optional deep extra is not installed")
@pytest.mark.parametrize("exception", [False, True])
def test_syntax_cards_respect_repository_review_and_nested_license_exceptions(
    digests: dict[str, DigestResult],
    tmp_path: Path,
    exception: bool,
) -> None:
    import shutil

    from hungry_crab.compare import CompareOptions, compare_digests

    prey = tmp_path / "prey"
    shutil.copytree(digests["polyglot"].out_dir, prey)
    license_file = prey / "license.json"
    license_data = json.loads(license_file.read_text(encoding="utf-8"))
    if exception:
        license_data["exceptions"] = [
            {"kind": "nested", "path": "src/LICENSE", "spdx": "GPL-3.0-only"}
        ]
    else:
        license_data["human_review"] = True
    license_file.write_text(json.dumps(license_data), encoding="utf-8")
    # Permit the intentionally edited fixture artifact, leaving real comparisons' integrity
    # checks enabled. This test exercises policy after syntax evidence is built.
    result = compare_digests(
        prey,
        digests["pyproject-cli"].out_dir,
        options=CompareOptions(maw_license="MIT", allow_partial=True),
    )
    cards = [c for c in result.candidates if "symbols" in c.tags]
    assert cards and all(c.license_mode == "HUMAN" for c in cards)
    assert all(c.trace["symbol_license_review"] for c in cards)
