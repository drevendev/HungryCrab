"""Fail-closed prey-cache shell guard (HungryCrab#85)."""

from __future__ import annotations

import io
import json
import sys
import tomllib
from pathlib import Path
from typing import Literal

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
    entry = next(
        item for item in hooks["hooks"]["PreToolUse"] if item["matcher"] == "Bash|PowerShell"
    )
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
        # git takes `-O` inside a cluster and any unambiguous abbreviation of a long option.
        "git -C {P} grep -lOsh echo",
        "git -C {P} grep -nOsh -e x -- install.sh",
        "git -C {P} grep --open=sh echo",
        "git -C {P} grep --open-files=sh echo",
        "git -C {P} grep --open-files-in-pager=sh echo",
        "git -C {P} cat-file --textc HEAD:x",
        "git -C {P} cat-file --filt HEAD:x",
        "git -C {P} show --ext HEAD",
        "git -C {P} log --out=/tmp/x.sh",
        "git -C {P} log --outp /tmp/x.sh",
        # symbolic-ref writes with two names and deletes with -d.
        "git -C {P} symbolic-ref HEAD refs/heads/evil",
        "git -C {P} symbolic-ref -d HEAD",
        "git -C {P} symbolic-ref --delete HEAD",
        "git -C {P} symbolic-ref -m msg HEAD refs/heads/x",
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
        "git -C {P} symbolic-ref HEAD",
        "git -C {P} symbolic-ref -q --short HEAD",
        "git -C {P} log -p --output-indicator-new=+",
        "git -C {P} show --cc HEAD",
        "git -C {P} log --no-ext-diff --no-textconv -p",
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
    # Text about the cache, and the directory that holds it, are not the cache.
    'git commit -m "docs: mention .cache/hungry-crab here"',
    "grep -rn '.cache/hungry-crab' src | head",
    "git grep '.cache/hungry-crab'",
    "git grep -n '.cache/hungry-crab'",
    "rg -n '.cache/hungry-crab' docs | wc -l",
    "sed -i 's|.cache/hungry-crab|.cache/crab|' README.md",
    "du -sh ~/.cache",
    "find ~/.cache -maxdepth 1 -type d",
    "ls -la ~/.cache | grep crab",
    "cd ~/.cache && ls",
    'cd "$(git rev-parse --show-toplevel)" && rm -rf node_modules/.cache/*',
    # This repository's own folder is HungryCrab: `hungry` without the dash is no hint.
    "cd C:/MyProjects/ClaudeCode/HungryCrab && for f in src/*.py; do echo $f; done",
    "git -C /c/MyProjects/ClaudeCode/HungryCrab status",
]


# A hint of the cache plus a construct the guard does not model: refused without modelling it.
HINTED_UNMODELLED = [
    f"bash -c 'python ~/.cache/hungry-c*/{TAIL}'",
    f'sh -c "python ~/.cache/hungry-c*/{TAIL}"',
    f"timeout 60 sh -c 'cd ~/.cache && python hungry-crab/{TAIL}'",
    f"bash <<< 'python ~/.cache/hungry-c*/{TAIL}'",
    f"eval python ~/.cache/hungry-c\\*/{TAIL}",
    f"eval 'python ~/.cache/hungry-c*/{TAIL}'",
    f"p=~/.cache/hungry-crabX; python ${{p%X}}/{TAIL}",
    f"p=~/.cachexhungry-crab; python ${{p/x//}}/{TAIL}",
    f"p=~/.cache/hungry-crab.old; python ${{p:0:-4}}/{TAIL}",
    f"python {{~,/x}}/.cache/hungry-*/{TAIL}",
    f"python {{~,}}/.cache/hungry-c*/{TAIL}",
    f"a=(~/.cache/hungry-*); python ${{a[0]}}/{TAIL}",
    f"a=(~/.cache/hungry-*); python $a/{TAIL}",
    f"python ~/$'\\x2e'cache/hungry-crab/{TAIL}",
    f"python ~$'\\x2f.cache\\x2fhungry-crab\\x2f'{TAIL}",
    f"set -- ~/.cache/hungry-*; python $1/{TAIL}",
    f"CDPATH=~/.cache cd hungry-crab && python {TAIL}",
    "find ~/.cache -name setup.py -exec python {} ;",
    f"echo ~/.cache/hungry-c*/{TAIL} | xargs python",
    f"bash <<'EOF'\npython ${{p%X}}/hungry-crab/{TAIL}\nEOF",
]

# The same constructs with nothing pointing at the cache keep the scope gate's decision.
UNHINTED_CONSTRUCTS = [
    "echo ${x%y} ${#x} $1 $'\\t'",
    "bash -c 'make test'",
    "a=(1 2); echo ${a[0]}",
    "echo {a,b}",
    'cd "$(git rev-parse --show-toplevel)" && make',
    "find . -name '*.pyc' -exec rm {} +",
    "git ls-files | xargs wc -l",
    # Claude Code's own commit idiom: the quoted here-document is literal text.
    "git commit -m \"$(cat <<'EOF'\nfix(guard): the hungry-crab hook reads ${x%y}\nEOF\n)\"",
]


DIRECTORY_VARIABLES = [
    ("bash", f"cd ~/.cache && python $PWD/hungry-crab/{TAIL}"),
    ("bash", f'cd ~/.cache && python "$PWD/hungry-crab/{TAIL}"'),
    ("bash", f"cd ~/.cache && python ${{PWD}}/hungry-crab/{TAIL}"),
    ("bash", f"cd ~/.cache && python ~+/hungry-crab/{TAIL}"),
    ("bash", "cd ~/.cache/hungry-crab && python ~+/github/acme/widget/repo/setup.py"),
    ("bash", f"pushd ~/.cache >/dev/null; python ~+/hungry-crab/{TAIL}"),
    ("bash", f"cd ~/.cache && cd /tmp && python $OLDPWD/hungry-crab/{TAIL}"),
    ("bash", f"cd ~/.cache && cd /tmp && python ~-/hungry-crab/{TAIL}"),
    ("bash", f"pushd ~/.cache && pushd /tmp && popd && python $PWD/hungry-crab/{TAIL}"),
    ("ps", r"cd ~/.cache; python $PWD/hungry-crab/github/acme/widget/repo/setup.py"),
    ("ps", r'Set-Location ~/.cache; python "$PWD\hungry-crab\github\acme\widget\repo\setup.py"'),
    ("ps", r"sl ~\.cache; python $pwd\hungry-crab\github\acme\widget\repo\setup.py"),
    ("ps", r"Push-Location ~\.cache; python $PWD\hungry-crab\github\acme\widget\repo\setup.py"),
    ("ps", r'cd ~\.cache; python "$(Get-Location)\hungry-crab\github\acme\widget\repo\setup.py"'),
    ("ps", r"cd ~\.cache; python (Get-Location)\hungry-crab\github\acme\widget\repo\setup.py"),
]


@pytest.mark.parametrize(("shell", "command"), DIRECTORY_VARIABLES)
def test_the_working_directory_variables_follow_every_directory_change(
    shell: str, command: str, home: Path
) -> None:
    """`$PWD`, `$OLDPWD`, `~+` and `~-` are wherever the command has gone, not the event cwd."""
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw", shell=shell_name(shell))
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize(
    ("shell", "command"),
    [
        ("bash", 'docker run --rm -v "$PWD:/w" -w /w python:3.12 python -V'),
        ("bash", "cd src && python $PWD/../tools/build.py"),
        ("bash", "echo ~+ ~-"),
        ("ps", "$p = Join-Path $PWD 'src'; Get-ChildItem $p"),
        ("ps", "Set-Location src; python $PWD\\build.py"),
    ],
)
def test_the_working_directory_variables_stay_ordinary_away_from_the_cache(
    shell: str, command: str, home: Path
) -> None:
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    assert guard_reason(command, root=root, cwd=cwd, shell=shell_name(shell)) is None


def shell_name(shell: str) -> Literal["bash", "powershell"]:
    return "powershell" if shell == "ps" else "bash"


@pytest.mark.parametrize("command", HINTED_UNMODELLED)
def test_a_cache_hint_with_an_unmodelled_construct_fails_closed(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize("command", UNHINTED_CONSTRUCTS)
def test_an_unmodelled_construct_without_a_hint_is_not_refused_for_it(
    command: str, home: Path
) -> None:
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None


def test_a_custom_root_is_hinted_by_its_name(home: Path) -> None:
    root = home / "data" / "crab-prey"
    command = f"python ${{X:-{root.parent.as_posix()}}}/crab-prey/{TAIL}"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is not None
    assert guard_reason("echo ${X:-default}", root=root, cwd=home / "work" / "maw") is None


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
    "command",
    [
        "ls *",
        "echo $FOO",
        "grep -r foo *",
        "python script.py",
        "git status",
        "du -sh .[!.]* *",
        "tar czf dotfiles.tgz .*rc .config",
        "chmod -R go-rwx .c*",
    ],
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
        # cmd and PowerShell spellings, read textually on every platform.
        f'python "%USERPROFILE%/prey/{TAIL}"',
        f'python "$env:USERPROFILE/prey/{TAIL}"',
    ]
    if sys.platform == "win32":
        # A backslash is a separator only on Windows; elsewhere it is part of a file name.
        commands += [
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


PS_PREY = r"~\.cache\hungry-crab\github\acme\widget\repo"

# PowerShell commands that run, load or assemble prey (HungryCrab#141).
POWERSHELL_HOSTILE = [
    r"python $env:USERPROFILE\.cache\hungry-crab\github\acme\widget\repo\setup.py",
    r'& "$HOME\.cache\hungry-crab\github\acme\widget\repo\setup.py"',
    rf"& {PS_PREY}\setup.ps1",
    rf". {PS_PREY}\setup.ps1",
    rf"Invoke-Expression (Get-Content {PS_PREY}\x.ps1 -Raw)",
    rf"iex (gc {PS_PREY}\x.ps1 -Raw)",
    rf"Get-Content {PS_PREY}\x.ps1 | Invoke-Expression",
    rf"Get-Content {PS_PREY}\README.md | python -",
    rf"Start-Process {PS_PREY}\bin\tool.exe",
    rf"Get-ChildItem {PS_PREY} -Recurse | ForEach-Object {{ & $_.FullName }}",
    rf"cmd /c {PS_PREY}\setup.bat",
    rf"{PS_PREY}\bin\gc.exe README.md",
    rf"Get-Content {PS_PREY}\README.md > out.ps1",
    rf"Get-Content {PS_PREY}\README.md; python x.py",
    rf"Get-Content {PS_PREY}\README.md && python x.py",
    rf"Get-Content {PS_PREY}\README.md`; python x.py",
    rf"Get-Content “{PS_PREY}\README.md”; python x.py",
    r"Get-Content ~\.cache\hungry-*\github\acme\widget\repo\README.md",
    r"Get-Content -Path ${env:USERPROFILE}\.cache\hungry-crab\README.md",
    r"python ~\.cache\hungry-*\github\acme\widget\repo\setup.py",
    r"$d = 'hungry-crab'; python ~\.cache\$d\github\acme\widget\repo\setup.py",
    r"Set-Location ~\.cache; python hungry-crab\github\acme\widget\repo\setup.py",
    r"cd -Path ~\.cache; python hungry-crab\github\acme\widget\repo\setup.py",
    r"python (Join-Path $HOME '.cache\hungry-crab\github\acme\widget\repo\setup.py')",
    r"foreach ($d in '~\.cache') { python $d\hungry-crab\github\acme\widget\repo\setup.py }",
    # Paths PowerShell computes: refused for the hint, without modelling the expression.
    r"python (Join-Path $HOME .cache hungry-crab github acme widget repo setup.py)",
    r"python (Join-Path $HOME '.cache' | Join-Path -ChildPath 'hungry-crab\github\x\setup.py')",
    r"python ($HOME + '\.cache\hun' + 'gry-crab\github\acme\widget\repo\setup.py')",
    r"python ('{0}\.cache\{1}\github\acme\widget\repo\setup.py' -f $HOME, 'hungry-crab')",
    r"python (($HOME, '.cache', 'hungry-crab', 'github\acme\widget\repo\setup.py') -join '\')",
    r"python ([IO.Path]::Combine($HOME, '.cache', 'hungry-crab', 'github\x\setup.py'))",
    r"python ('~\.cache\HUNGRY-XXXX\github\acme\widget\repo\setup.py' -replace 'XXXX','crab')",
    r"Start-Process python -ArgumentList (Join-Path $HOME .cache hungry-crab x setup.py)",
    r"bash -c 'python ~/.cache/hungry-c*/github/acme/widget/repo/setup.py'",
    r"pwsh -NoProfile -Command 'python ~\.cache\hungry-c*\github\acme\widget\repo\setup.py'",
    r"cmd /c 'python %USERPROFILE%\.cache\hungry-c*\x.py'",
    r"python ~\.cache\hungry`-crab\github\acme\widget\repo\setup.py",
]

# Plain reads of the cache: read cmdlets with literal arguments.
POWERSHELL_READS = [
    rf"Get-Content {PS_PREY}\README.md",
    rf"gc {PS_PREY}\README.md | Select-Object -First 20",
    rf"Get-ChildItem {PS_PREY} -Recurse",
    rf"Select-String -Path {PS_PREY}\core.py -Pattern 'def .*'",
    rf"Test-Path {PS_PREY}\setup.py",
    rf"Get-Item {PS_PREY}\README.md",
    rf"Get-Content -LiteralPath {PS_PREY}\README.md | Measure-Object -Line",
    r"Get-ChildItem ~\.cache",
]

# Ordinary PowerShell that never approaches the cache.
POWERSHELL_ORDINARY = [
    "Get-ChildItem",
    "git status",
    "python -m pytest",
    "$env:Path -split ';'",
    "Get-Content README.md",
    "Set-Location ~; Get-ChildItem",
    "Get-ChildItem -Recurse -Filter *.py",
    r"Select-String -Path src\*.py -Pattern TODO",
    "$x = 1; $x + 2",
    "uv run pytest -q",
    "Get-Process | Where-Object { $_.CPU -gt 10 }",
    "Remove-Item -Recurse -Force build",
    "foreach ($f in Get-ChildItem *.py) { python $f }",
    "Write-Output $HOME",
    r"& .\build.ps1",
    "cd $PSScriptRoot",
    # Another child of `~/.cache`, built with an expression, is not the cache.
    r"Get-ChildItem (Join-Path $HOME '.cache\pip')",
    r"Remove-Item -Recurse $HOME\.cache\uv\builds-v0",
    r'& "C:\Program Files\Git\bin\git.exe" status',
    r"Get-ChildItem $env:USERPROFILE\.cache\pip",
    r"Get-ChildItem ~\.cache\pip\*",
    r"python ~\.cache\pip\x.py",
]


def _powershell(command: str, home: Path) -> str | None:
    event = {
        "tool_name": "PowerShell",
        "tool_input": {"command": command},
        "cwd": str(home / "work" / "maw"),
    }
    return event_guard_reason(event, root=home / ".cache" / "hungry-crab")


@pytest.mark.parametrize("command", POWERSHELL_HOSTILE)
def test_powershell_that_runs_or_assembles_prey_fails_closed(command: str, home: Path) -> None:
    reason = _powershell(command, home)
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize("command", POWERSHELL_READS)
def test_powershell_read_cmdlets_with_literal_arguments_are_allowed(
    command: str, home: Path
) -> None:
    assert _powershell(command, home) is None


@pytest.mark.parametrize("command", POWERSHELL_ORDINARY)
def test_ordinary_powershell_stays_allowed(command: str, home: Path) -> None:
    assert _powershell(command, home) is None


@pytest.mark.parametrize(
    "command",
    [
        r"& '{root}\github\acme\widget\repo\setup.ps1'",
        r"python $HOME\store\prey\github\acme\widget\repo\setup.py",
        r"python ~\store\pr*\github\acme\widget\repo\setup.py",
        r"python ~\store\*\github\acme\widget\repo\setup.py",
        r"Set-Location ~\store; python prey\github\acme\widget\repo\setup.py",
        r"$p = '~\store'; python $p\prey\github\acme\widget\repo\setup.py",
        r"Get-ChildItem ~\store | ForEach-Object { python $_ }",
    ],
)
def test_powershell_judges_a_custom_root_without_the_default_spelling(
    command: str, home: Path
) -> None:
    """No `.cache\\hungry-crab` to match: only the path analysis can see these."""
    root = home / "store" / "prey"
    event = {
        "tool_name": "PowerShell",
        "tool_input": {"command": command.replace("{root}", str(root))},
        "cwd": str(home / "work" / "maw"),
    }
    assert event_guard_reason(event, root=root) is not None
    read = {**event, "tool_input": {"command": rf"Get-Content {root}\README.md"}}
    assert event_guard_reason(read, root=root) is None


@pytest.mark.skipif(sys.platform != "win32", reason="elsewhere PowerShell's `$HOME` is `HOME`")
def test_powershell_home_is_the_profile_whatever_home_says(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PowerShell's `$HOME` is the profile directory; a Git or Emacs user may set `HOME`
    elsewhere. Both readings are judged."""
    monkeypatch.setenv("HOME", str(home.parent / "unix-home"))
    for command in (
        r"python $HOME\.cache\hungry-c*\github\acme\widget\repo\setup.py",
        r"python ~\.cache\hungry-c*\github\acme\widget\repo\setup.py",
    ):
        assert _powershell(command, home) is not None, command


def test_powershell_input_it_cannot_inspect_is_refused() -> None:
    reason = event_guard_reason({"tool_name": "PowerShell", "tool_input": None})
    assert reason is not None and reason.startswith("PowerShell")
    assert event_guard_reason({"tool_name": "PowerShell", "tool_input": {}}) is not None


def _short_name(path: Path) -> str | None:
    """The 8.3 alias Windows gives ``path``, or ``None`` where the volume makes none."""
    if sys.platform != "win32":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):
        return None
    return buffer.value if buffer.value.casefold() != str(path).casefold() else None


@pytest.mark.skipif(sys.platform != "win32", reason="device paths and 8.3 names are Windows'")
def test_device_and_short_spellings_of_a_cache_path_are_judged(home: Path) -> None:
    """A quoted Windows path reaches the program intact, so its spelling must not matter."""
    root = home / ".cache" / "hungry-crab"
    (root / "github" / "acme" / "widget" / "repo").mkdir(parents=True)
    short = _short_name(root)
    if short is None:
        pytest.skip("this volume does not generate 8.3 names")
    cwd = home / "work" / "maw"
    script = "\\github\\acme\\widget\\repo\\setup.py"
    for spelling in ("\\\\?\\" + short, "\\\\.\\" + short, short, "\\\\?\\" + str(root)):
        assert guard_reason(f"python '{spelling}{script}'", root=root, cwd=cwd), spelling
        assert guard_reason(f"cat '{spelling}{script}'", root=root, cwd=cwd) is None, spelling
    custom = home / "crab-prey"
    command = f"python '\\\\?\\{custom}{script}'"
    assert guard_reason(command, root=custom, cwd=cwd) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="device and share paths are Windows'")
def test_device_and_share_spellings_of_the_cache_are_judged(home: Path) -> None:
    """`\\\\?\\Volume{...}`, `\\\\?\\GLOBALROOT`, `\\\\localhost\\C$` open the cache; a share
    the guard cannot reduce is judged by its text and never resolved over the network."""
    import ctypes

    root = home / "data" / "crab-prey"
    target = root / "github" / "acme" / "widget" / "repo"
    target.mkdir(parents=True)
    volume = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(target.anchor, volume, 260)
    device = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.QueryDosDeviceW(target.drive, device, 260)
    tail = str(target)[3:] + "\\setup.py"
    letter = target.drive[0]
    cwd = home / "work" / "maw"
    for spelling in (
        volume.value + tail,
        "\\\\?\\GLOBALROOT" + device.value + "\\" + tail,
        f"\\\\localhost\\{letter}$\\{tail}",
        f"\\\\127.0.0.1\\{letter}$\\{tail}",
        f"\\\\?\\UNC\\localhost\\{letter}$\\{tail}",
        "\\\\nohost-crab\\share\\data\\crab-prey\\github\\setup.py",
        "\\\\nohost-crab\\share\\data\\CRAB-P~1\\github\\setup.py",
        "//nohost-crab/share/data/crab-prey/github/setup.py",
    ):
        assert guard_reason(f"python '{spelling}'", root=root, cwd=cwd) is not None, spelling
    assert guard_reason(f"cat '\\\\localhost\\{letter}$\\{tail}'", root=root, cwd=cwd) is None
    assert (
        guard_reason("python '\\\\nohost-crab\\share\\tools\\build.py'", root=root, cwd=cwd) is None
    )


def test_every_decision_stays_fast(home: Path) -> None:
    """A slow decision is an allowance: a hook that times out lets the call proceed. A 10 KB
    command of share names and a long chain of unknown expansions decides in well under a
    second, against a long custom root, and still refuses what it hides."""
    import time

    root = home / "OneDrive - Contoso Corporation Aleksandr.Konstantinov" / "crab-prey"
    chain = "".join(f"$v{i}?" for i in range(800)) + "Q"
    shares = " ".join(f"//nohost-crab{i}/s" for i in range(400))
    hostile = f'"{root.parent.as_posix()}/crab-pre"?/github/acme/widget/repo/setup.py'
    cwd = home / "work" / "maw"
    for command, shell in (
        (f"python -X {chain} {shares} {hostile}", "bash"),
        (f"python -X {chain} {shares} {hostile}", "powershell"),
        ("python -X " + "".join(f"$v{i}a" for i in range(600)) + "Q README.md", "bash"),
        ("cd $D && ls " + "*a" * 600 + "Q", "bash"),
    ):
        start = time.perf_counter()
        reason = guard_reason(command, root=root, cwd=cwd, shell=shell)  # type: ignore[arg-type]
        assert time.perf_counter() - start < 0.5, command[:40]
        if hostile in command:
            assert reason is not None
    assert len(f"python -X {chain} {shares} {hostile}") > 10_000


def test_a_long_chain_of_directory_changes_decides_in_time(home: Path) -> None:
    """Each `cd` multiplies the directories every word is resolved against. Past a budget the
    guard decides from the text: a command that names the cache is refused, one that does not
    is let through, and either way well inside the hook timeout."""
    import time

    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    hops = "; ".join(f"cd d{i}" for i in range(1000))
    hostile = f"cd ~/.cache; {hops}; cd hungry-crab/github/acme/widget/repo; python setup.py"
    ordinary = f"{hops}; python setup.py"
    assert len(hostile) > 8_000
    for command, refused in ((hostile, True), (ordinary, False)):
        start = time.perf_counter()
        reason = guard_reason(command, root=root, cwd=cwd)
        assert time.perf_counter() - start < 0.5
        assert (reason is not None) is refused
    # Inside the cache a large command is refused even when its text names nothing.
    clone = root / "github" / "acme" / "widget" / "repo"
    clone.mkdir(parents=True)
    padded = "python setup.py " + " ".join(f"arg{i}" for i in range(400))
    assert guard_reason(padded, root=root, cwd=clone) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash drive paths exist on Windows only")
def test_git_takes_a_git_bash_drive_path_as_cat_does(home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    clone = root / "github" / "acme" / "widget" / "repo"
    (clone / ".git").mkdir(parents=True)
    msys = f"/{clone.drive[0].lower()}{clone.as_posix()[2:]}"
    cwd = home / "work" / "maw"
    assert guard_reason(f"git -C {msys} log --oneline -5", root=root, cwd=cwd) is None
    assert guard_reason(f"cat {msys}/README.md", root=root, cwd=cwd) is None
    assert guard_reason(f"git -C {msys}/src log", root=root, cwd=cwd) is not None


@pytest.mark.parametrize(
    "command",
    [f"cat {PREY}/README.md |& wc -l", f"cat {PREY}/README.md >| /tmp/x.sh", f"cat {PREY}/x &>> y"],
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


CLONE_TAIL = ".cache/hungry-crab/github/acme/widget/repo"


@pytest.mark.parametrize(
    ("second", "allowed"),
    [
        (f"'~/{CLONE_TAIL}'", False),
        (f'"~/{CLONE_TAIL}"', False),
        (f"\\~/{CLONE_TAIL}", False),
        (f"''~/{CLONE_TAIL}", False),
        # The tilde prefix runs to the first unquoted slash; anything quoted in it, even an
        # empty string, keeps the `~` literal.
        (f'~"/{CLONE_TAIL}"', False),
        (f"~\\/{CLONE_TAIL}", False),
        (f"~''/{CLONE_TAIL}", False),
        (f'~""/{CLONE_TAIL}', False),
        (f"~$'/'{CLONE_TAIL}", False),
        (f"~/{CLONE_TAIL}", True),
        (f"~/'{CLONE_TAIL}'", True),
    ],
)
def test_git_takes_a_quoted_tilde_literally(second: str, allowed: bool, home: Path) -> None:
    """Bash expands only an unquoted leading `~`: `-C '~/...'` opens a directory named `~`,
    here one the prey commits holding a git directory whose config names a program."""
    root = home / ".cache" / "hungry-crab"
    clone = root / "github" / "acme" / "widget" / "repo"
    (clone / ".git").mkdir(parents=True)
    _ship_git_dir(clone / "~" / Path(CLONE_TAIL), worktree=".")
    command = f"git -C {clone.as_posix()} -C {second} status"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert (reason is None) is allowed


def test_a_quoted_tilde_is_judged_as_the_directory_it_names(home: Path) -> None:
    """`'~/x'` reaches whatever a directory called `~` holds; `~/x` reaches the home's `x`."""
    root = home / ".cache" / "hungry-crab"
    (root / "github").mkdir(parents=True)
    maw = home / "work" / "maw"
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(root), str(maw / "~"))
    else:
        (maw / "~").symlink_to(root, target_is_directory=True)
    script = "github/acme/widget/repo/setup.py"
    assert guard_reason(f"python '~/{script}'", root=root, cwd=maw) is not None
    assert guard_reason(f"python \\~/{script}", root=root, cwd=maw) is not None
    assert guard_reason(f'python ~"/{script}"', root=root, cwd=maw) is not None
    assert guard_reason(f"python ~''/{script}", root=root, cwd=maw) is not None
    assert guard_reason(f"python ~/{script}", root=root, cwd=maw) is None
    assert guard_reason(f"cat '~/{script}'", root=root, cwd=maw) is None


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


def test_hook_reads_the_bytes_under_a_text_stream_of_another_code_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HungryCrab#315: a cp1252 text read of UTF-8 bytes would mangle the cache root."""
    cache = tmp_path / "добыча"
    monkeypatch.setenv("CRAB_CACHE_DIR", str(cache))
    command = f"python {cache.as_posix()}/github/acme/widget/repo/setup.py"
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    data = json.dumps(event, ensure_ascii=False).encode("utf-8")
    stream = io.TextIOWrapper(io.BytesIO(data), encoding="cp1252", errors="surrogateescape")
    assert main(stdin=stream) == 2


def test_hook_treats_undecodable_input_like_any_other_transport_failure() -> None:
    """A traceback would exit 1, which the agent also reads as allow, only louder."""
    stream = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{"), encoding="utf-8")
    assert main(stdin=stream) == 0
