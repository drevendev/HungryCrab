from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hungry_crab.cache import Slug
from hungry_crab.errors import CrabError, ExternalCommandError
from hungry_crab.fetch.git import GitRunner
from hungry_crab.pr_effects import publish_git_pull_request
from hungry_crab.pr_publication import GeneratedFile, PreparedPullRequest

MAW_SLUG = Slug("example", "maw")
BRANCH = "crab/ci-cache-deadbeef"


class FakeGh:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.created_bodies: list[str] = []
        self.fail_create = False

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:2] == ("api", "repos/example/maw"):
            return "master\n"
        if args[:2] == ("pr", "create"):
            body_index = args.index("--body-file") + 1
            self.created_bodies.append(Path(args[body_index]).read_text(encoding="utf-8"))
            if self.fail_create:
                raise ExternalCommandError("simulated gh pr create failure")
            return "https://github.com/example/maw/pull/9\n"
        raise AssertionError(f"unexpected gh call: {args!r}")


def _prepared(path: str = "generated/cache.yml") -> PreparedPullRequest:
    return PreparedPullRequest(
        title="Cache dependencies in CI",
        body="<!-- crab:ci:cache -->\nTrace: generated safely.\n",
        files=(GeneratedFile(path, "cache: true\n"),),
    )


def _git_repo(tmp_path: Path) -> tuple[Path, Path, GitRunner, GitRunner]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    remote_git = GitRunner(remote)
    remote_git.run("init", "--bare", "--initial-branch=master")

    maw = tmp_path / "maw"
    maw.mkdir()
    git = GitRunner(maw)
    git.run("init", "--initial-branch=master")
    git.run("config", "user.name", "Hungry Crab Test")
    git.run("config", "user.email", "crab@example.invalid")
    (maw / "README.md").write_text("# Maw\n", encoding="utf-8")
    git.run("add", "README.md")
    git.run("commit", "-m", "chore: seed maw")
    git.run("remote", "add", "origin", str(remote))
    git.run("push", "-u", "origin", "master")
    return maw, remote, git, remote_git


def test_publisher_uses_isolated_worktree_and_exact_prepared_files(tmp_path: Path) -> None:
    maw, _, git, remote_git = _git_repo(tmp_path)
    gh = FakeGh()
    dirty = maw / "mine.txt"
    dirty.write_text("maintainer work\n", encoding="utf-8")

    url = publish_git_pull_request(
        MAW_SLUG,
        maw,
        BRANCH,
        _prepared(),
        run_gh=gh,
        git=git,
    )

    assert url == "https://github.com/example/maw/pull/9"
    assert git.current_branch() == "master"
    assert dirty.read_text(encoding="utf-8") == "maintainer work\n"
    assert not (maw / "generated" / "cache.yml").exists()
    assert remote_git.run("show", f"refs/heads/{BRANCH}:generated/cache.yml") == "cache: true\n"
    assert gh.created_bodies == ["<!-- crab:ci:cache -->\nTrace: generated safely.\n"]
    assert any(call[:2] == ("pr", "create") for call in gh.calls)


def test_publisher_bypasses_clean_filters_and_commits_exact_bytes(tmp_path: Path) -> None:
    maw, _, git, remote_git = _git_repo(tmp_path)
    gh = FakeGh()
    marker = tmp_path / "filter-ran.txt"
    script = tmp_path / "clean-filter.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('ran\\n', encoding='utf-8')\n"
        "sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(b'cache: rewritten\\n')\n",
        encoding="utf-8",
    )
    executable = Path(sys.executable).as_posix()
    command = f'"{executable}" "{script.as_posix()}" "{marker.as_posix()}"'
    git.run("config", "filter.crab-rewrite.clean", command)
    git.run("config", "filter.crab-rewrite.required", "true")
    (maw / ".gitattributes").write_text(
        "generated/cache.yml filter=crab-rewrite\n",
        encoding="utf-8",
    )
    git.run("add", ".gitattributes")
    git.run("commit", "-m", "test: configure rewriting clean filter")
    git.run("push", "origin", "master")

    url = publish_git_pull_request(
        MAW_SLUG,
        maw,
        BRANCH,
        _prepared(),
        run_gh=gh,
        git=git,
    )

    assert url == "https://github.com/example/maw/pull/9"
    assert not marker.exists(), "publication must not execute the configured clean filter"
    assert remote_git.run("show", f"refs/heads/{BRANCH}:generated/cache.yml") == "cache: true\n"


def test_retry_reuses_branch_after_push_succeeds_but_pr_create_fails(tmp_path: Path) -> None:
    maw, _, git, remote_git = _git_repo(tmp_path)
    gh = FakeGh()
    gh.fail_create = True

    with pytest.raises(ExternalCommandError, match="simulated gh pr create failure"):
        publish_git_pull_request(
            MAW_SLUG,
            maw,
            BRANCH,
            _prepared(),
            run_gh=gh,
            git=git,
        )

    first_head = remote_git.run("rev-parse", f"refs/heads/{BRANCH}").strip()
    gh.fail_create = False
    url = publish_git_pull_request(
        MAW_SLUG,
        maw,
        BRANCH,
        _prepared(),
        run_gh=gh,
        git=git,
    )
    second_head = remote_git.run("rev-parse", f"refs/heads/{BRANCH}").strip()

    assert url == "https://github.com/example/maw/pull/9"
    assert second_head == first_head, "retry must reuse the already-pushed exact branch"
    assert remote_git.run("rev-list", "--count", f"master..{BRANCH}").strip() == "1"


def test_retry_refuses_stale_extra_paths_on_existing_nutrient_branch(tmp_path: Path) -> None:
    maw, _, git, _ = _git_repo(tmp_path)
    gh = FakeGh()
    gh.fail_create = True
    with pytest.raises(ExternalCommandError):
        publish_git_pull_request(
            MAW_SLUG,
            maw,
            BRANCH,
            _prepared(),
            run_gh=gh,
            git=git,
        )

    gh.fail_create = False
    with pytest.raises(CrabError, match="outside the prepared payload") as raised:
        publish_git_pull_request(
            MAW_SLUG,
            maw,
            BRANCH,
            _prepared("generated/other.yml"),
            run_gh=gh,
            git=git,
        )

    assert raised.value.hint == "unexpected paths: generated/cache.yml"
    assert len([call for call in gh.calls if call[:2] == ("pr", "create")]) == 1


@pytest.mark.parametrize(
    "path",
    ["../escape.txt", "/tmp/escape.txt", "generated/../escape.txt", ".git/config"],
)
def test_unsafe_prepared_path_fails_before_provider_read(tmp_path: Path, path: str) -> None:
    maw, _, git, _ = _git_repo(tmp_path)
    gh = FakeGh()

    with pytest.raises(CrabError, match="unsafe maw path"):
        publish_git_pull_request(
            MAW_SLUG,
            maw,
            BRANCH,
            _prepared(path),
            run_gh=gh,
            git=git,
        )

    assert gh.calls == []