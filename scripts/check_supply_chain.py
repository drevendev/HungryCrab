"""Refuse floating executable action references and unpinned skills-installer inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def errors(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for path in [*sorted((root / ".github" / "workflows").glob("*.yml")), root / "action.yml"]:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\buses:\s*([^\s#]+)", text):
            value = match.group(1)
            if value.startswith(("./", "$/")):
                continue
            if not re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", value):
                problems.append(f"{path.name}: executable action must have a commit SHA: {value}")
        document = yaml.safe_load(text)
        for job in document.get("jobs", {}).values():
            for step in job.get("steps", []):
                if (
                    str(step.get("uses", "")).startswith("actions/checkout@")
                    and step.get("with", {}).get("persist-credentials") is not False
                ):
                    problems.append(f"{path.name}: checkout must disable persisted credentials")
    package = json.loads((root / "scripts" / "skills-installer" / "package.json").read_text())
    lock = json.loads((root / "scripts" / "skills-installer" / "package-lock.json").read_text())
    for name, version in package["dependencies"].items():
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            problems.append(f"skills installer dependency {name} must have an exact version")
    for name, entry in lock["packages"].items():
        if name and (
            not entry.get("integrity", "").startswith("sha512-")
            or not entry.get("resolved", "").startswith("https://registry.npmjs.org/")
        ):
            problems.append(f"skills installer lock entry {name} must have registry integrity")
    return problems


if __name__ == "__main__":
    failures = errors()
    for failure in failures:
        print(failure)
    raise SystemExit(bool(failures))
