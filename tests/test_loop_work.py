from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_loop import NOW, SHA, URL, Provider, artifact, force_phase
from test_loop import loop as _loop_fixture

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError
from hungry_crab.fetch.git import GitRunner
from hungry_crab.loop import Loop
from hungry_crab.loop_work import _molt, _release, check_paths, publish, tag

loop = _loop_fixture


def landed() -> dict[str, Any]:
    return {
        "round": 1,
        "phase": "grow",
        "state": "merged",
        "url": URL,
        "sha": SHA,
        "nutrient_id": "crab:tests:tests.unit",
        "files": [
            {"filename": "redundant.py", "status": "added"},
            {"filename": "app.py", "status": "modified"},
        ],
    }


def molt_receipt() -> dict[str, Any]:
    return {
        "changed_paths": ["redundant.py", "app.py"],
        "checks": {
            "tests_passed": True,
            "lint_before": 0,
            "lint_after": 0,
            "coverage_before": 80.0,
            "coverage_after": 81.0,
            "public_surface_added": False,
        },
        "unreachable": {
            "redundant.py": "Its only caller was removed from app.py; reference scan is empty."
        },
    }


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/a.yml",
        ".GitHub/WORKFLOWS/a.yml",
        "LICENSE",
        "LICENCE.txt",
        ".crab.yml",
        ".git/config",
        ".crab/loop.json",
        "../escape",
        "C:/escape",
        "a\\b",
        "/absolute",
    ],
)
def test_protected_and_ambiguous_paths_are_rejected(loop: Loop, path: str) -> None:
    with pytest.raises(CrabError):
        check_paths(loop, [path])


def test_case_collisions_and_custom_protection_are_rejected(loop: Loop) -> None:
    with pytest.raises(CrabError):
        check_paths(loop, ["src/app.py", "src/APP.py"])
    loop.settings.protected.append("src/licensing/**")
    with pytest.raises(CrabError, match="protected"):
        check_paths(loop, ["src/licensing/rules.py"])


def test_loop_phase_branch_equal_to_default_refuses_before_fetch_or_push(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    class Git:
        def toplevel(self) -> Path:
            return loop.maw

        def run(self, *args: str) -> str:
            calls.append(args)
            return "https://github.com/example/maw.git\n"

    force_phase(loop, "molt", prs=[landed()])
    ready = loop.next()
    monkeypatch.setattr("hungry_crab.loop_work.GitRunner", lambda _: Git())
    with pytest.raises(CrabError, match="default branch"):
        publish(
            loop,
            ready["active"]["token"],
            SHA,
            "refactor: molt",
            "Molt body",
            {},
            lambda *_: loop.branch(loop._load()),
        )
    assert calls == [("remote", "get-url", "--push", "--all", "origin")]


@pytest.mark.parametrize(
    "damage",
    [
        "tests",
        "lint",
        "coverage",
        "public",
        "no-landed",
        "foreign-path",
        "preexisting-delete",
        "no-proof",
        "nan",
    ],
)
def test_molt_invariants_fail_before_publication(loop: Loop, damage: str) -> None:
    data = loop._load()
    data["prs"] = [landed()]
    receipt = molt_receipt()
    changes = [("D", "redundant.py"), ("M", "app.py")]
    if damage == "tests":
        receipt["checks"]["tests_passed"] = False
    elif damage == "lint":
        receipt["checks"]["lint_after"] = 1
    elif damage == "coverage":
        receipt["checks"]["coverage_after"] = 79
    elif damage == "public":
        receipt["checks"]["public_surface_added"] = True
    elif damage == "no-landed":
        data["prs"] = []
    elif damage == "foreign-path":
        changes.append(("M", "old.py"))
    elif damage == "preexisting-delete":
        changes = [("D", "app.py")]
    elif damage == "no-proof":
        receipt["unreachable"] = {}
    else:
        receipt["checks"]["coverage_after"] = float("nan")
    with pytest.raises(CrabError):
        _molt(loop, data, changes, receipt)


def test_failed_molt_is_closed_and_becomes_a_lesson(loop: Loop) -> None:
    assert isinstance(loop.provider, Provider)
    molt_url = URL.replace("/1", "/2")
    loop.provider.rows[molt_url] = artifact(molt_url, ci="failure")
    force_phase(loop, "molt", prs=[{**landed(), "phase": "molt", "state": "open", "url": molt_url}])
    ready = loop.next()
    result = loop.record(ready["active"]["token"], "molt", "fail", note="Coverage fell")
    assert result["phase"] == "harden" and "Coverage fell" in result["notes"]
    assert loop.provider.rows[molt_url]["state"] == "closed"


@pytest.mark.parametrize("version", ["2.0.0", "1.2.4", "1.3.1", "1.3.0.dev0"])
def test_harden_rejects_wrong_bump_or_unstable_version(loop: Loop, version: str) -> None:
    data = loop._load()
    data["prs"] = [landed()]
    receipt = {"previous_version": "1.2.3", "version": version, "contract_changed": False}
    with pytest.raises(CrabError):
        _release(loop, data, GitRunner(loop.maw), SHA, SHA, [("M", "CHANGELOG.md")], receipt)


def test_real_git_molt_harden_round_preserves_dirty_files_and_never_runs_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    GitRunner(remote).run("init", "--bare")
    maw = tmp_path / "maw"
    maw.mkdir()
    git = GitRunner(maw)
    git.run("init", "-b", "master")
    git.run("config", "user.name", "Loop Test")
    git.run("config", "user.email", "loop@example.test")
    git.run("remote", "add", "origin", str(remote))
    (maw / ".crab.yml").write_text(
        "loop:\n  autonomy: work\n  work_authorized: true\n"
        "  prey: [example/prey]\n"
        "  release_files: [CHANGELOG.md, version.txt]\n",
        encoding="utf-8",
    )
    (maw / "app.py").write_text("import redundant\n", encoding="utf-8")
    (maw / "redundant.py").write_text("VALUE = 1\n", encoding="utf-8")
    (maw / "version.txt").write_text("1.2.3\n", encoding="utf-8")
    (maw / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    git.run("add", "--", ".crab.yml", "app.py", "redundant.py", "version.txt", "CHANGELOG.md")
    git.run("commit", "-m", "feat: initial synthetic maw")
    git.run("push", "origin", "HEAD:master")
    base = git.head_sha()
    provider = Provider()
    provider.rows[URL] = artifact(sha=base, merged=True)
    monkeypatch.setattr("hungry_crab.loop.maw_slug", lambda _: Slug("example", "maw"))
    # Real effects use a local bare server; only the URL identity read is substituted.
    original_run = GitRunner.run

    def git_run(self: GitRunner, *args: str, **kwargs: Any) -> str:
        if args == ("remote", "get-url", "--push", "--all", "origin"):
            return "https://github.com/example/maw.git\n"
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(GitRunner, "run", git_run)
    instance = Loop(maw, provider=provider, now=NOW)
    instance.init()
    force_phase(instance, "molt", prs=[{**landed(), "sha": base}])
    hook_marker = tmp_path / "hook-ran"
    hook = maw / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\necho ran > '{hook_marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    (maw / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (maw / "redundant.py").unlink()
    git.run("add", "--", "app.py", "redundant.py")
    git.run("commit", "-m", "refactor: remove round redundancy")
    (maw / "scratch.txt").write_text("unrelated dirty file\n", encoding="utf-8")
    created: list[str] = []

    def gh(*args: str) -> str:
        if args[0] == "api":
            return "master"
        assert args[:2] == ("pr", "create")
        branch = args[args.index("--head") + 1]
        body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
        sha = GitRunner(remote).run("rev-parse", f"refs/heads/{branch}").strip()
        url = URL.rsplit("/", 1)[0] + "/" + str(len(created) + 2)
        created.append(url)
        provider.rows[url] = {
            **artifact(url, sha=sha),
            "body": body,
            "head": {"sha": sha, "ref": branch},
            "base": {"ref": "master"},
        }
        provider.changed[url] = (
            [
                {"filename": path, "status": status}
                for path, status in (("app.py", "modified"), ("redundant.py", "removed"))
            ]
            if len(created) == 1
            else [
                {"filename": "CHANGELOG.md", "status": "modified"},
                {"filename": "version.txt", "status": "modified"},
            ]
        )
        return url

    ready = instance.next()
    result = publish(
        instance,
        ready["active"]["token"],
        git.head_sha(),
        "refactor: molt",
        "Remove the round's redundant helper. Tests and coverage pass.",
        molt_receipt(),
        gh,
    )
    # Reconciliation returns the same artifact and makes no second provider write.
    repeat = publish(
        instance,
        ready["active"]["token"],
        git.head_sha(),
        "refactor: molt",
        "Remove the round's redundant helper. Tests and coverage pass.",
        molt_receipt(),
        gh,
    )
    assert repeat["url"] == result["url"] and len(created) == 1
    assert not hook_marker.exists()
    instance.record(ready["active"]["token"], "molt", "ok")
    assert not instance.next()["ready"]
    # Simulate the human merge on the synthetic server, outside the loop.
    merged_sha = result["sha"]
    GitRunner(remote).run("update-ref", "refs/heads/master", merged_sha)
    provider.rows[result["url"]].update(
        merged_at="today", merge_commit_sha=merged_sha, state="closed"
    )
    git.run("fetch", "origin", "master")
    git.run("reset", "--hard", "FETCH_HEAD")
    (maw / "version.txt").write_text("1.3.0\n", encoding="utf-8")
    (maw / "CHANGELOG.md").write_text(
        f"# Changelog\n\n1.3.0\ncrab:tests:tests.unit {URL}\nMOLT {result['url']}\n",
        encoding="utf-8",
    )
    git.run("add", "--", "CHANGELOG.md", "version.txt")
    git.run("commit", "-m", "chore: harden 1.3.0")
    ready = instance.next()
    receipt = {
        "changed_paths": ["CHANGELOG.md", "version.txt"],
        "previous_version": "1.2.3",
        "version": "1.3.0",
        "contract_changed": False,
    }
    release = publish(
        instance,
        ready["active"]["token"],
        git.head_sha(),
        "chore: release 1.3.0",
        "Trace the landed nutrient and MOLT in the changelog.",
        receipt,
        gh,
    )
    assert not instance.next()["ready"]
    assert not GitRunner(remote).try_run("rev-parse", "--verify", "refs/tags/v1.3.0")
    GitRunner(remote).run("update-ref", "refs/heads/master", release["sha"])
    provider.rows[release["url"]].update(
        merged_at="today", merge_commit_sha=release["sha"], state="closed"
    )
    ready = instance.next()
    hardened = tag(instance, ready["active"]["token"])
    assert hardened["tagged"] == "v1.3.0"
    assert tag(instance, ready["active"]["token"])["tagged"] == "v1.3.0"
    result = instance.record(ready["active"]["token"], "harden", "ok")
    assert result["phase"] == "hunt" and result["round"] == 2
    assert GitRunner(remote).run("rev-parse", "refs/tags/v1.3.0").strip() == release["sha"]
    assert (maw / "scratch.txt").read_text(encoding="utf-8") == "unrelated dirty file\n"
    assert not hook_marker.exists()
    assert "scratch.txt" not in GitRunner(remote).run(
        "ls-tree", "-r", "--name-only", release["sha"]
    )
