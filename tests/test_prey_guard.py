"""Fail-closed prey-cache shell guard (HungryCrab#85)."""

from __future__ import annotations

import io
import json
import sys
import tomllib
from pathlib import Path

import pytest

from hungry_crab.prey_guard import event_guard_reason, guard_reason, main

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/tmp/crab-prey")
PREY = "/tmp/crab-prey/github/acme/widget/repo"


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone root as `crab catch` lays it out, with the `.git` directory it creates."""
    repo = tmp_path / "crab-prey" / "github" / "acme" / "widget" / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "README.md").write_text("prey\n", encoding="utf-8")
    return repo.resolve()


@pytest.mark.parametrize(
    "command",
    [
        f"{PREY}/bin/tool --version",
        f"{PREY}/bin/cat {PREY}/README.md",
        f'"$CRAB_CACHE_DIR/github/acme/widget/repo/bin/cat" {PREY}/README.md',
        f"PATH={PREY}/bin:$PATH cat {PREY}/README.md",
        f"LD_PRELOAD={PREY}/evil.so cat {PREY}/README.md",
        f"LC_ALL=C cat {PREY}/README.md",
        f"cd {PREY} && ./install.sh",
        f"python {PREY}/setup.py",
        'python "$CRAB_CACHE_DIR/github/acme/widget/repo/setup.py"',
        "python ${CRAB_CACHE_DIR}/github/acme/widget/repo/setup.py",
        r"python %CRAB_CACHE_DIR%\github\acme\widget\repo\setup.py",
        r"python $env:CRAB_CACHE_DIR\github\acme\widget\repo\setup.py",
        'echo "$(python ~/.cache/hungry-crab/github/acme/widget/repo/setup.py)"',
        "cat <(python ~/.cache/hungry-crab/github/acme/widget/repo/setup.py)",
        "printf '%s\\n' `~/.cache/hungry-crab/github/acme/widget/repo/bin/tool`",
        (
            "find ~/.cache/hungry-crab/github/acme/widget/repo -type f -exec "
            "~/.cache/hungry-crab/github/acme/widget/repo/bin/tool {} \\;"
        ),
        f"git -C {PREY} difftool --extcmd={PREY}/bin/tool HEAD~ HEAD",
        f"git -C {PREY} mergetool",
        f"git -c core.pager={PREY}/bin/tool -C {PREY} log",
        f"git -C {PREY} log --ext-diff",
        f"rg --pre={PREY}/bin/tool needle {PREY}",
        f"rg --pre {PREY}/bin/tool needle {PREY}",
        f"rg --hostname-bin={PREY}/bin/tool --hyperlink-format=default needle {PREY}",
        f"rg --hostname-bin {PREY}/bin/tool needle {PREY}",
        f"rg -z needle {PREY}",
        f"rg -inz needle {PREY}",
        f"rg --search-zip needle {PREY}",
    ],
)
def test_cache_touching_execution_or_unknown_shapes_fail_closed(command: str) -> None:
    reason = guard_reason(command, root=CACHE)
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize(
    "command",
    [
        f"cat {PREY}/README.md",
        f"head -50 {PREY}/pyproject.toml",
        f"tail -20 {PREY}/README.md",
        f"wc -l {PREY}/README.md",
        f"rg -n TODO {PREY}",
        f"rg -in --no-search-zip --pre-glob=*.txt TODO {PREY}",
        f"grep -R TODO {PREY}",
        f"ls -la {PREY}",
        f"stat {PREY}/README.md",
    ],
)
def test_small_audited_read_only_shapes_remain_allowed(command: str) -> None:
    assert guard_reason(command, root=CACHE) is None


@pytest.mark.parametrize(
    "command",
    [
        "git -C {P} log --oneline -5",
        "git --no-pager -C {P} rev-parse HEAD",
        "git -C {P} for-each-ref --format=%(refname)",
        "git -C {P} status --short",
    ],
)
def test_read_only_git_against_a_clone_root_remains_allowed(command: str, clone: Path) -> None:
    assert guard_reason(command.format(P=clone.as_posix()), root=clone.parents[3]) is None


def test_bare_reader_remains_allowed_when_cwd_is_inside_cache() -> None:
    assert guard_reason("cat README.md", root=CACHE, cwd=Path(PREY)) is None


def test_guard_has_no_opinion_without_a_cache_reference() -> None:
    assert guard_reason("python -m pytest", root=CACHE) is None
    assert guard_reason("git difftool --extcmd=./tool HEAD~ HEAD", root=CACHE) is None


def test_malformed_cache_touching_shell_fails_closed() -> None:
    command = "cat '~/.cache/hungry-crab/github/acme/widget/repo/README.md"
    assert guard_reason(command, root=CACHE) is not None


def test_hook_denies_a_valid_bash_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_CACHE_DIR", str(CACHE))
    event = {"tool_name": "Bash", "tool_input": {"command": f"python {PREY}/setup.py"}}
    assert event_guard_reason(event) is not None
    assert main(stdin=io.StringIO(json.dumps(event))) == 2


def test_hook_allows_a_read_only_bash_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_CACHE_DIR", str(CACHE))
    event = {"tool_name": "Bash", "tool_input": {"command": f"cat {PREY}/README.md"}}
    assert event_guard_reason(event) is None
    assert main(stdin=io.StringIO(json.dumps(event))) == 0


def test_hook_denies_bash_input_it_cannot_inspect() -> None:
    assert event_guard_reason({"tool_name": "Bash", "tool_input": None}) is not None
    assert event_guard_reason({"tool_name": "Bash", "tool_input": {}}) is not None


def test_non_bash_event_is_outside_this_guard() -> None:
    event = {"tool_name": "Read", "tool_input": {"command": f"python {PREY}/setup.py"}}
    assert event_guard_reason(event, root=CACHE) is None


def test_hook_manifest_runs_the_packaged_guard_through_the_launcher() -> None:
    """The Bash hook runs this module, from the plugin's own ``src/``, not a console script.

    ``hooks/guard.py`` maps ``prey`` to the same module the ``crab-prey-guard`` console script
    names, so the hook and the manual command are one guard; the launcher's own behaviour is
    covered in ``tests/test_hooks.py``.
    """
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entry = next(item for item in hooks["hooks"]["PreToolUse"] if item["matcher"] == "Bash")
    handler = entry["hooks"][0]
    assert '"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"' in handler["command"]
    assert '"$g" prey;' in handler["command"]
    assert handler["timeout"] >= 5
    launcher = (ROOT / "hooks" / "guard.py").read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert '"prey": "hungry_crab.prey_guard"' in launcher
    assert scripts["crab-prey-guard"] == "hungry_crab.prey_guard:main"


@pytest.mark.parametrize(
    "command",
    [
        f"cat {PREY}/README.md\npython {PREY}/setup.py",
        f"cat {PREY}/README.md\r\npython {PREY}/setup.py",
        f"git -C {PREY} log -1\nmake -C {PREY}",
        f"cat {PREY}/README.md\n{PREY}/bin/tool",
        f"cat {PREY}/README.md\n",
    ],
)
def test_a_line_break_is_a_command_separator(command: str) -> None:
    """`shlex` reads a newline as whitespace; the shell reads it as `;`."""
    reason = guard_reason(command, root=CACHE)
    assert reason is not None
    assert "multi-line" in reason


@pytest.mark.parametrize(
    "command",
    [
        "git -C {P} log -1 --format=%B --output=/tmp/x.sh",
        "git -C {P} log -1 --format=%B --output /tmp/x.sh",
        "git -C {P} show --output=/tmp/x.sh HEAD",
        "git -C {P} grep -O needle",
        "git -C {P} grep -Ovim needle",
        "git -C {P} grep --open-files-in-pager=vim needle",
        "git -C {P} show --ext-diff HEAD",
        "git -C {P} diff --textconv HEAD~ HEAD",
        "git -C {P} cat-file --filters HEAD:README.md",
        "git -C {P} cat-file --batch --filters",
    ],
)
def test_git_options_that_write_or_run_something_fail_closed(command: str, clone: Path) -> None:
    """Refused against a real clone root, so it is the argument filter that refuses."""
    assert guard_reason(command.format(P=clone.as_posix()), root=clone.parents[3]) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git -C {P} show --stat HEAD",
        "git -C {P} show HEAD:README.md",
        "git -C {P} blame README.md",
        "git -C {P} diff HEAD~ HEAD -- src/",
        "git -C {P} shortlog -sn",
        "git -C {P} describe --tags",
        "git -C {P} show-ref --heads",
        "git -C {P} grep -n TODO",
    ],
)
def test_the_historians_read_only_git_verbs_are_allowed(command: str, clone: Path) -> None:
    """`agents/crab-historian.md` tells the agent to run these in the clone."""
    assert guard_reason(command.format(P=clone.as_posix()), root=clone.parents[3]) is None


@pytest.mark.parametrize(
    "command",
    [
        "python /tmp/crab-prey-other/setup.py",
        "python /tmp/crab-prey.old/setup.py",
        "python /tmp/crab-prey_backup/setup.py",
        "python ~/.cache/hungry-crab-other/setup.py",
    ],
)
def test_a_sibling_that_starts_with_the_cache_name_is_not_the_cache(command: str) -> None:
    assert guard_reason(command, root=CACHE) is None


@pytest.mark.parametrize(
    "command",
    [
        "python /tmp/crab-prey/setup.py",
        'python "/tmp/crab-prey"/setup.py',
        "ls /tmp/crab-prey",
        'ls "/tmp/crab-prey"',
    ],
)
def test_the_cache_root_itself_is_still_recognised(command: str) -> None:
    expected_allowed = command.startswith("ls")
    assert (guard_reason(command, root=CACHE) is None) is expected_allowed


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory under tmp_path, with a maw in it; the default cache root is its own."""
    home = (tmp_path / "home").resolve()
    (home / "work" / "maw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in ("CRAB_CACHE_DIR", "FOO", "X", "TMPDIR"):
        monkeypatch.delenv(name, raising=False)
    return home


TAIL = "github/acme/widget/repo/setup.py"

# Commands whose words the shell assembles into a path in the cache (HungryCrab#142).
ASSEMBLED = [
    f"d=hungry-crab; python ~/.cache/$d/{TAIL}",
    f"p=$HOME/.cache; python $p/hungry-crab/{TAIL}",
    f"python ~/.cache/hungry-{{crab,}}/{TAIL}",
    f"python ~/.cache/hungry-cra?/{TAIL}",
    f"python ~/.cache/hungry-*/{TAIL}",
    f"python ~/.cache/[h]ungry-crab/{TAIL}",
    f"python $(echo ~/.cache)/hungry-crab/{TAIL}",
    f"python `echo ~/.cache`/hungry-crab/{TAIL}",
    f"cd ~/.cache && python hungry-crab/{TAIL}",
    "cd ~/.cache && cd hungry-crab/github/acme/widget/repo && python setup.py",
    f"(cd ~/.cache; python hungry-crab/{TAIL})",
    f"x=~/.cache; python $x/*/{TAIL}",
    f"for d in ~/.cache; do python $d/*/{TAIL}; done",
    f"python ${{X:-~/.cache}}/*/{TAIL}",
    f"python ~/.c*/hungry-crab/{TAIL}",
    f"python ~/.*/hungry-crab/{TAIL}",
    "python ~/.cache/**/setup.py",
    f"python ~/.cache/*/../hungry-crab/{TAIL}",
    f"python $FOO/hungry-crab/{TAIL}",
    f"python ${{FOO}}-crab/{TAIL}",
    "find ~/.cache -name setup.py -exec python {} ;",
    f"ls -d ~/.cache/* | xargs -I{{}} python {{}}/{TAIL}",
    f"bash -c 'python $0/*/{TAIL}' ~/.cache",
    f"~/.cache/hungry-*/{TAIL.replace('setup.py', 'bin/cat')} README.md",
    f"python ~/.cache/hungry-*/{TAIL} $'\\''",
    f'python ~/.cache/hungry-*/{TAIL} "',
]

# Ordinary commands that never approach the cache: the guard runs on every Bash call, so a
# refusal here is a real cost.
ORDINARY = [
    "echo $HOME",
    "echo $PATH $1 $@ $? $$",
    "echo $FOO",
    "python $PWD/x.py",
    "ls ~/.cache",
    "ls ~",
    "cd ~ && ls",
    "git status",
    'git commit -m "fix: $msg"',
    "git log --oneline HEAD~3..HEAD",
    "git -C ../other log",
    "npm run build",
    "uv run pytest -q",
    "uv tool install hungry-crab",
    "uv run --project ~/.claude/plugins/cache/hungry-crab/crab/0.6.1-dev.0 crab menu",
    "find . -name '*.py'",
    "find . -name '*.py' -exec grep -l foo {} +",
    "rg 'foo.*bar' src",
    "ls *.md",
    "rm -rf build/*",
    "cp ~/*.txt /tmp",
    'python -c "print(2*3)"',
    "for f in *.py; do echo $f; done",
    "cd $(git rev-parse --show-toplevel) && make",
    'cd "$TMPDIR" && make',
    "export PATH=$HOME/bin:$PATH",
    "du -sh ~/.cache/pip",
    "rm -rf ~/.cache/pip/*",
    "python ~/.cache/pip/*.py",
    "ls ~/.cache/hungry-crab-other/*",
    "cat ~/.bashrc",
    "echo {a,b} && mkdir -p src/{a,b}",
    "echo $((1 + 2))",
    "printf '%s\\n' \"$x\"",
    "ls -la | grep foo",
    "xargs -I{} echo {}",
]


@pytest.mark.parametrize("command", ASSEMBLED)
def test_a_cache_path_the_shell_assembles_is_judged(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize("command", ORDINARY)
def test_ordinary_commands_stay_allowed(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None


@pytest.mark.parametrize(
    "command", ["ls *", "echo $FOO", "grep -r foo *", "python script.py", "git status"]
)
def test_ordinary_commands_stay_allowed_from_the_home_directory(command: str, home: Path) -> None:
    """A glob does not match the leading dot of `.cache`, so `*` in `~` cannot reach it."""
    assert guard_reason(command, root=home / ".cache" / "hungry-crab", cwd=home) is None


@pytest.mark.parametrize(
    "command",
    [
        f"cat ~/.cache/hungry-*/{TAIL.replace('setup.py', 'README.md')}",
        "ls ~/.cache/*",
        f"rg -n TODO ~/.cache/hungry-crab/{TAIL.removesuffix('/setup.py')}",
    ],
)
def test_readers_over_an_assembled_cache_path_stay_allowed(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None


def test_a_custom_root_spelled_through_the_environment_is_judged(home: Path) -> None:
    root = home / "prey"
    commands = [
        f"python $HOME/prey/{TAIL}",
        f'python "${{HOME}}/prey/{TAIL}"',
        f'python "%USERPROFILE%\\prey\\{TAIL}"',
        f'python "$env:USERPROFILE\\prey\\{TAIL}"',
    ]
    for command in commands:
        assert guard_reason(command, root=root, cwd=home / "work" / "maw") is not None, command
    assert guard_reason("cd ~ && make", root=root, cwd=home / "work" / "maw") is None


@pytest.mark.parametrize(
    ("command", "from_clone"),
    [
        ("git -C ~/.cache/hungry-crab/github/acme/widget/repo log --oneline -- src/core.py", False),
        ("git -C ~/.cache/hungry-crab/github/acme/widget/repo show --stat abc1234", False),
        ("git --no-pager -C ~/.cache/hungry-crab/github/acme/widget/repo blame README.md", False),
        ("git -C ~/.cache/hungry-crab/github/acme/widget/repo diff abc1234 def5678", False),
        ("git log --oneline -- src/core.py", True),
        ("git show --stat abc1234", True),
        ("git blame -- README.md", True),
        ("git diff abc1234 def5678 -- src/", True),
    ],
)
def test_the_historians_documented_commands_run_in_the_cached_clone(
    command: str, from_clone: bool, home: Path
) -> None:
    """`agents/crab-historian.md`: `git log`, `git show --stat`, `git blame`, `git diff`."""
    root = home / ".cache" / "hungry-crab"
    clone = root / "github" / "acme" / "widget" / "repo"
    (clone / ".git").mkdir(parents=True)
    cwd = clone if from_clone else home / "work" / "maw"
    assert guard_reason(command, root=root, cwd=cwd) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash drive paths exist on Windows only")
def test_a_git_bash_drive_path_is_the_windows_path(home: Path) -> None:
    root = home / "prey"
    msys = f"/{home.drive[0].lower()}{home.as_posix()[2:]}/prey/{TAIL}"
    assert guard_reason(f"python {msys}", root=root, cwd=home / "work" / "maw") is not None
    assert guard_reason(f"cat {msys}", root=root, cwd=home / "work" / "maw") is None


@pytest.mark.parametrize(
    "command",
    [f"cat {PREY}/README.md |& sh", f"cat {PREY}/README.md >| /tmp/x.sh", f"cat {PREY}/x &>> y"],
)
def test_every_operator_run_is_shell_composition(command: str) -> None:
    """`shlex` keeps `|&`, `>|` and `&>>` together; each is composition, not an argument."""
    reason = guard_reason(command, root=CACHE)
    assert reason is not None
    assert "composition" in reason


def _ship_git_dir(directory: Path, *, worktree: str | None = None) -> None:
    """What a prey can commit: an ordinary directory git accepts as a repository."""
    for sub in ("objects/97", "refs/heads"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    (directory / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (directory / "refs" / "heads" / ".keep").write_text("", encoding="utf-8")
    (directory / "index").write_bytes(b"DIRC")
    config = "[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
    if worktree is not None:
        config += f"\tworktree = {worktree}\n"
    config += "\tfsmonitor = echo ran > fsmonitor-ran.txt; false\n"
    (directory / "config").write_text(config, encoding="utf-8")


@pytest.mark.parametrize(
    "command",
    [
        # The shapes of the reproduction: a git directory the prey ships, opened directly.
        "git --git-dir={P}/payload.git --work-tree={P} status",
        "git -C {P} --git-dir=payload.git --work-tree=. status",
        "git -C {P} --git-dir payload.git --work-tree . diff",
        "git -C {P}/tools.git status --short",
        "git -C {P}/tools.git log -p",
        "git -C {P} -C tools.git status",
        "git -C {P}/tools.git/refs log",
        # Any repository but the clone root itself, and any option that names one.
        "git -C {P}/src status",
        "git --git-dir={P}/.git log",
        "git -C {P} --work-tree={P}/src status",
        "git -C {P} --namespace=x log",
        "git -C {P} --bare log",
        "git -C{P} log",
        "git -C {P}/.. log",
        "git log -- {P}/README.md",
    ],
)
def test_git_runs_only_against_the_clone_root(command: str, clone: Path) -> None:
    """git reads the config of the repository it opens and starts what that config names."""
    _ship_git_dir(clone / "payload.git")
    _ship_git_dir(clone / "tools.git", worktree="..")
    (clone / "src").mkdir()
    reason = guard_reason(command.format(P=clone.as_posix()), root=clone.parents[3])
    assert reason is not None
    assert "rule 3" in reason


def test_git_in_a_shipped_git_dir_as_cwd_is_refused(clone: Path) -> None:
    _ship_git_dir(clone / "tools.git", worktree="..")
    root = clone.parents[3]
    assert guard_reason("git status --short", root=root, cwd=clone / "tools.git") is not None
    assert guard_reason("git status --short", root=root, cwd=clone) is None
    assert guard_reason("git log --oneline -- src/core.py", root=root, cwd=clone) is None


def test_a_clone_root_needs_a_real_git_directory(clone: Path) -> None:
    root = clone.parents[3]
    (clone / ".git").rmdir()
    command = f"git -C {clone.as_posix()} log"
    assert guard_reason(command, root=root) is not None
    (clone / ".git").write_text(f"gitdir: {(clone / 'tools.git').as_posix()}\n", encoding="utf-8")
    assert guard_reason(command, root=root) is not None


def test_a_linked_git_directory_is_not_a_clone_root(clone: Path) -> None:
    """A junction on Windows, a symlink elsewhere: either way not the `.git` catch made."""
    _ship_git_dir(clone / "tools.git")
    (clone / ".git").rmdir()
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(clone / "tools.git"), str(clone / ".git"))
    else:
        (clone / ".git").symlink_to(clone / "tools.git", target_is_directory=True)
    assert guard_reason(f"git -C {clone.as_posix()} log", root=clone.parents[3]) is not None


@pytest.mark.parametrize(
    ("parts", "allowed"),
    [
        (("github", "acme", "widget", "repo"), True),
        (("gitlab", "group", "sub", "project", "repo"), True),
        (("github", "acme", "repo"), False),
        (("github", "acme", "widget", "repo", "vendor", "repo"), False),
        (("github", "acme", "widget", "wiki"), False),
        (("maws", "maw-0123456789", "x", "repo"), False),
    ],
)
def test_clone_roots_are_where_catch_puts_them(
    parts: tuple[str, ...], allowed: bool, tmp_path: Path
) -> None:
    root = (tmp_path / "crab-prey").resolve()
    directory = root.joinpath(*parts)
    (directory / ".git").mkdir(parents=True)
    reason = guard_reason(f"git -C {directory.as_posix()} log --oneline", root=root)
    assert (reason is None) is allowed


def test_hook_treats_undecodable_input_like_any_other_transport_failure() -> None:
    """A traceback would exit 1, which the agent also reads as allow, only louder."""
    stream = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{"), encoding="utf-8")
    assert main(stdin=stream) == 0
