"""Opt-in receiving imports preserve authored source and isolated execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_discovery_inputs import repository

from hungry_crab.cli import main
from hungry_crab.errors import CrabError
from hungry_crab.receiving_probe import run_probe, verify_receipt


def test_explicit_receiving_roots_leave_default_and_environment_isolation_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maw = repository(
        tmp_path / "owned source \u00e9",
        {
            "owned_probe_flat_069.py": 'VALUE = "flat"\n',
            "src/owned_probe_src_069/__init__.py": 'VALUE = "src"\n',
        },
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "host_probe_poison_069.py").write_text('raise RuntimeError("do not import")\n')
    monkeypatch.setenv("PYTHONPATH", str(outside))
    source = (
        "import importlib.util, os, sys\n"
        "from pathlib import Path\n"
        "from owned_probe_flat_069 import VALUE as flat\n"
        "from owned_probe_src_069 import VALUE as packaged\n"
        'assert (flat, packaged) == ("flat", "src")\n'
        'assert importlib.util.find_spec("host_probe_poison_069") is None\n'
        'assert os.environ.get("PYTHONPATH") is None\n'
        'assert Path(sys.argv[0]).name == "original-probe.py"\n'
        'print("flat/src imports and negative environment control passed")\n'
    )
    common = {
        "author": "fixture author",
        "expected": "flat and src modules import; inherited PYTHONPATH remains absent",
        "controls": ["positive owned flat/src imports", "negative external environment import"],
        "original_receiving_owned": True,
    }
    default = run_probe(tmp_path / "default", maw, source, **common)
    original = json.loads(default.read_text(encoding="utf-8"))
    assert original["exit_code"] != 0 and not original["import_receiving"]
    assert "ModuleNotFoundError" in (default.parent / "stderr.txt").read_text()
    enabled = run_probe(tmp_path / "enabled", maw, source, import_receiving=True, **common)
    record = json.loads(enabled.read_text(encoding="utf-8"))
    assert record["exit_code"] == 0 and record["import_receiving"]
    assert record["context"] == record["after_context"]
    assert (enabled.parent / "original-probe.py").read_text(encoding="utf-8") == source
    assert verify_receipt(enabled, record["context"], record["probe_sha256"])["import_receiving"]
    assert not list(maw.rglob("__pycache__"))


def test_public_probe_plan_can_explicitly_enable_receiving_imports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    maw = repository(tmp_path / "maw", {"owned_cli_probe_069.py": "VALUE = 42\n"})
    source = tmp_path / "probe-source.py"
    source.write_text("from owned_cli_probe_069 import VALUE\nassert VALUE == 42\nprint(VALUE)\n")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": str(source),
                "author": "fixture host",
                "expected": "42",
                "controls": ["actual declared receiving module imports"],
                "original_receiving_owned": True,
                "import_receiving": True,
            }
        )
    )
    assert (
        main(
            [
                "discover",
                "probe",
                str(tmp_path / "execution"),
                "--maw",
                str(maw),
                "--input",
                str(plan),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["exit_code"] == 0 and result["import_receiving"]


@pytest.mark.parametrize("value", [1, "true", None])
def test_import_mode_is_not_coerced(tmp_path: Path, value: object) -> None:
    with pytest.raises(CrabError, match="explicit boolean"):
        run_probe(
            tmp_path / "execution",
            tmp_path / "not-opened",
            "print(1)",
            author="fixture",
            expected="not executed",
            controls=["invalid option fails before receiving reads/effects"],
            original_receiving_owned=True,
            import_receiving=value,  # type: ignore[arg-type]
        )
    assert not (tmp_path / "execution").exists()
