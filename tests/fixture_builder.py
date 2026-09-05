"""Build the synthetic fixture repositories with real git history.

Trees live in ``tests/fixtures/repos/<name>/``; a file named ``X.fixture`` becomes ``X`` when
copied, so fixture ``CLAUDE.md`` / ``.gitignore`` files never affect this repository itself.
Histories live in ``tests/fixtures/histories/<name>.json`` as an ordered list of commits.
Every file in a tree must be introduced by some commit, which keeps trees and histories in sync.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPOS_DIR = FIXTURES_DIR / "repos"
HISTORIES_DIR = FIXTURES_DIR / "histories"
FIXTURE_NAMES = ("npm-app", "pyproject-cli", "dotnet-lib")
SUFFIX = ".fixture"
DEFAULT_AUTHOR = "Fixture Bot <fixture@example.com>"

_COMMENT_BY_EXT = {
    ".ts": "//",
    ".tsx": "//",
    ".js": "//",
    ".mjs": "//",
    ".cs": "//",
    ".go": "//",
    ".rs": "//",
    ".java": "//",
    ".kt": "//",
    ".py": "#",
    ".yml": "#",
    ".yaml": "#",
    ".toml": "#",
    ".sh": "#",
    ".ps1": "#",
    ".cfg": "#",
    ".ini": "#",
    ".html": "<!--",
    ".css": "/*",
}


def _split_author(text: str) -> tuple[str, str]:
    name, _, rest = text.partition("<")
    return name.strip(), rest.rstrip(">").strip()


def git(
    repo: Path,
    *args: str,
    date: str | None = None,
    author: str | None = None,
) -> str:
    """Run git with an isolated global config and a fixed identity."""
    empty_config = repo.parent / ".empty-gitconfig"
    if not empty_config.exists():
        empty_config.write_text("", encoding="utf-8")
    name, email = _split_author(author or DEFAULT_AUTHOR)
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
    )
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    command = [
        "git",
        "-c", "commit.gpgsign=false",
        "-c", "tag.gpgsign=false",
        "-c", "core.autocrlf=false",
        "-c", "core.safecrlf=false",
        "-c", "init.templateDir=",
        *args,
    ]  # fmt: skip
    proc = subprocess.run(
        command,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}:\n{proc.stderr}")
    return proc.stdout


def tree_files(tree: Path) -> list[str]:
    """Destination-relative POSIX paths of every file in a fixture tree."""
    files: list[str] = []
    for path in sorted(tree.rglob("*")):
        if path.is_file():
            rel = path.relative_to(tree).as_posix()
            files.append(rel.removesuffix(SUFFIX))
    return files


def source_path(tree: Path, rel: str) -> Path:
    plain = tree / rel
    if plain.is_file():
        return plain
    suffixed = tree / f"{rel}{SUFFIX}"
    if suffixed.is_file():
        return suffixed
    raise KeyError(f"fixture file {rel!r} not found under {tree}")


def touch(path: Path, counter: int) -> None:
    """Append a harmless, language-appropriate line so the file shows up as changed."""
    ext = path.suffix.lower()
    if ext == ".json":
        raise ValueError(f"cannot touch JSON file {path}; use a source or text file")
    marker = _COMMENT_BY_EXT.get(ext, "")
    if marker == "<!--":
        line = f"<!-- touched {counter} -->"
    elif marker == "/*":
        line = f"/* touched {counter} */"
    elif marker:
        line = f"{marker} touched {counter}"
    else:
        line = f"touched {counter}"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def build_fixture(name: str, dest: Path) -> Path:
    tree = REPOS_DIR / name
    spec = json.loads((HISTORIES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    default = spec.get("default_branch", "main")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    git(dest, "init", "-q", "-b", default)

    remaining = set(tree_files(tree))
    branches = {default}
    current = default
    touches = 0
    for commit in spec["commits"]:
        branch = commit.get("branch", default)
        if branch != current:
            if branch in branches:
                git(dest, "checkout", "-q", branch)
            else:
                git(dest, "checkout", "-q", "-b", branch, commit.get("from", current))
                branches.add(branch)
            current = branch
        adds = commit.get("add", [])
        if adds == ["*"]:
            adds = sorted(remaining)
        for rel in adds:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path(tree, rel), target)
            remaining.discard(rel)
        for rel in commit.get("touch", []):
            touches += 1
            touch(dest / rel, touches)
        for rel in commit.get("remove", []):
            (dest / rel).unlink()
        git(dest, "add", "-A")
        subject, _, body = commit["message"].partition("\n\n")
        args = ["commit", "-q", "--allow-empty", "-m", subject]
        if body:
            args += ["-m", body]
        git(dest, *args, date=commit["date"], author=commit.get("author"))
        tag = commit.get("tag")
        if tag:
            git(
                dest,
                "tag",
                "-a",
                tag,
                "-m",
                f"Release {tag}",
                date=commit["date"],
                author=commit.get("author"),
            )
    if current != default:
        git(dest, "checkout", "-q", default)
    if remaining:
        raise AssertionError(
            f"fixture {name}: files never added by any commit: {sorted(remaining)}"
        )
    return dest
