"""Check the combined workflow as inert data; never merge a branch or execute it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


left, right = sys.argv[1:]
subprocess.run(["git", "fetch", "--no-tags", "origin", left, right], check=True)
tree = subprocess.check_output(
    ["git", "merge-tree", "--write-tree", left, right], text=True
).strip()
source = subprocess.check_output(
    ["git", "show", tree + ":.github/workflows/ci.yml"], text=True
)
workflow = yaml.load(source, Loader=UniqueLoader)
jobs = workflow["jobs"]
assert {"lint", "test", "discovery-operations", "acquisition-lifecycle"} <= jobs.keys()
assert jobs["test"]["needs"] == ["lint", "discovery-operations"]
assert "test_discovery_review_regressions.py" in jobs["discovery-operations"]["steps"][-1]["run"]
assert "test_acquisition_dispatch.py" in jobs["acquisition-lifecycle"]["steps"][-1]["run"]
Path("merged-ci.yml").write_text(source, encoding="utf-8")
print(f"PASS: {left} + {right} -> {tree}")
print("Both focused matrices and full-suite dependencies retained; no conflicts or duplicate keys.")
