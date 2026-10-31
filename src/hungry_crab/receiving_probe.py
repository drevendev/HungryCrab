"""Execute an explicitly owned original probe; donor execution is never a probe.

The invoking agent is the provenance trust boundary: originality cannot be
inferred from a hash. This runner captures actual bounded process results and
checks the receiving identity on both sides; it does not certify usefulness.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .analysis_context import fingerprint
from .artifact_read import read_bytes, read_object
from .config_edit import atomic_text
from .errors import CrabError
from .fetch.bounded import ResponseTooLargeError, run_bounded
from .publication_safety import scan_publication

SCHEMA = "hungry-crab.receiving-probe/1"


def run_probe(
    directory: Path,
    maw: Path,
    source: str,
    *,
    author: str,
    expected: str,
    controls: list[str],
    original_receiving_owned: bool,
    timeout: int = 30,
) -> Path:
    """Run only a new agent-authored probe, with explicit host provenance.

    The API accepts original text, never a path into a donor tree. It runs the
    installed interpreter in isolation with the receiving root as cwd. A probe
    may deliberately import trusted receiving code using that root. This is an
    invoked host action, not a miner or a sandbox for untrusted programs.
    """
    from .discovery_session import live_context

    if original_receiving_owned is not True:
        raise CrabError("probe requires explicit original receiving-owned provenance")
    if not isinstance(source, str) or not 0 < len(source.encode()) <= 65_536:
        raise CrabError("original probe source exceeds its byte bounds")
    if not author or len(author) > 200 or not expected or len(expected) > 2000:
        raise CrabError("probe needs bounded author and expected outcome")
    if not controls or len(controls) > 10 or any(not c or len(c) > 500 for c in controls):
        raise CrabError("probe requires bounded explicit control descriptions")
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise CrabError("probe timeout must be in 1..120 seconds")
    context = live_context(maw)
    if directory.resolve().is_relative_to(maw.resolve()):
        raise CrabError("probe output must be outside the receiving worktree")
    directory.mkdir(parents=True, exist_ok=False)
    script = directory / "original-probe.py"
    atomic_text(script, source)
    command = [sys.executable, "-I", "-B", str(script.resolve())]
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    started = datetime.now(UTC).isoformat()
    status, code, stdout, stderr = "executed", None, b"", b""
    try:
        result = run_bounded(
            command,
            cwd=str(maw.resolve()),
            env=env,
            timeout=timeout,
            stdout_limit=65_536,
            stderr_limit=65_536,
            process_group=True,
        )
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired, ResponseTooLargeError):
        status = "unavailable-or-resource-limit"
    try:
        after = live_context(maw)
    except CrabError:
        after = {"worktree": "unknown"}
    if after != context:
        status = "receiving-context-changed"
    # Local outputs can contain private receiving evidence. Never publish them
    # automatically; mark secret findings without putting secret text in reports.
    for name, data in (("stdout.txt", stdout), ("stderr.txt", stderr)):
        atomic_text(directory / name, data.decode("utf-8", "replace"))
    receipt = {
        "schema": SCHEMA,
        "context": context,
        "after_context": after,
        "author": author,
        "original_receiving_owned": True,
        "probe_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "expected": expected,
        "controls": controls,
        "command": command,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "status": status,
        "exit_code": code,
        "artifacts": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("original-probe.py", "stdout.txt", "stderr.txt")
        },
        "publication_requires_review": bool(
            scan_publication("stdout.txt", stdout.decode("utf-8", "replace"))
            or scan_publication("stderr.txt", stderr.decode("utf-8", "replace"))
        ),
        "donor_executed": False,
        "semantic_review": "required",
    }
    receipt["sha256"] = fingerprint(receipt)
    path = directory / "receipt.json"
    atomic_text(path, json.dumps(receipt, indent=2) + "\n")
    return path


def verify_receipt(path: Path, context: dict[str, Any], probe_sha256: str) -> dict[str, Any]:
    receipt = read_object(path)
    identity = receipt.pop("sha256", None)
    if (
        receipt.get("schema") != SCHEMA
        or identity != fingerprint(receipt)
        or receipt.get("context") != context
        or receipt.get("after_context") != context
        or receipt.get("probe_sha256") != probe_sha256
        or receipt.get("status") != "executed"
        or type(receipt.get("exit_code")) is not int
        or receipt.get("original_receiving_owned") is not True
        or not receipt.get("controls")
        or receipt.get("donor_executed") is not False
    ):
        raise CrabError("verification needs an actual receiving-bound executed probe receipt")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "original-probe.py",
        "stdout.txt",
        "stderr.txt",
    }:
        raise CrabError("executed probe artifact set is incomplete")
    for name, digest in artifacts.items():
        if hashlib.sha256(read_bytes(path.parent / name, 65_536)).hexdigest() != digest:
            raise CrabError("executed probe artifact changed")
    return receipt


def verify_design_artifact(artifact: object, context: dict[str, Any]) -> None:
    """Validate an original acceptance document; usefulness still needs semantic review."""
    if not isinstance(artifact, dict):
        raise CrabError("design verification needs an actual acceptance artifact")
    criteria = artifact.get("criteria")
    if (
        artifact.get("receiving_context") != context
        or artifact.get("original_receiving_owned") is not True
        or not isinstance(criteria, list)
        or not 1 <= len(criteria) <= 20
        or any(not isinstance(row, str) or not 1 <= len(row.strip()) <= 2000 for row in criteria)
        or not isinstance(artifact.get("assessment"), str)
        or not 1 <= len(artifact["assessment"].strip()) <= 4000
        or not isinstance(artifact.get("path"), str)
    ):
        raise CrabError("design artifact needs receiving identity, criteria and assessment")
    body = read_bytes(Path(artifact["path"]), 65_536)
    if not body.strip() or hashlib.sha256(body).hexdigest() != artifact.get("sha256"):
        raise CrabError("design acceptance artifact is empty or changed")
