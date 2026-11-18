"""Read public Microsoft symbol metadata for two recovered historical module identities.

This Actions-only diagnostic accepts no dump, process, arbitrary URL or executable input.
The resulting public records aid address attribution; they do not unwind a call stack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

SYMBOLS = (
    ("ntdll.pdb", "44294CBD55E8CD67B3D56B689F03152D1"),
    ("ws2_32.pdb", "9DC73321ED08893007DF02E641F44B871"),
)
MAX_PDB_BYTES = 64 * 1024 * 1024


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    destination = parser.parse_args().out
    destination.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, object]] = []
    manifest: dict[str, object] = {
        "schema": 1,
        "status": "CAPTURE_IN_PROGRESS",
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "workflow_sha": os.environ.get("GITHUB_SHA"),
        "records": records,
        "interpretation": "Public symbol records only; no unwound stack or causal finding",
    }

    def save() -> None:
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    save()
    try:
        tool = next(
            (
                path
                for name in ("llvm-pdbutil", *(f"llvm-pdbutil-{n}" for n in range(22, 15, -1)))
                if (path := shutil.which(name))
            ),
            None,
        )
        if tool is None:
            raise RuntimeError("runner has no supported llvm-pdbutil; symbol capture unavailable")
        manifest["tool"] = subprocess.run(
            [tool, "--version"], capture_output=True, check=True, timeout=10
        ).stdout.decode("utf-8", errors="replace")
        for filename, identity in SYMBOLS:
            url = f"https://msdl.microsoft.com/download/symbols/{filename}/{identity}/{filename}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(MAX_PDB_BYTES + 1)
            if len(data) > MAX_PDB_BYTES:
                raise ValueError("public PDB exceeds the diagnostic byte limit")
            pdb = destination / filename
            pdb.write_bytes(data)
            result = subprocess.run(
                [tool, "dump", "--summary", "--section-headers", "--publics", str(pdb)],
                capture_output=True,
                check=True,
                timeout=120,
            )
            (destination / f"{filename}.txt").write_bytes(result.stdout)
            (destination / f"{filename}.stderr.txt").write_bytes(result.stderr)
            records.append(
                {
                    "pdb": filename,
                    "expected_symbol_id": identity,
                    "source": url,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            save()
        manifest["status"] = "PUBLIC_RECORDS_CAPTURED"
    except Exception as error:
        manifest["status"] = "FAILED"
        manifest["error"] = str(error)
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
