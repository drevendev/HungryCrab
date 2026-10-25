from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_loop import APP_ID, LINT, NOW, SHA, TESTS, URL, Provider, artifact, force_phase
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
        "third_party/fast/LICENSE",
        "third_party/fast/COPYING.txt",
        "third_party/fast/NOTICE",
        ".crab/attributions.json",
        "THIRD_PARTY_NOTICES.md",
        "NOTICE",
        "src/file.py.",
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


def test_only_verified_copy_grow_may_write_generated_attributions(loop: Loop) -> None:
    loop.config.attribution_file = "legal/notices.md"
    generated = [".crab/attributions.json", "legal/notices.md"]
    with pytest.raises(CrabError, match="protected"):
        check_paths(loop, generated)
    check_paths(loop, generated, allow_attributions=True)
    with pytest.raises(CrabError, match="protected"):
        check_paths(loop, ["third_party/fast/LICENSE"], allow_attributions=True)


def test_verified_copy_preparation_keeps_its_generated_notice_and_records(loop: Loop) -> None:
    from test_attribution import WORKFLOW, _maw, _payload, _prepare

    _maw(loop.maw, {"templates/ci.yml": WORKFLOW})
    prepared = _prepare(
        loop.maw,
        _payload(
            taken=[
                {
                    "prey_path": ".github/workflows/test.yml",
                    "maw_path": "templates/ci.yml",
                    "verbatim": False,
                }
            ]
        ),
    )
    paths = [file.path for file in prepared.files]
    assert ".crab/attributions.json" in paths and "THIRD_PARTY_NOTICES.md" in paths
    check_paths(loop, paths, allow_attributions=True)
    with pytest.raises(CrabError, match="protected"):
        check_paths(loop, paths)


@pytest.mark.parametrize(
    "path", ["third_party/fast/LICENSE", "THIRD_PARTY_NOTICES.md", ".crab/attributions.json"]
)
def test_molt_cannot_delete_compliance_even_when_introduced_by_this_round(
    loop: Loop, path: str
) -> None:
    data = loop._load()
    data["prs"] = [{**landed(), "files": [{"filename": path, "status": "added"}]}]
    receipt = molt_receipt()
    receipt["unreachable"][path] = "Nothing imports this text file"
    with pytest.raises(CrabError, match="protected"):
        _molt(loop, data, [("D", path)], receipt)


@pytest.mark.parametrize(
    "damage", [None, "url", "nutrient", "changelog-version", "old-version", "new-version"]
)
def test_harden_traces_exact_urls_ids_and_versions(loop: Loop, damage: str | None) -> None:
    loop.settings.release_files.append("version.txt")
    data = loop._load()
    data["prs"] = [landed()]
    values = {
        ("head", "CHANGELOG.md"): f"## [1.3.0]\n- ({URL}) `crab:tests:tests.unit`\n",
        ("base", "version.txt"): 'version = "1.2.3"',
        ("head", "version.txt"): 'version = "1.3.0"',
    }
    if damage == "url":
        values["head", "CHANGELOG.md"] = values["head", "CHANGELOG.md"].replace(URL, URL + "4")
    elif damage == "nutrient":
        values["head", "CHANGELOG.md"] = values["head", "CHANGELOG.md"].replace(
            "tests.unit", "tests.unit-extra"
        )
    elif damage == "changelog-version":
        values["head", "CHANGELOG.md"] = values["head", "CHANGELOG.md"].replace("1.3.0", "11.3.0")
    elif damage == "old-version":
        values["base", "version.txt"] = 'version = "11.2.3"'
    elif damage == "new-version":
        values["head", "version.txt"] = 'version = "11.3.0"'

    class Git:
        def run(self, *args: str) -> str:
            if args[0] == "ls-tree":
                return "100644 blob " + "b" * 40 + "\t" + args[-1]
            commit, path = args[-1].split(":", 1)
            return values[commit, path]

    receipt = {"previous_version": "1.2.3", "version": "1.3.0", "contract_changed": False}
    if damage is None:
        _release(
            loop,
            data,
            Git(),
            "base",
            "head",
            [("M", "CHANGELOG.md"), ("M", "version.txt")],
            receipt,
        )
    else:
        with pytest.raises(CrabError, match="HARDEN"):
            _release(
                loop,
                data,
                Git(),
                "base",
                "head",
                [("M", "CHANGELOG.md"), ("M", "version.txt")],
                receipt,
            )


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


@pytest.mark.parametrize("merge_style", ["fast-forward", "squash"])
def test_real_git_molt_harden_round_preserves_dirty_files_and_never_runs_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, merge_style: str
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
        "  required_checks: [{name: tests, app_id: 15368}]\n"
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
            "head": {"sha": sha, "ref": branch, "repo": {"full_name": "example/maw"}},
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
        merged_at="today",
        merged_by=artifact(merged=True)["merged_by"],
        merge_commit_sha=merged_sha,
        state="closed",
        merge_ci=artifact(sha=merged_sha, merged=True)["merge_ci"],
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
    release_merge = release["sha"]
    if merge_style == "squash":
        release_merge = git.run(
            "commit-tree",
            f"{release['sha']}^{{tree}}",
            "-p",
            merged_sha,
            "-m",
            "chore: simulate human squash merge",
        ).strip()
        git.run("-c", "core.hooksPath=", "push", "origin", f"{release_merge}:refs/heads/master")
    else:
        GitRunner(remote).run("update-ref", "refs/heads/master", release_merge)
    provider.rows[release["url"]].update(
        merged_at="today",
        merged_by=artifact(merged=True)["merged_by"],
        merge_commit_sha=release_merge,
        state="closed",
        merge_ci=artifact(sha=release_merge, merged=True)["merge_ci"],
    )
    ready = instance.next()
    hardened = tag(instance, ready["active"]["token"])
    assert hardened["tagged"] == "v1.3.0"
    assert tag(instance, ready["active"]["token"])["tagged"] == "v1.3.0"
    result = instance.record(ready["active"]["token"], "harden", "ok")
    assert result["phase"] == "hunt" and result["round"] == 2
    assert GitRunner(remote).run("rev-parse", "refs/tags/v1.3.0").strip() == release_merge
    assert (maw / "scratch.txt").read_text(encoding="utf-8") == "unrelated dirty file\n"
    assert not hook_marker.exists()
    assert "scratch.txt" not in GitRunner(remote).run(
        "ls-tree", "-r", "--name-only", release["sha"]
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "empty",
        "failed",
        "pending",
        "wrong-sha",
        "stale-head",
        "skipped",
        "neutral",
        "unrelated",
        "wrong-check-sha",
        "no-config",
        "missing-required",
        "missing-app",
        "wrong-app",
        "impostor-only",
        "impostor-skipped",
        "impostor-failed",
    ],
)
def test_merge_ci_blocks_waiting_and_tagging_without_effects(
    loop: Loop, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    assert isinstance(loop.provider, Provider)
    truth = artifact(merged=True)
    if damage == "missing":
        truth.pop("merge_ci")
    elif damage == "empty":
        truth["merge_ci"]["checks"] = []
    elif damage == "failed":
        truth["merge_ci"]["checks"][0]["conclusion"] = "failure"
    elif damage == "pending":
        truth["merge_ci"]["checks"][0]["status"] = "in_progress"
    elif damage == "wrong-sha":
        truth["merge_ci"]["sha"] = "b" * 40
    elif damage in {"skipped", "neutral"}:
        truth["merge_ci"]["checks"][0]["conclusion"] = damage
    elif damage == "unrelated":
        truth["merge_ci"]["checks"][0]["name"] = "unrelated"
    elif damage == "wrong-check-sha":
        truth["merge_ci"]["checks"][0]["head_sha"] = "b" * 40
    elif damage == "no-config":
        loop.settings.required_checks = []
    elif damage == "missing-required":
        loop.settings.required_checks = [TESTS, LINT]
    elif damage == "missing-app":
        truth["merge_ci"]["checks"][0].pop("app")
    elif damage == "wrong-app":
        truth["merge_ci"]["checks"][0]["app"] = {"id": APP_ID + 1}
    elif damage.startswith("impostor-"):
        expected = truth["merge_ci"]["checks"][0]
        if damage == "impostor-only":
            truth["merge_ci"]["checks"] = []
        else:
            expected["conclusion"] = "skipped" if damage == "impostor-skipped" else "failure"
        truth["merge_ci"]["checks"].append(
            {**expected, "id": 99, "app": {"id": APP_ID + 1}, "conclusion": "success"}
        )
    else:
        truth["head"]["sha"] = "b" * 40
    loop.provider.rows[URL] = truth
    force_phase(loop, "harden", prs=[{**landed(), "phase": "harden", "state": "open"}])
    data = loop._load()
    data["waiting_on"] = {"kind": "merge", "reason": "waiting for human merge"}
    loop._save(data)
    before = loop._load()["phases_today"]
    assert not loop.next()["ready"]
    assert loop._load()["phases_today"] == before
    assert loop._load()["prs"][0]["state"] == "open"
    force_phase(loop, "harden", prs=[{**landed(), "phase": "harden", "state": "open"}])
    ready = loop.next()

    def no_git(*_: object, **__: object) -> None:
        pytest.fail("tagging touched Git before verifying the merge commit CI")

    monkeypatch.setattr("hungry_crab.loop_work.GitRunner", no_git)
    before_tag = loop.path.read_bytes()
    with pytest.raises(CrabError, match=r"passing CI|reviewed head|required_checks"):
        tag(loop, ready["active"]["token"])
    assert loop.path.read_bytes() == before_tag


@pytest.mark.parametrize(
    "merger",
    [None, {"login": "bot", "type": "Bot"}, {"login": "LOOP-PUBLISHER", "type": "User"}],
)
def test_tag_refuses_an_unverified_human_merge_before_git_or_state_effects(
    loop: Loop, monkeypatch: pytest.MonkeyPatch, merger: object
) -> None:
    assert isinstance(loop.provider, Provider)
    truth = artifact(merged=True)
    truth["merged_by"] = merger
    loop.provider.rows[URL] = truth
    force_phase(loop, "harden", prs=[{**landed(), "phase": "harden", "state": "open"}])
    ready = loop.next()

    def no_git(*_: object, **__: object) -> None:
        pytest.fail("tagging touched Git before proving a human merge")

    monkeypatch.setattr("hungry_crab.loop_work.GitRunner", no_git)
    before = loop.path.read_bytes()
    with pytest.raises(CrabError, match="human merge"):
        tag(loop, ready["active"]["token"])
    assert loop.path.read_bytes() == before
