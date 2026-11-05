"""Original receiving-only Python cases for typed APIs, with fixed call witnesses."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

SAFE_IMPORTS = frozenset({"math", "pathlib", "dataclasses", "json", "io", "types"})
FORBIDDEN = frozenset(
    {
        "print",
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "setattr",
        "delattr",
    }
)

PYTHON_CASE_CONTRACT = (
    "For runtime_inputs.probe_format=original-python, cases_json is exactly a JSON object "
    "with python_probe string, not cases/helpers. It is ORIGINAL receiving-only Python, "
    "with no donor code or test data. Import only listed runtime_inputs.imports and "
    "math/pathlib/dataclasses/json/io/types. fixture_root is an owned temporary Path; "
    "do not assign it or inspect home/environment. Construct actual receiving types. "
    "Call record_case(label, role, zero_argument_callback, checks) exactly twice: "
    "role target and positive-control. Call the required target inside EACH callback, "
    "not during setup. A callback may perform a receiving-only multi-call comparison. "
    "The fixed runner encodes its return as {value:...} or its exception, measures "
    "per-case actual target entry, and applies JSON pointer checks with exactly one "
    "equals/contains/not_contains. Checks assert desired correct behavior. "
    "Do not print, serialize cases yourself, use sys/os/network/process APIs, call "
    "sleep, evaluate dependency text, or replace the runner. Keep the source <=7000 "
    "characters. For ordinary JSON runtime inputs, use cases/helpers as usual.\n"
)


def python_probe(source: str, target: dict[str, Any], imports: list[str]) -> str:
    """Validate an owned program, then wrap it in the fixed existing probe contract."""
    if not isinstance(source, str) or not 1 <= len(source.encode()) <= 12000:
        raise ValueError("original receiving Python source exceeds its allowance")
    allowed = SAFE_IMPORTS | frozenset(imports)
    tree = ast.parse(source)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Global | ast.Nonlocal):
            raise ValueError("original receiving probe cannot replace global runner state")
        declared = (
            node.name
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            else node.asname
            if isinstance(node, ast.alias)
            else None
        )
        if declared and (
            declared.startswith("_crab_") or declared in {"record_case", "fixture_root"}
        ):
            raise ValueError("original receiving probe replaces its runner")
        if isinstance(node, ast.Name):
            if node.id.startswith("_crab_") or node.id in FORBIDDEN:
                raise ValueError("original receiving probe accesses a forbidden runner name")
            if not isinstance(node.ctx, ast.Load) and node.id in {"record_case", "fixture_root"}:
                raise ValueError("original receiving probe replaces its runner")
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("__") or node.attr in {"home", "cwd", "sleep", "system", "popen"}
        ):
            raise ValueError("unsupported original receiving probe attribute")
        if isinstance(node, ast.Import) and any(alias.name not in allowed for alias in node.names):
            raise ValueError("original receiving probe imports an unapproved module")
        if isinstance(node, ast.ImportFrom) and (
            node.level or node.module not in allowed or any(a.name == "*" for a in node.names)
        ):
            raise ValueError("original receiving probe imports an unapproved module")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            count += node.func.id == "record_case"
    if count != 2:
        raise ValueError("original Python probe requires exactly two explicit record_case calls")
    from hungry_crab import case_worker

    worker = Path(case_worker.__file__).resolve()
    header = (
        """import json as _crab_json
import sys as _crab_sys
import tempfile as _crab_tempfile
import hashlib as _crab_hashlib
import runpy as _crab_runpy
from pathlib import Path as _crab_Path
_crab_worker = """
        + repr(str(worker))
        + """
if _crab_hashlib.sha256(_crab_Path(_crab_worker).read_bytes()).hexdigest() != """
        + repr(hashlib.sha256(worker.read_bytes()).hexdigest())
        + """:
    raise RuntimeError('trusted receiving case runner changed')
_crab_namespace = _crab_runpy.run_path(_crab_worker, run_name='crab_typed_case_support')
_crab_Witness, _crab_encode = _crab_namespace['CallWitness'], _crab_namespace['encode']
_crab_root = _crab_Path.cwd()
_crab_rows = []
_crab_target = """
        + repr(target)
        + """
def record_case(label, role, callback, checks):
    if role not in {'target', 'positive-control'} or not isinstance(label, str):
        raise ValueError('invalid original case identity')
    if not isinstance(checks, list) or not 1 <= len(checks) <= 12:
        raise ValueError('bounded original case checks required')
    witness = _crab_Witness(_crab_root / _crab_target['path'], _crab_target['qualified_name'])
    _crab_sys.setprofile(witness)
    try:
        try:
            actual = {'value': _crab_encode(callback())}
        except Exception as error:
            actual = {'exception': {'type': type(error).__name__, 'message': str(error)[:1000]},
                      'exception_origin': witness.exception_origin(error, _crab_root)}
    finally:
        _crab_sys.setprofile(witness.previous)
    observed_checks = []
    for check in checks:
        operations = [key for key in ('equals', 'contains', 'not_contains') if key in check]
        if len(operations) != 1 or not isinstance(check.get('pointer'), str):
            raise ValueError('invalid original pointer predicate')
        if not check['pointer'].startswith('/'):
            raise ValueError('original pointer must start with slash')
        operation = operations[0]
        expected = check[operation]
        observed = actual
        try:
            for part in check['pointer'].split('/')[1:]:
                key = part.replace('~1', '/').replace('~0', '~')
                observed = observed[int(key)] if isinstance(observed, list) else observed[key]
            passed = observed == expected if operation == 'equals' else expected in observed
            if operation == 'not_contains':
                passed = not passed
            error = None
        except (KeyError, IndexError, TypeError, ValueError):
            observed, passed, error = None, False, 'missing-pointer-or-incompatible-predicate'
        observed_checks.append({'pointer': check['pointer'], 'expected': expected,
                                'observed': observed, 'passed': passed, 'check_error': error})
    _crab_rows.append({'label': label, 'role': role, 'actual': actual,
                       'target_called': witness.called, 'checks': observed_checks,
                       'passed': all(c['passed'] for c in observed_checks)})
_crab_temporary = _crab_tempfile.TemporaryDirectory(prefix='crab-original-typed-')
fixture_root = _crab_Path(_crab_temporary.name)
"""
    )
    footer = """
if len(_crab_rows) != 2 or {row['role'] for row in _crab_rows} != {'target','positive-control'}:
    raise ValueError('original target and positive control were not both recorded')
print(_crab_json.dumps({'schema':'hungry-crab.receiving-cases-result/1', 'cases':_crab_rows,
                       'human_acceptance':None,'donor_executed':False}))
_crab_temporary.cleanup()
"""
    return header + source + "\n" + footer
