"""Live Deep Bite acceptance: public forge reads, isolated syntax, portable meals; no prey runs."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from hungry_crab.cache import resolve_target
from hungry_crab.feeder import EatOptions, eat
from hungry_crab.fetch.git import GitRunner


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="crab-deep-smoke-"))
    maw = root / "maw"
    maw.mkdir()
    files = {
        "LICENSE": "SPDX-License-Identifier: MIT\n",
        ".crab.yml": "license: MIT\nmode: strict\n",
        "pyproject.toml": '[project]\nname="deep-smoke-maw"\nversion="0.0.0"\n',
        "core.py": "def maw_boundary(): return 1\n",
        "go.mod": "module example.com/deep-smoke\ngo 1.22\n",
        "core.go": "package smoke\nfunc MawBoundary() int { return 1 }\n",
    }
    for name, body in files.items():
        (maw / name).write_text(body, encoding="utf-8")
    git = GitRunner(maw)
    git.run("-c", "init.templateDir=", "init", "--quiet", "--initial-branch=main")
    git.run("add", ".")
    git.run(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--quiet",
        "-m",
        "test: live smoke maw",
    )
    results = []
    for name, prey in (("github", "cli/cli"), ("gitlab", "https://gitlab.com/gitlab-org/cli")):
        protected_signals = name == "github" or bool(os.environ.get("GITLAB_TOKEN"))
        result = eat(
            resolve_target(prey),
            maw,
            EatOptions(
                cache_root=root / "cache",
                out=root / name,
                wiki=False,
                issues=3,
                reviews=3 if protected_signals else 0,
                discussions=3 if name == "github" else 0,
                runs=1 if protected_signals else 0,
                allow_unknown_size=True,
            ),
        )
        bundle = result.out_dir
        manifest = json.loads((bundle / "prey-digest/manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["miners"]) == 15 and all(m["ok"] for m in manifest["miners"])
        assert manifest["coverage"]["healthy"]
        symbols = json.loads((bundle / "prey-digest/symbols.json").read_text(encoding="utf-8"))
        assert symbols["available"] and symbols["symbols"] and symbols["edges"]
        signals = json.loads((bundle / "prey-digest/signals.json").read_text(encoding="utf-8"))
        assert not protected_signals or all(
            signals["channels"][c]["status"] == "available" for c in ("reviews", "runs")
        )
        menu = json.loads((bundle / "menu.json").read_text(encoding="utf-8"))
        assert any(c["category"] == "code" for c in menu["candidates"])
        assert not git.run("status", "--porcelain").strip()
        results.append(
            {
                "forge": name,
                "sha": manifest["prey"]["sha"],
                "symbols": len(symbols["symbols"]),
                "edges": len(symbols["edges"]),
                "symbol_coverage": symbols["coverage"],
                "protected_signals_requested": protected_signals,
                "bundle": str(bundle),
            }
        )
    (root / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"results": results, "evidence": str(root / "results.json")}, indent=2))


if __name__ == "__main__":
    main()
