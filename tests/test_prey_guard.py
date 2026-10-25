"""Fail-closed prey-cache shell guard (HungryCrab#85)."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Literal

import pytest

from hungry_crab import prey_guard
from hungry_crab.prey_guard import event_guard_reason, guard_reason, main

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/tmp/crab-prey")
PREY = "/tmp/crab-prey/github/acme/widget/repo"
PREY_MATCHER = (
    "Bash|PowerShell|Monitor|mcp__terminal__run_in_terminal|Write|Edit|MultiEdit|NotebookEdit"
)


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


def test_a_tool_without_a_command_or_a_written_file_is_outside_this_guard() -> None:
    event = {"tool_name": "Read", "tool_input": {"file_path": f"{PREY}/setup.py"}}
    assert event_guard_reason(event, root=CACHE) is None
    assert event_guard_reason({"tool_name": "Glob", "tool_input": "x"}, root=CACHE) is None


@pytest.mark.parametrize("tool", ["Monitor", "SomeFutureShell"])
def test_any_tool_that_runs_a_command_is_judged_as_bash(tool: str) -> None:
    """Monitor runs a bash script in the same environment as Bash; any other tool whose input
    carries a string `command` is judged the same way, should a matcher send it here."""
    event = {"tool_name": tool, "tool_input": {"command": f"python {PREY}/setup.py"}}
    assert event_guard_reason(event, root=CACHE) is not None
    assert event_guard_reason({**event, "tool_input": {"command": "ls"}}, root=CACHE) is None
    unreadable = event_guard_reason({"tool_name": "Monitor", "tool_input": {}}, root=CACHE)
    assert unreadable is not None


def test_hook_manifest_runs_the_packaged_guard_through_the_launcher() -> None:
    """The Bash hook runs this module, from the plugin's own ``src/``, not a console script.

    ``hooks/guard.py`` maps ``prey`` to the same module the ``crab-prey-guard`` console script
    names, so the hook and the manual command are one guard; the launcher's own behaviour is
    covered in ``tests/test_hooks.py``.
    """
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entry = next(item for item in hooks["hooks"]["PreToolUse"] if item["matcher"] == PREY_MATCHER)
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
# A requirement string with a substitution, as `uv tool install` takes it (HungryCrab#392).
REQUIREMENT = "hungry-crab @ git+https://github.com/drevendev/HungryCrab@$(git rev-parse HEAD)"

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
    # HungryCrab#392: a search pattern, a sibling under `~/.cache`, an exit status, a requirement
    # string or a URL is no hint of the cache, and a quoted awk program is not shell syntax.
    "uv pip list | grep hungry-crab | awk '{print $2}'",
    "uv tool list | grep -c hungry-crab; echo $?",
    "grep -rl hungry-crab docs | xargs wc -l",
    f'uv tool install --force "{REQUIREMENT}"',
    "du -sh ~/.cache/{uv,pip}",
    'ls ~/.cache/uv; echo "exit=$?"',
    'for n in hungry-crab hungry_crab; do code=$(curl -s "https://pypi.org/pypi/$n/json"); done',
    "gh issue comment 392 --body \"$(cat <<'EOF'\nit's hungry-crab and $(curl x) ${x%y}\nEOF\n)\"",
    "git commit -F - <<'EOF'\nfix: it's the hungry-crab hook\nEOF",
    "ls; echo $?",
    "git -c color.ui=always log --oneline -3",
    "find ~/work -name '*.md' -exec grep -l foo {} +",
    "git log --oneline -5 | while read sha rest; do git show --stat $sha; done",
    "cd ~/.cache/uv && python $(git rev-parse --show-toplevel)/x.py",
    "echo \"unbalanced # it's a comment\"  # and it's another",
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
def test_ordinary_commands_stay_allowed(
    command: str, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Known-safe controls must not inherit a developer's cache-bearing executable search path.
    monkeypatch.setenv("PATH", str(home / "bin"))
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
def test_ordinary_powershell_stays_allowed(
    command: str, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(home / "bin"))
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
        # Generous for a loaded CI runner; the hook's own timeout is 10 s.
        assert time.perf_counter() - start < 2.0, command[:40]
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
        assert time.perf_counter() - start < 2.0
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


# --- paths the shell builds from the command text (GHSA-443x-5484-qr4g) ----------------------

REPO = "github/acme/widget/repo"

# A script handed to a nested shell, `eval`, `xargs` or `find -exec`, with the cache spelled
# by a glob or an 8.3 alias rather than by its name.
NESTED_SCRIPTS = [
    f"bash -c 'python ~/.cach?/hungr?-crab/{TAIL}'",
    f"bash -c 'python ~/CACHE~1/HUNGRY~1/{TAIL}'",
    f'sh -c "python $HOME/CACHE~1/HUNGRY~1/{TAIL}"',
    f"eval 'python ~/CACHE~1/HUNGRY~1/{TAIL}'",
    f"echo HUNGRY~1 | xargs -I@ python ~/CACHE~1/@/{TAIL}",
    f"find ~ -maxdepth 0 -exec sh -c 'python $0/.cach?/hungr?-crab/{TAIL}' {{}} ';'",
    f"bash -c 'python ~/.*/*/{TAIL}'",
    f"bash <<'EOF'\npython ~/.cach?/hungr?-crab/{TAIL}\nEOF",
    f"cat <<EOF | bash\npython ~/.cach?/hungr?-crab/{TAIL}\nEOF",
    f"source /dev/stdin <<< 'cd ~/.c*; cd *; python {TAIL}'",
    f"env -S 'python ~/.cach?/hungr?-crab/{TAIL}'",
    f"watch 'python ~/.cach?/hungr?-crab/{TAIL}'",
    f"git -c core.pager='python ~/.cach?/hungr?-crab/{TAIL}' log",
    f"shopt -s expand_aliases\nalias x='python ~/.cach?/hungr?-crab/{TAIL}'\nx",
    "find ~ -name setup.py -exec python {} ;",
    f"echo $($($($(python ~/.cach?/hungr?-crab/{TAIL}))))",
]

# A directory change the shell makes where 0.6.1 did not look for one.
DIRECTORY_CHANGES = [
    f"f(){{ cd ~/.cache; }}; f; cd hungry-crab/{REPO}; python setup.py",
    f"function f {{ cd ~/.cache; }}; f; cd hungry-crab/{REPO} && python setup.py",
    f"case x in *) cd ~/.cache;; esac; cd hungry-crab/{REPO}; python setup.py",
    f"$NOPE cd ~/.cache; cd hungry-crab/{REPO}; python setup.py",
    f"x=cd; $x ~/.cache; python hungr?-crab/{TAIL}",
    f"cd ~; cd .c*; cd *; python {TAIL}",
    f"cd ~/.c*; cd *; python {TAIL}",
    f"cd 2>/dev/null ~/.cache; python hungr?-crab/{TAIL}",
    f"p=~/.cache/uv; cd ${{p%/uv}}; cd *; python {TAIL}",
    f'cd "$(dirname ~/.cache/x)"; cd *; python {TAIL}',
    f"cd ~/.cache; python /proc/self/cwd/hungr?-crab/{TAIL}",
    f"cd ~/.cache; python /proc/$$/cwd/*/{TAIL}",
]

# A path attached to a short option, a response file, or one entry of a path list.
ATTACHED_PATHS = [
    f"make -C$HOME/.c''ache/hungry-crab/{REPO}",
    f"make -C$HOME/.cache//hungry-crab/{REPO}",
    f"d=hungry-crab; make -C$HOME/.cache/$d/{REPO}",
    f"perl -I$HOME/.c''ache/hungry-crab/{REPO}/lib -MEvil -e1",
    f"pip install -e$HOME/.c''ache/hungry-crab/{REPO}",
    f"cp -t$HOME/.c''ache/hungry-crab/{REPO} ./payload.sh",
    f"gcc @$HOME/.c''ache/hungry-crab/{REPO}/args.rsp x.c",
    f"PYTHONPATH=\"x;$USERPROFILE/.ca\"'che/hungry-crab/{REPO}' python -c 'import evil'",
    f"PYTHONPATH=x:$HOME/.c''ache/hungry-crab/{REPO} python -c 'import evil'",
    f"tar -xvf$HOME/.c''ache/hungry-crab/{REPO}/x.tar",
]

# Text an expansion, a substitution, a builtin or a brace alternative puts into a word.
BUILT_TEXT = [
    f"python ~/.cach${{x:-e/hungr}}y-crab/{TAIL}",
    f"x=q; python ~/.cach${{x/q/e/hungr}}y-crab/{TAIL}",
    f"python ~/.cach$(echo e/hungr)y-crab/{TAIL}",
    f"python ~/.cach`echo e/hungr`y-crab/{TAIL}",
    f'printf -v d %s/.c%s/hungry-crab/{REPO} "$HOME" ache; python $d/setup.py',
    f"python {{../../.cach?/hungr?-crab,}}/{TAIL}",
    f"x=~/.cache/; x+=hungry-crab; python $x/{TAIL}",
    f"python $(echo ~/.cache/hungry-cra)$(echo b)/{TAIL}",
    f"x=~/.cache; cd $(echo $x) && python */{TAIL}",
    f"cd ~/.cache && x=$(ls) && python $x/{TAIL}",
    f"cd ~/.cache/uv && python $(ls ..)/{TAIL}",
    f"declare -n r=d; d=hungry-crab; python ~/.cache/$r/{TAIL}",
    f"read d <<< hungry-crab; python ~/.cache/$d/{TAIL}",
    f"cd ~/.cache; ls | while read d; do python $d/{TAIL}; done",
    f"echo hungry-crab; cd ~/.cache; python $_/{TAIL}",
    f"u=x://$HOME/.cache/hungry-crab; python ${{u#x://}}/{TAIL}",
    f"a=(~/.c*); cd ${{a[0]}}; cd *; python {TAIL}",
    f"trap 'python ${{p%X}}/{TAIL}' EXIT; p=~/.cache/hungry-crabX",
]

# Text the bash tokenizer of 0.6.1 could not read, so it split it on whitespace.
TOKENIZER_GAPS = [
    f"d=hungry-crab; python ~/.cache/$d/{TAIL}  # don't",
    f"python $HOME/.c''ache/hungry-crab/{TAIL} <<EOF\nit's\nEOF",
    f"python $HOME/.c''ache/hungry-crab/{TAIL} <<'EOF'\nit's\nEOF",
]


@pytest.mark.parametrize(
    "command", NESTED_SCRIPTS + DIRECTORY_CHANGES + ATTACHED_PATHS + BUILT_TEXT + TOKENIZER_GAPS
)
def test_a_path_the_shell_builds_is_judged(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert reason is not None
    assert "rule 3" in reason


def _powershell_shapes(home: Path) -> list[str]:
    tail = REPO.replace("/", "\\") + "\\setup.py"
    script = f"python ~\\.cache\\hungry-crab\\{tail}"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return [
        f"python $HOME/.cach$('e/hungr')y-crab/{TAIL}",
        f"python $HOME\\.ca''che\\hungry-crab\\{tail} # don't",
        f"python\u00a0{home}\\.cache\\.\\hungry-crab\\{tail}",
        f"python\u2003$HOME\\.ca''che\\hungry-crab\\{tail}",
        f"python\u000b$HOME\\.ca''che\\hungry-crab\\{tail}",
        f"python ~\\.cache\\hungry-crab\\{tail} <# it's #>",
        f"$s = @'\nit's\n'@; python $HOME\\.ca''che\\hungry-crab\\{tail}",
        f"function f {{ Set-Location ~\\.cache }}; f; python hungr?-crab\\{tail}",
        f"& {{ Set-Location ~\\.cache }}; python hungr?-crab\\{tail}",
        f'cmd /c "cd %USERPROFILE%\\.cache && python hungry-crab\\{tail}"',
        f"pwsh -EncodedCommand {encoded}",
    ]


def test_powershell_reads_words_and_comments_as_powershell_does(home: Path) -> None:
    for command in _powershell_shapes(home):
        reason = _powershell(command, home)
        assert reason is not None, repr(command)
        assert "rule 3" in reason


@pytest.mark.parametrize(
    "command",
    [
        "Get-ChildItem <# it's a note #> src",
        "$s = @'\nit's plain text\n'@; Write-Output $s",
        "Write-Output 'hungry-crab (notes)'",
    ],
)
def test_powershell_comments_and_here_strings_stay_ordinary(command: str, home: Path) -> None:
    assert _powershell(command, home) is None


# HungryCrab#392: each ordinary command next to a twin that does reach the cache.
ISSUE_392 = [
    (
        "bash",
        "uv pip list | grep hungry-crab | awk '{print $2}'",
        f"uv pip list | grep hungry-crab | awk -f ~/.cache/hungry-*/{REPO}/x.awk",
    ),
    (
        "bash",
        "uv tool list | grep -c hungry-crab; echo $?",
        f"uv tool list | grep -c hungry-crab; python ~/.cache/hungr?-crab/{TAIL}; echo $?",
    ),
    (
        "bash",
        "grep -rl hungry-crab docs | xargs wc -l",
        "grep -rl hungry-crab ~/.cache/hungry-crab | xargs python",
    ),
    (
        "bash",
        f'uv tool install --force "{REQUIREMENT}"',
        f'uv tool install --force "$(echo ~/.cache/hungry-crab)/{REPO}"',
    ),
    ("bash", "du -sh ~/.cache/{uv,pip}", "du -sh ~/.cache/{uv,hungry-crab}"),
    (
        "bash",
        'ls ~/.cache/uv; echo "exit=$?"',
        f'ls ~/.cache/uv; python ~/.cache/hungr?-crab/{TAIL}; echo "exit=$?"',
    ),
    (
        "ps",
        "uv pip list | Select-String hungry-crab | ForEach-Object { $_.Line }",
        (
            "uv pip list | Select-String hungry-crab | ForEach-Object "
            f'{{ python "$HOME\\.cache\\hungry-crab\\{REPO}\\setup.py" }}'
        ),
    ),
]


@pytest.mark.parametrize(("shell", "ordinary", "twin"), ISSUE_392)
def test_a_hint_that_reaches_nothing_is_not_refused(
    shell: str, ordinary: str, twin: str, home: Path
) -> None:
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    assert guard_reason(ordinary, root=root, cwd=cwd, shell=shell_name(shell)) is None
    reason = guard_reason(twin, root=root, cwd=cwd, shell=shell_name(shell))
    assert reason is not None
    assert "rule 3" in reason


PLUGIN = "~/.claude/plugins/cache/hungry-crab/crab/0.6.2-dev.0"  # `${CLAUDE_PLUGIN_ROOT}`


@pytest.mark.parametrize(
    "command",
    [
        f"uv run --project {PLUGIN} crab menu acme/widget",
        f"uv run --project {PLUGIN} crab sniff acme/widget --maw .",
        "crab catch acme/widget --shallow --since 90d --issues 100",
        "crab compare acme/widget --maw . --issues 300",
        'uv tool install "hungry-crab @ git+https://github.com/drevendev/HungryCrab"',
        "cat receipt.json | crab serve acme/widget --maw . --ids id1 --notes n.json --as pr-branch",
        "crab spec crab:ci:ci.cache",
        "crab ledger mark crab:ci:ci.cache rejected --reason 'not for us'",
    ],
)
def test_the_skills_commands_stay_allowed(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash's mount table is Windows'")
def test_git_bash_tmp_is_the_users_temporary_directory(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `CRAB_CACHE_DIR` under `%TEMP%` is `/tmp/...` to Git Bash; another mount the guard
    cannot map is judged by the cache's name in it."""
    temp = home.parent / "Temp"
    root = temp / "crab-prey"
    (root / REPO).mkdir(parents=True)
    monkeypatch.setenv("TEMP", str(temp))
    cwd = home / "work" / "maw"
    assert guard_reason(f"python /tmp/crab-prey/{TAIL}", root=root, cwd=cwd) is not None
    assert guard_reason(f"python /usr/x/crab-prey/{TAIL}", root=root, cwd=cwd) is not None
    assert guard_reason(f"cat /tmp/crab-prey/{REPO}/README.md", root=root, cwd=cwd) is None
    assert guard_reason(f"python /tmp/crab-prey-other/{TAIL}", root=root, cwd=cwd) is None


# --- deciding in time ------------------------------------------------------------------------
#
# A decision that outlasts the hook's ten-second timeout lets the call proceed. CI runners are
# slow, so these bounds are generous; the decisions take milliseconds on a workstation.

IN_TIME = 2.0


def _timed(command: str, root: Path, cwd: Path, shell: str = "bash") -> tuple[str | None, float]:
    start = time.perf_counter()
    reason = guard_reason(command, root=root, cwd=cwd, shell=shell_name(shell))
    return reason, time.perf_counter() - start


@pytest.mark.parametrize("references", [10, 30, 100, 300])
def test_a_default_that_names_itself_decides_in_time(references: int, home: Path) -> None:
    """Each reference to `x` once re-expanded `x` and judged its text again: 167 characters
    took 26 seconds."""
    refs = " ".join("$x" for _ in range(references))
    command = f"x='${{a:-{refs}}}'; python ${{b:-~/.cach?/hungr?-crab/{TAIL}}}"
    reason, elapsed = _timed(command, home / ".cache" / "hungry-crab", home / "work" / "maw")
    assert reason is not None
    assert elapsed < IN_TIME


@pytest.mark.parametrize(
    ("command", "refused"),
    [
        ("echo {" + ",".join(f"a{i}" for i in range(4000)) + "}", False),
        ("python {" + ",".join(f"a{i}" for i in range(4000)) + ",~/.cache/hungry-crab}/x", True),
        ("echo " + "{" * 12500 + "}" * 12500, False),
        ("echo " + "{a" * 12500, False),
        ("echo " + "${a:-" * 200 + "x" + "}" * 200, False),
        # Past the budget or the nesting the guard reads, only plain text passes.
        ("echo " + "$(" * 30 + "x" + ")" * 30, True),
        ("echo " + "[" * 25000, True),
        ("echo " + "?(" * 5000 + ")" * 5000, True),
        ("ls " + "*a" * 6000 + "Q", False),
        ("eval " * 200 + "ls", False),
        (" | ".join("grep x" for _ in range(3000)), False),
        ("cat <<EOF\n" + "line $x\n" * 3000 + "EOF", False),
    ],
)
def test_long_and_deep_commands_decide_in_time(command: str, refused: bool, home: Path) -> None:
    """Every scan, expansion and nested script is charged to one budget."""
    reason, elapsed = _timed(command, home / ".cache" / "hungry-crab", home / "work" / "maw")
    assert elapsed < IN_TIME, command[:40]
    assert (reason is not None) is refused, command[:40]
    assert len(command) > 90


def test_a_long_powershell_command_decides_in_time(home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    long = "Get-ChildItem " + " ".join(f"C:\\x\\y{i}" for i in range(3000))
    deep = "Write-Output " + "(" * 3000 + "1" + ")" * 3000
    for command in (long, deep):
        reason, elapsed = _timed(command, root, cwd, "ps")
        assert elapsed < IN_TIME
        assert reason is None


def test_a_decision_through_the_launcher_finishes_inside_the_hook_timeout(
    home: Path, tmp_path: Path
) -> None:
    """The real launcher, a fresh interpreter included, on the shape that took 26 seconds."""
    refs = " ".join("$x" for _ in range(30))
    command = f"x='${{a:-{refs}}}'; python ${{b:-~/.cach?/hungr?-crab/{TAIL}}}"
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    env = {**os.environ, "CRAB_CACHE_DIR": str(home / ".cache" / "hungry-crab")}
    env.pop("CRAB_GUARD_HANDOVER", None)
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "hooks" / "guard.py"), "prey"],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    assert time.perf_counter() - start < 5.0


# --- past the budget ---------------------------------------------------------------------------

TOWARD_THE_CACHE = f"python ~/.cach?/hungr?-crab/{TAIL}"


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "".join(f"cd d{i}; " for i in range(8)),
        "echo " + " ".join("x" for _ in range(260)) + "; ",
    ],
)
def test_a_glob_toward_the_cache_is_refused_after_many_words(prefix: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(prefix + TOWARD_THE_CACHE, root=root, cwd=home / "work" / "maw")


@pytest.mark.parametrize(
    ("command", "refused"),
    [
        (TOWARD_THE_CACHE, True),
        (f"python ~/CACHE~1/HUNGRY~1/{TAIL}", True),
        (f"cd ~/.c*; cd *; python {TAIL}", True),
        (f"python ${{X}}ungry-crab/{TAIL}", True),
        ("python -m pytest -q tests", False),
        ("ls ~/.cache/pip", True),  # the cache's parent: refused once judging stopped
    ],
)
def test_past_the_budget_the_text_decides_and_fails_closed(
    command: str, refused: bool, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the budget runs out, a command whose text can match the cache or a directory on
    the way to it from home is refused; one that points nowhere near it may pass."""
    monkeypatch.setattr(prey_guard, "_STEP_BUDGET", 3)
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert (reason is not None) is refused
    if refused:
        assert "could not be parsed or judged in time" in reason


@pytest.mark.parametrize(
    ("command", "refused"),
    [
        (f"python ~/.cach?/hungr?-crab/{TAIL} 'unclosed", True),
        (f'bash -c "python ~/.c*/h*/{TAIL} \'unclosed"', True),
        ("echo 'unclosed", False),
        ('echo "unclosed', False),
    ],
)
def test_a_command_that_cannot_be_read_is_never_split_on_whitespace(
    command: str, refused: bool, home: Path
) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert (reason is not None) is refused


# --- the second review of GHSA-443x-5484-qr4g --------------------------------------------------

BACKSLASH_REPO = REPO.replace("/", "\\")
SPLIT_PATH = f"c=.cach; a=hungr; b=y-crab; python ~/${{c}}e/$a$b/{TAIL}"

# Text bash reads differently from a careful reading of the characters: `$$` before a brace or a
# quote, a carriage return (part of a word, so `#` after it opens no comment), bash 5.3's
# `${ cmd; }` and `${| cmd; }`.
LEXER_EDGES = [
    f"cat $${{ ; python ~/.cache/hungry-crab/{TAIL} ; cat x}}",
    f"cat $${{ ; python ~/.cach?/hungr?-crab/{TAIL} ; cat x}}",
    f"cat $$'\\' ; python ~/.cache/hungr\"\"y-crab/{TAIL} ; cat '\\'",
    f'echo "$${{" ; python ~/.cach?/hungr?-crab/{TAIL} ; echo x}}"',
    f"cat x\r# ; python ~/.cach?/hungr?-crab/{TAIL}",
    f"cat x\r# ; python ~/.cache/hungr''y-crab/{TAIL}",
    f"cat ${{ python ~/.cache/hungry-crab/{TAIL}; }}",
    f"cat ${{ python ~/.cach?/hungr?-crab/{TAIL}; }}",
    f"cat ${{| python ~/.cache/hungry-crab/{TAIL}; }}",
]

# A command pushed past what the guard reads in full is not judged more leniently there.
FORCED_FALLBACK = [
    "eval :; " * 17 + SPLIT_PATH,
    'echo "$${"; ' + SPLIT_PATH,
    SPLIT_PATH + " \\",
    "echo " + "$(" * 25 + "true" + ")" * 25 + "; " + SPLIT_PATH,
]

# `grep` as a word another command reads is not grep searching for a pattern.
NOT_THE_PROGRAM = [
    f"set -- grep -e ~/.cache -e hungr''y-crab; python \"$3/$5/{TAIL}\"",
    f"sh -c 'python $2/$4/{TAIL}' grep -e ~/.cach? -e hungr?-crab",
    f"echo grep -e x hungr''y-crab | xargs -I@ python ~/.cache/@/{TAIL}",
]

# Glob and brace characters taken literally: the word as written reaches the cache.
LITERAL_WILDCARDS = [
    f'python "$HOME/.cache/[/../hungry-crab/{REPO}/]/../setup.py"',
    f"python $HOME/.cache/[/../hungry-crab/{REPO}/]/../setup.py",
    f'python "$HOME/.cache/{{/../hungry-crab/,}}/../{TAIL}"',
    f"python $HOME/.cache/\\{{/../hungry-crab/,\\}}/../{TAIL}",
    f'python "$HOME/.cache/@(/../hungry-crab/{REPO}/)/../setup.py"',
]

# Variables, `HOME` included, the command itself changes.
CHANGED_VARIABLES = [
    f"HOME=$HOME/.cache; python ~/hungry-crab/{TAIL}",
    f"HOME=$HOME/.cache; cd; cd hungry-crab/{REPO}; python setup.py",
    f'x=a; printf -vx %s "$HOME/.cache"; python $x/hungry-crab/{TAIL}',
    f'printf -vx %s/.c%s/hungr%s "$HOME" ache y-crab; python $x/{TAIL}',
    f"printf -v 'a[0]' %s/.c%s/hungr%s \"$HOME\" ache y-crab; python $a/{TAIL}",
    f"x=; : ${{x:=$HOME/.cache}}; python $x/hungry-crab/{TAIL}",
    f"x=a; unset x; : ${{x=$HOME/.cache}}; python $x/hungry-crab/{TAIL}",
]

# Text bash evaluates a second time: arithmetic, a prompt, `${x@P}`, `eval`.
EVALUATED_AGAIN = [
    f"z='a[$(python ~/.cach?/hungr?-crab/{TAIL})]'; echo $((z))",
    f"z='a[$(python ~/.cache/hungr\"\"y-crab/{TAIL})]'; echo $((z))",
    f"PS4='$(python ~/.cach?/hungr?-crab/{TAIL}) '; set -x; true",
    f'w=\'$(python ~/.cache/hungr""y-crab/{TAIL})\'; echo "${{w@P}}"',
    f'x=\'[ -n "$(python ~/.cach?/hungr?-crab/{TAIL})" ]\'; eval "$x"',
]

# A shell command handed over in a variable or to a program the guard has no list entry for.
COMMAND_STRINGS = [
    f"GIT_SSH_COMMAND='python ~/.cach?/hungr?-crab/{TAIL}' git ls-remote ssh://example.invalid/x",
    (
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.sshCommand "
        f"GIT_CONFIG_VALUE_0='python ~/.cach?/hungr?-crab/{TAIL}' git ls-remote ssh://e.invalid/x"
    ),
    f"V='python ~/.cach?/hungr?-crab/{TAIL}' git --config-env=core.sshCommand=V ls-remote x",
    f"env -iS 'python ~/.cach?/hungr?-crab/{TAIL}'",
    f"tmux new -d 'python ~/.cach?/hungr?-crab/{TAIL}'",
    f"hyperfine --runs 1 'python ~/.cach?/hungr?-crab/{TAIL}'",
    f'forfiles /p . /m x /c "cmd /c python ~/.cach?/hungr?-crab/{TAIL}"',
    (
        'cmd //c "set c=.cache&& set x=hungr&& call python '
        f'%USERPROFILE%\\%c%\\%x%y-crab\\{BACKSLASH_REPO}\\setup.py"'
    ),
    f"echo 'python ~/.cach?/hungr?-crab/{TAIL}' | ssh localhost",
]

# A link to, or a copy of, the cache or a directory above it, made and used by one command.
LINKED_CACHE = [
    f"ln -s ~/.cache l && python l/hungry-crab/{TAIL}",
    f"cp -r ~/.cache c && python c/hungry-crab/{TAIL}",
    f"ln -s ~ h; python h/.cache/hungry-crab/{TAIL}",
]


@pytest.mark.parametrize(
    "command",
    LEXER_EDGES
    + FORCED_FALLBACK
    + NOT_THE_PROGRAM
    + LITERAL_WILDCARDS
    + CHANGED_VARIABLES
    + EVALUATED_AGAIN
    + COMMAND_STRINGS
    + LINKED_CACHE,
)
def test_a_path_the_shell_builds_is_judged_after_review(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    assert guard_reason(SPLIT_PATH, root=root, cwd=cwd) is not None  # judged in full
    reason = guard_reason(command, root=root, cwd=cwd)
    assert reason is not None
    assert "rule 3" in reason


def test_a_detour_through_another_home_spelling_still_names_the_parent(home: Path) -> None:
    """`C:/Users/me/../me/...` is the home too: spellings are compared once normalised."""
    root = home / ".cache" / "hungry-crab"
    parts = home.as_posix().split("/")
    detour = "/".join([*parts[:3], "..", *parts[2:]])
    command = f'x={detour}/.cache/hungry_crab; python "${{x/_/-}}/{TAIL}"'
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is not None


def _encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode()


def test_powershell_variables_and_encodings_the_first_fix_missed(home: Path) -> None:
    script = f"python $HOME\\.cache\\hungry-crab\\{BACKSLASH_REPO}\\setup.py"
    encoded = _encoded(script)
    spaced = " ".join(encoded[i : i + 8] for i in range(0, len(encoded), 8))
    odd = base64.b64encode((script + "\n#").encode("utf-16-le") + b"\x00").decode()
    tail = f"python $c\\hungry-crab\\{BACKSLASH_REPO}\\setup.py"
    for command in (
        f"$c = 'x'; Set-Variable c \"$HOME\\.cache\"; {tail}",
        f"$c = 'x'; Set-Variable -Name c -Value \"$HOME\\.cache\"; {tail}",
        f"$c = 'x'; $global:c = \"$HOME\\.cache\"; {tail}",
        f"$c = 'x'; ${{c}} = \"$HOME\\.cache\"; {tail}",
        f"$c = 'x'; $c =\"$HOME\\.cache\"; {tail}",
        f"$c = 'x'; [string]$c = \"$HOME\\.cache\"; {tail}",
        (
            f'New-PSDrive -Name Q -PSProvider FileSystem -Root "$HOME\\.cache"; '
            f"Set-Location Q:\\hungry-crab\\{BACKSLASH_REPO}; python setup.py"
        ),
        (
            f"New-Item -ItemType Junction -Path l -Target $HOME\\.cache; "
            f"python l\\hungry-crab\\{BACKSLASH_REPO}\\setup.py"
        ),
        f'pwsh -EncodedCommand "{spaced}"',
        f"pwsh -EncodedCommand {odd}",
        f"powershell /EncodedCommand {encoded}",
        "pwsh -EncodedCommand !!not-base64!!",
        (
            'cmd /c "set c=.cache&& set x=hungr&& call python '
            f'%USERPROFILE%\\%c%\\%x%y-crab\\{BACKSLASH_REPO}\\setup.py"'
        ),
        f"$env:GIT_SSH_COMMAND = 'python ~\\.cach?\\hungr?-crab\\{BACKSLASH_REPO}\\x'; git fetch",
    ):
        reason = _powershell(command, home)
        assert reason is not None, command
        assert "rule 3" in reason
    assert guard_reason(f'pwsh -EncodedCommand "{spaced}"', root=home / ".cache" / "hungry-crab")


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 path rules")
def test_win32_spellings_are_judged_after_the_resolution_budget(home: Path) -> None:
    """Win32 drops trailing dots and spaces from components; past the resolution budget the
    guard normalises the way Win32 does instead of asking the filesystem."""
    root = home / ".cache" / "hungry-crab"
    (root / REPO).mkdir(parents=True)
    cwd = home / "work" / "maw"
    burn = "cat " + " ".join(f"f{i}" for i in range(300)) + "; "
    for command in (
        f"python $HOME/.cache./hungr''y-crab./{TAIL}",
        burn + f"python $HOME/.cache./hungr''y-crab./{TAIL}",
        burn + f'python "$HOME/.cache /hungr"\'\'"y-crab /{TAIL}"',
        burn + f"python '{home.as_posix()}/.cache::$INDEX_ALLOCATION/hungry-crab/{TAIL}'",
        f"python //127.0.0.2/C$/{home.as_posix()[3:]}/.cach?/hungr?-crab/{TAIL}",
    ):
        assert guard_reason(command, root=root, cwd=cwd) is not None, command[-60:]
    ps = "Get-Content " + ",".join(f"f{i}" for i in range(300))
    command = f"{ps}; python $HOME\\.cache.\\hungr''y-crab.\\{BACKSLASH_REPO}\\setup.py"
    assert _powershell(command, home) is not None


# HungryCrab#392 again: ordinary commands the first fix refused, each next to a refused twin.
ISSUE_392_AGAIN = [
    (
        (
            "curl -s https://pypi.org/pypi/hungry-crab/json | jq -r '.releases|keys[]' "
            '| while read -r v; do echo "$v"; done'
        ),
        f'ls ~/.cache | while read -r v; do python "$v/{TAIL}"; done',
    ),
    (
        'uv tool list | while read -r n v; do [ "$n" = hungry-crab ] && echo $v; done',
        f'uv tool list | while read -r n v; do [ "$n" = x ] && python ~/.cach?/$n/{TAIL}; done',
    ),
    (
        'gh release list | while read -r tag rest; do echo "hungry-crab $tag"; done',
        f"echo 'python ~/.cach?/hungr?-crab/{TAIL}' | while read -r c; do eval $c; done",
    ),
    (
        "ssh build-host 'uv tool install hungry-crab && crab --version'",
        f"ssh localhost 'python ~/.cach?/hungr?-crab/{TAIL}'",
    ),
    ("ssh build-host 'ls ~/.cache'", f"ssh localhost 'cd ~/.cache && python hungr?-crab/{TAIL}'"),
    (
        'mapfile -t t < <(git tag); echo "hungry-crab ${#t[@]}"',
        f'mapfile -t t < <(ls ~/.cache); python "${{t[0]}}/{TAIL}"',
    ),
    (
        (
            "git ls-remote https://github.com/drevendev/HungryCrab "
            "| while read s r; do echo $r; done; pip index versions hungry-crab"
        ),
        f"pip download hungry-crab -d ~/.cache/hungr?-crab/x && python ~/.cache/hungr?-crab/{TAIL}",
    ),
]


@pytest.mark.parametrize(("ordinary", "twin"), ISSUE_392_AGAIN)
def test_a_package_name_or_a_url_is_no_path_hint(ordinary: str, twin: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    assert guard_reason(ordinary, root=root, cwd=cwd) is None
    assert guard_reason(twin, root=root, cwd=cwd) is not None


@pytest.mark.parametrize(
    ("ordinary", "twin"),
    [
        (
            f'uv tool install --force "{REQUIREMENT}"',
            f'uv tool install --force "$(ls -d .cache/hungry-crab)/{REPO}"',
        ),
        (
            'echo "hungry-crab version: $(uv run crab --version)"',
            f'python "$(ls -d .cach?)/hungry-crab/{TAIL}"',
        ),
        ('ls ~/.cache/uv; echo "exit=$?"', f"ls .cache/uv; python .cache/hungr?-crab/{TAIL}"),
    ],
)
def test_the_home_directory_as_cwd_does_not_refuse_every_construct(
    ordinary: str, twin: str, home: Path
) -> None:
    """Home holds the cache two levels down; a program run there names `.cache`, not it."""
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(ordinary, root=root, cwd=home) is None
    assert guard_reason(twin, root=root, cwd=home) is not None


@pytest.mark.parametrize(
    ("ordinary", "twin"),
    [
        (
            "uv tool list | ForEach-Object { if ($_ -match 'hungry-crab') { $_ } }",
            (
                "uv tool list | ForEach-Object { if ($_ -match 'x') { "
                f"python ~\\.cach?\\hungr?-crab\\{BACKSLASH_REPO}\\setup.py }} }}"
            ),
        ),
        (
            "(Invoke-RestMethod https://pypi.org/pypi/hungry-crab/json).info.version",
            f"& (Join-Path $HOME '.cache\\hungry-crab\\{BACKSLASH_REPO}\\setup.ps1')",
        ),
        (
            "Invoke-RestMethod https://pypi.org/pypi/hungry-crab/json | Select-Object -Expand info",
            f"Invoke-RestMethod https://x.invalid/a | Out-File ~\\.cache\\hungry-crab\\{REPO}\\x",
        ),
    ],
)
def test_powershell_package_names_and_urls_are_no_path_hint(
    ordinary: str, twin: str, home: Path
) -> None:
    assert _powershell(ordinary, home) is None
    assert _powershell(twin, home) is not None


@pytest.mark.parametrize(
    "command",
    [
        "x=$(case a in a) echo 1;; esac); echo $x",
        "cp README.md ~/ && ls ~",
        "cp -r src /tmp/src-copy && ls /tmp/src-copy",
        "ln -s ../shared lib && ls lib",
        "printf -v now '%(%s)T' -1; echo $now",
        "read -r a b <<< 'x y'; echo $a",
        "while read l; do echo $l; done < notes.txt; ls ~/.cache",
        "git -c color.ui=always log --oneline -3",
        "echo $$ $# $? $!",
    ],
)
def test_ordinary_commands_stay_allowed_after_review(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None


@pytest.mark.parametrize(
    "deep",
    [
        "/".join(["a"] * 16000),
        "\\\\?\\GLOBALROOT\\??\\C:\\x\\" + "\\".join(["a"] * 15000),
        "//?/C:/x/" + "/".join(["a"] * 15000),
    ],
    ids=["relative", "globalroot", "device"],
)
def test_a_path_of_many_components_decides_in_time(deep: str, home: Path) -> None:
    """Resolving one path on Windows takes time that grows with the square of its length: a
    16,000-component word took seven seconds. Such a path is normalised instead."""
    command = f"cat '{deep}'; python ~/.cache/hungr''y-crab/{TAIL}"
    reason, elapsed = _timed(command, home / ".cache" / "hungry-crab", home / "work" / "maw")
    assert reason is not None
    assert elapsed < IN_TIME


# --- the third review --------------------------------------------------------------------------

# The bare name, or another directory under `~/.cache`, beside text the command computes.
COMPUTED_FROM_A_NAME = [
    f'p=,.cache,hungry-crab; python "$HOME${{p//,//}}/{TAIL}"',
    f"p=,.cache; CDPATH=$HOME${{p//,//}}; cd hungry-crab; cd {REPO}; python setup.py",
    f'p=.cache,hungry-crab; cd; python "${{p//,//}}/{TAIL}"',
    f'python "$HOME$(echo ,.cache,hungry-crab | tr , /)/{TAIL}"',
    f'p=$(echo ,.cache,hungry-crab | tr , /); python "$HOME$p/{TAIL}"',
    "p=~/.cache/uv; cd ${p%/uv}; cd *; python setup.py",
    'cd "$(dirname ~/.cache/x)"; cd *; python setup.py',
    f'python "$HOME$(cat name.txt)/{TAIL}"',
    f"python ~/.cache/uv$(cat name.txt)/{TAIL}",
    # What a program prints about a sibling can be the parent, read back and used.
    f'dirname ~/.cache/uv | while read d; do cd "$d"; cd */{REPO}; python setup.py; done',
    (
        'dirname ~/.cache/uv | while read d; do ls "$d" | while read n; do '
        f'python "$d/$n/{TAIL}"; done; done'
    ),
    (
        "find ~/.cache/uv -maxdepth 0 -printf '%h\\n' | while read d; do cd \"$d\"; "
        'cd "$(ls | head -1)"; python setup.py; done'
    ),
    # The name written to a file and read back by the same command.
    f'echo ,.cache,hungry-crab > f; cd; cd "$(tr , / < f | cut -c2-)"; python {TAIL}',
    f"echo ,.cache,hungry-crab > f; cd; tr , / < f | {{ read p; cd ~$p; python {TAIL}; }}",
    f'echo .cache/hungry-crab > f; python "$HOME/$(cat f)/{TAIL}"',
    f'echo hungry-crab > f; python "$HOME/.cache/$(cat f)/{TAIL}"',
]

# Code held in a value: a shell, `eval`, `trap` or `mapfile -C` runs the value's text.
CODE = f"python ~/.cache/${{x/_/-}}/{TAIL}"
CODE_IN_A_VALUE = [
    f"x=hungry_crab; c='{CODE}'; eval \"$c\"",
    f"x=hungry_crab; c='{CODE}'; eval \"${{c:-}}\"",
    f"x=hungry_crab; c='{CODE}'; bash -c \"$c\"",
    f"x=hungry_crab; c='{CODE}'; trap \"$c\" EXIT",
    f"x=hungry_crab; c='{CODE}'; mapfile -C \"$c\" -c 1 <<< a",
    f"x=hungry_crab; c='{CODE}'; echo \"$c\" | bash",
    f"x=hungry_crab; c='{CODE}'; source <(echo \"$c\")",
    f"x=hungry_crab; c='{CODE}'; bash <<< \"$c\"",
    f"x=hungry_crab; printf '%s\\n' '{CODE}' | bash",
    f"x=hungry_crab; a='python ~/.ca'; b='che/${{x/_/-}}/{TAIL}'; eval \"$a$b\"",
]

# Text bash, or a program that runs it, reads where this guard's lexer cannot.
UNREADABLE_SCRIPTS = [
    f"echo $(( ${{ python ~/.cach?/hungr?-crab/{TAIL}; }} ))",
    f"x=hungry_crab; echo $(( ${{ python ~/.cache/${{x/_/-}}/{TAIL}; }} ))",
    f"cat <<EOF\r\nhello\nEOF\r\npython ~/.cach?/hungr?-crab/{TAIL}\n",
    f"cat <<EOF\r\nhello\nEOF\r\npython ~/.cache/hungr''y-crab/{TAIL}\n",
    f"GIT_SSH_COMMAND='echo ${{ python ~/.cach?/hungr?-crab/{TAIL}; }}' git ls-remote ssh://e.x/y",
    f"GIT_SSH_COMMAND='python ~/.cach?/hungr?-crab/{TAIL} \\' git ls-remote ssh://e.x/y",
    f"tmux new -d 'python ~/.cach?/hungr?-crab/{TAIL} \\'",
    f"ssh localhost 'python ~/.cach?/hungr?-crab/{TAIL} \\'",
]

# A copy, an archive or a move of the cache's parent, even in a call of its own; a copy of
# home used where its path still spells the cache.
COPIES = [
    "cp -r ~/.cache x",
    "rsync -a ~/.cache/ backup/",
    "tar czf c.tgz ~/.cache",
    "zip -r c.zip ~/.cache",
    "python -m zipfile -c y.zip ~/.cache",
    "python3 -m tarfile -c y.tar ~/.cache",
    "mv ~/.cache ~/old-cache",
    f"ln -s ~ h; python h/.cache/./hungry-crab/{TAIL}",
    f"cp -r ~ /tmp/h && python /tmp/h/.cache/./hungry-crab/{TAIL}",
    f"python /tmp/h/.cache//hungry-crab/{TAIL}",
]


@pytest.mark.parametrize(
    "command", COMPUTED_FROM_A_NAME + CODE_IN_A_VALUE + UNREADABLE_SCRIPTS + COPIES
)
def test_a_path_the_shell_builds_is_judged_after_the_third_review(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert reason is not None
    assert "rule 3" in reason


def test_a_typographic_dash_starts_an_encoded_command_from_bash(home: Path) -> None:
    encoded = _encoded(f"python $HOME\\.cache\\hungry-crab\\{BACKSLASH_REPO}\\setup.py")
    root = home / ".cache" / "hungry-crab"
    for dash in ("\u2013EncodedCommand", "\u2014ec", "\u2015e"):
        command = f"pwsh {dash} {encoded}"
        assert guard_reason(command, root=root, cwd=home / "work" / "maw") is not None, dash


def test_powershell_variables_a_cmdlet_fills_are_unknown(home: Path) -> None:
    tail = f"python $c\\hungry-crab\\{BACKSLASH_REPO}\\setup.py"
    for command in (
        f"$c = 'x'; Set-Item Variable:c \"$HOME\\.cache\"; {tail}",
        f"$c = 'x'; New-Item Variable:c -Value \"$HOME\\.cache\" -Force; {tail}",
        f"$c = 'x'; Write-Output \"$HOME\\.cache\" -OutVariable c; {tail}",
        f"$c = 'x'; Write-Output \"$HOME\\.cache\" -ov:c; {tail}",
        f"$c = 'x'; \"$HOME\\.cache\" | Tee-Object -Variable c; {tail}",
        f"$c = 'x'; Get-Item \"$HOME\\.cache\" -PipelineVariable c | Out-Null; {tail}",
        (
            "$p = '+.cache+hungry-crab'; "
            f"python \"$HOME$($p.Replace('+','\\'))\\{BACKSLASH_REPO}\\setup.py\""
        ),
        (
            "$x = 'hungry_crab'; "
            f"$c = \"python ~\\.cache\\$($x.Replace('_','-'))\\{BACKSLASH_REPO}\\setup.py\"; iex $c"
        ),
        "Compress-Archive -Path ~\\.cache -DestinationPath y.zip",
        "Compress-Archive -Path:~\\.cache -DestinationPath y.zip",
        "Copy-Item -Recurse ~\\.cache x",
        (
            "Set-Location (Get-Item ~\\.cache\\uv).Parent; Set-Location (Get-ChildItem)[3]; "
            f"python {BACKSLASH_REPO}\\setup.py"
        ),
        (
            "Get-Item ~\\.cache\\uv | % Parent | % { Set-Location $_; "
            f"Set-Location (Get-ChildItem)[3]; python {BACKSLASH_REPO}\\setup.py }}"
        ),
    ):
        reason = _powershell(command, home)
        assert reason is not None, command
        assert "rule 3" in reason


def _documents_and_settings(home: Path) -> str | None:
    """The home directory through Windows' `C:\\Documents and Settings` junction, if it has one."""
    legacy = Path("C:/Documents and Settings")
    parts = home.as_posix().split("/")
    if parts[:2] != ["C:", "Users"] or not legacy.exists():
        return None
    return "/".join(["C:/Documents and Settings", *parts[2:]])


@pytest.mark.skipif(sys.platform != "win32", reason="the junction is Windows'")
def test_a_path_compared_by_its_spelling_alone_is_not_let_through_a_junction(
    home: Path,
) -> None:
    """Past 64 components, or past the resolution budget, a path is normalised without the
    filesystem, so a junction is not followed; one that hints at the cache reaches it."""
    junction = _documents_and_settings(home)
    if junction is None:
        pytest.skip("no `Documents and Settings` junction above this home")
    root = home / ".cache" / "hungry-crab"
    (root / REPO).mkdir(parents=True)
    cwd = home / "work" / "maw"
    dots = "/".join(["."] * 70)
    pairs = "/".join(["x/.."] * 40)
    burn = "cat " + " ".join(f"f{i}" for i in range(300)) + "; "
    for command in (
        f'python "{junction}/.cache/./hungry-crab/{TAIL}"',
        f'python "{junction}/.cache/{dots}/hungry-crab/{TAIL}"',
        f'python "{junction}/.cache/{dots}/"hungr?-crab/{TAIL}',
        f'python "{junction}/.cache/{pairs}/hungry-crab/{TAIL}"',
        burn + f'python "{junction}/.cache/./hungry-crab/{TAIL}"',
    ):
        assert guard_reason(command, root=root, cwd=cwd) is not None, command[-80:]
    windows = f"{junction}\\.cache\\{pairs}\\hungry-crab\\{BACKSLASH_REPO}\\setup.py"
    assert _powershell(f'python "{windows}"', home) is not None


@pytest.mark.parametrize(
    ("ordinary", "twin"),
    [
        (
            "pip install -e . && python -m pytest -q",
            f"pip install -e . && python .cache/hungr?-crab/{TAIL}",
        ),
        ("uv pip install -e . && uv run pytest", f"uv pip install -e .cache/hungr?-crab/{REPO}"),
        ("npm install . && npm test", f"npm install . && node .cache/hungr?-crab/{REPO}/x.js"),
        ("cp -r . ../backup && ls ../backup", "cp -r .cache ../backup && ls ../backup"),
        ("zip -qr ../site.zip . && ls -la ../site.zip", "zip -qr ../site.zip .cache"),
        (
            (
                "git clone https://github.com/drevendev/HungryCrab hc && cd hc && "
                "pip install -e . && crab --version"
            ),
            f"git clone https://x.invalid/hc hc && cd .cache/hungr?-crab/{REPO} && pip install .",
        ),
        (
            'ls ~/.cache/uv | while read -r f; do echo "$f"; done',
            f'ls ~/.cache | while read -r f; do python "$f/{TAIL}"; done',
        ),
        (
            'for d in ~/.cache/uv ~/.cache/pip; do du -sh "$d"; done; echo $?',
            'for d in ~/.cache/uv ~/.cache/hungry-crab; do du -sh "$d"; done; echo $?',
        ),
    ],
)
def test_ordinary_commands_from_home_stay_allowed_after_the_third_review(
    ordinary: str, twin: str, home: Path
) -> None:
    """`install` is a subcommand of pip and npm; home holds much besides the cache, so copying
    it is no copy of the cache; a listing of `~/.cache/uv` cannot hold the cache's name."""
    root = home / ".cache" / "hungry-crab"
    for cwd in (home, home / "work" / "maw"):
        assert guard_reason(ordinary, root=root, cwd=cwd) is None, (ordinary, cwd)
    assert guard_reason(twin, root=root, cwd=home) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m \"fix: don't break hungry-crab's guard\"",
        'git commit -m "fix(cache): clear the .cache directory"',
        "c='make test'; eval \"$c\"",
        'gh release list | while read -r tag rest; do echo "hungry-crab $tag"; done',
        'gh release create v1 --title "hungry-crab v1" --notes "$(cat notes.md)"',
        "cp -r ~/.cache/uv backup && ls backup",
        "install -m 755 build/crab ~/.local/bin/crab && crab --version",
        "make install && crab --version",
        "git -C ~/.cache/hungry-crab -C github/acme/widget/repo log --oneline -1",
    ],
)
def test_ordinary_commands_stay_allowed_after_the_third_review(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    (root / REPO / ".git").mkdir(parents=True)
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None


@pytest.mark.parametrize(
    "command",
    [
        "Get-ChildItem src -OutVariable files; $files.Count",
        "Get-ChildItem ~\\.cache\\hungry-crab -ErrorVariable e",
        "Compress-Archive -Path src -DestinationPath site.zip",
        "uv tool install hungry-crab; (Get-Command crab).Source",
    ],
)
def test_powershell_commands_stay_allowed_after_the_third_review(command: str, home: Path) -> None:
    assert _powershell(command, home) is None


def test_many_git_directories_decide_in_time(home: Path) -> None:
    """Each `git -C` is resolved after the budget ends: past a few the command is refused."""
    clone = home / ".cache" / "hungry-crab" / REPO
    (clone / ".git").mkdir(parents=True)
    deep = "/".join(["a"] * 60)
    moves = " ".join(f"-C C:/n{i}/{deep}" for i in range(1500))
    command = f"git {moves} -C {clone.as_posix()} log --oneline -1"
    reason, elapsed = _timed(command, home / ".cache" / "hungry-crab", home / "work" / "maw")
    assert reason is not None
    assert elapsed < IN_TIME


@pytest.mark.parametrize(
    ("tool", "command"),
    [
        ("Bash", f'p=,.cache,hungry-crab; python "$HOME${{p//,//}}/{TAIL}"'),
        ("Bash", f"cat <<EOF\r\nhello\nEOF\r\npython ~/.cache/hungr''y-crab/{TAIL}\n"),
        ("Bash", "cp -r ~/.cache x"),
        (
            "PowerShell",
            (
                f"$c = 'x'; Set-Item Variable:c \"$HOME\\.cache\"; "
                f"python $c\\hungry-crab\\{BACKSLASH_REPO}\\setup.py"
            ),
        ),
    ],
    ids=["computed", "heredoc", "copy", "powershell"],
)
def test_the_third_review_cases_are_refused_through_the_launcher(
    tool: str, command: str, home: Path
) -> None:
    event = {"tool_name": tool, "tool_input": {"command": command}, "cwd": str(home / "work")}
    env = {key: value for key, value in os.environ.items() if key != "CRAB_CACHE_DIR"}
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "hooks" / "guard.py"), "prey"],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 2, result.stderr


# --- the fourth review -------------------------------------------------------------------------

# Git Bash discards carriage returns before it reads a command; bash elsewhere keeps them.
CARRIAGE_RETURNS = [
    f"cat <<EOF\nhello\nEOF\r\npython ~/.cach?/hungr?-crab/{TAIL}\n",
    f"cat <<EOF\nhello\nEOF\r\npython ~/.cache/hungr''y-crab/{TAIL}\n",
    f"cat <<EOF\r\nhello\r\nEOF\r\npython ~/.cach?/hungr?-crab/{TAIL}\r\n",
    f"python ~/.cache/hungr\ry-crab/{TAIL}",
    f"cd ~/.cac\rhe/hungry-crab/{REPO} && python setup.py",
    f"cat x\r# ; python ~/.cach?/hungr?-crab/{TAIL}",
    f"cat x\r# ; python ~/.cache/hungr\ry-crab/{TAIL}",
    f"bash -c $'python ~/.cache/hungr\\ry-crab/{TAIL}'",
]

# A search that walks through the cache, feeding something that runs what it finds.
SEARCHES_THAT_RUN = [
    "find ~ -name setup.py -path '*widget*' | while read f; do python \"$f\"; done",
    "find ~ -name setup.py | head -1 | xargs python",
    "python \"$(find ~ -name setup.py -path '*acme*' | head -1)\"",
    'cd "$(find ~ -type d -name repo | head -1)" && python setup.py',
    'find ~ -type d -name repo | while read d; do (cd "$d" && python setup.py); done',
    "for f in $(find ~ -name setup.py); do python $f; done",
    'mapfile -t a < <(find ~ -name setup.py); python "${a[0]}"',
    "find ~ -name setup.py | python -c 'import runpy; runpy.run_path(input())'",
    "find / -name setup.py -path '*widget*' | xargs python",
    "fd setup.py ~ -x python",
    "fd -p widget/repo/setup.py ~ --exec python {}",
    "rg --files ~ -g setup.py | grep widget | xargs -n1 python",
    "grep -rl setuptools ~ | grep widget | xargs -n1 python",
    "du -a ~ | grep setup.py | cut -f2 | xargs python",
    "locate setup.py | grep widget | xargs -n1 python",
    "find ~ -name '*.log' | xargs rm",
    "fd -e py setup --search-path ~ | xargs python",
    'find "$(echo ~)" -name setup.py | xargs python',
]


@pytest.mark.parametrize("command", CARRIAGE_RETURNS + SEARCHES_THAT_RUN)
def test_a_path_the_shell_builds_is_judged_after_the_fourth_review(
    command: str, home: Path
) -> None:
    root = home / ".cache" / "hungry-crab"
    for cwd in (home / "work" / "maw", home):
        reason = guard_reason(command, root=root, cwd=cwd)
        assert reason is not None, cwd
        assert "rule 3" in reason


@pytest.mark.parametrize(
    "command",
    [
        'find . -name setup.py | while read f; do python "$f"; done',
        "rg -t py setup | xargs python",
        "rg -l -t py setuptools | xargs -n1 python",
        "tree -L 3 -f -i | grep setup.py | xargs python",
        "fd -e py setup | xargs python",
        "grep -rl -e setuptools | xargs python",
    ],
)
def test_a_search_from_home_as_the_working_directory_runs_nothing(command: str, home: Path) -> None:
    """With no start named, a search starts where the command runs: past option values."""
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home) is not None
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None


def test_powershell_searches_that_run_what_they_find(home: Path) -> None:
    for command in (
        "Get-ChildItem ~ -Recurse -Filter setup.py | ForEach-Object { python $_.FullName }",
        (
            "Get-ChildItem ~ -Recurse -Filter setup.py | Select-Object -First 1 | "
            "ForEach-Object { & python $_ }"
        ),
        "Get-ChildItem ~ -Recurse -Filter setup.py | Invoke-Item",
        "python (Get-ChildItem ~ -Recurse -Filter setup.py)[0].FullName",
        "gci -r -Path:~ -Filter setup.py | % { python $_ }",
        "Get-ChildItem -LiteralPath ~ -Recurse -Filter setup.py | % { python $_ }",
        "gci ~ setup.py -Recurse | % { python $_ }",
        "gci ~ -r -Directory -Filter repo | Set-Location; python setup.py",
    ):
        reason = _powershell(command, home)
        assert reason is not None, command
        assert "rule 3" in reason


@pytest.mark.parametrize(
    ("shell", "command"),
    [
        ("bash", "find ~ -name '*.log' | xargs wc -l"),
        ("bash", "find ~ -name x | head"),
        ("bash", "find ~ -maxdepth 1 -name .bashrc | xargs cat"),
        ("bash", 'find ~ -name "*.log" | while read f; do echo "$f"; done'),
        ("bash", "rg --files | wc -l"),
        ("bash", "rg -n TODO ~ | head"),
        ("bash", "grep -rn TODO ~ | sort | uniq -c"),
        ("bash", "grep -rn x src | cd .."),
        ("bash", "grep -rn x src | xargs echo"),
        ("bash", "du -a ~ | sort -n | tail"),
        ("bash", "tree -L 2 ~ | head"),
        ("bash", "locate setup.py | head"),
        ("bash", "fd -e py | xargs wc -l"),
        ("bash", "git status\r\n"),
        ("ps", "Get-ChildItem -Recurse -Filter *.py | Measure-Object"),
        ("ps", "Get-ChildItem ~ -Recurse -Filter *.log | Select-Object FullName"),
    ],
)
def test_searches_that_feed_readers_stay_allowed(shell: str, command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    for cwd in (home / "work" / "maw", home):
        reason = guard_reason(command, root=root, cwd=cwd, shell=shell_name(shell))
        assert reason is None, cwd


def test_a_search_of_the_maw_may_run_what_it_finds(home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    for command in (
        "find . -name '*.py' | xargs python -m py_compile",
        "fd -e py -x ruff check",
        'find . -name "*.sh" | while read f; do bash "$f"; done',
    ):
        assert guard_reason(command, root=root, cwd=cwd) is None, command


def test_a_copy_of_home_reached_by_separate_directory_changes(home: Path, tmp_path: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    copy = (tmp_path / "h").as_posix()
    for command in (
        f"cp -r ~ {copy} && cd {copy}/.cache && cd hungry-crab/{REPO} && python setup.py",
        f"cp -r ~ {copy} && cd {copy}/.cache && python hungry-crab/{TAIL}",
        f"cp -r ~ {copy} && python {copy}/.cache/hungry-crab/{TAIL}",
    ):
        assert guard_reason(command, root=root, cwd=cwd) is not None, command
    # A copy an earlier call made, used from where that call left the working directory.
    left = tmp_path / "h" / ".cache"
    (left / "hungry-crab" / REPO).mkdir(parents=True)
    assert guard_reason("make", root=root, cwd=left / "hungry-crab") is not None
    assert guard_reason(f"python hungry-crab/{TAIL}", root=root, cwd=left) is not None
    # Text, not a path: the words of a commit message.
    message = 'git commit -m "docs: mention .cache/hungry-crab here"'
    assert guard_reason(message, root=root, cwd=cwd) is None


@pytest.mark.parametrize(
    ("tool", "command"),
    [
        ("Bash", f"cat <<EOF\nhello\nEOF\r\npython ~/.cach?/hungr?-crab/{TAIL}\n"),
        ("Bash", f"python ~/.cache/hungr\ry-crab/{TAIL}"),
        ("Bash", "find ~ -name setup.py | head -1 | xargs python"),
        ("Bash", "fd setup.py ~ -x python"),
        (
            "PowerShell",
            "Get-ChildItem ~ -Recurse -Filter setup.py | ForEach-Object { python $_.FullName }",
        ),
        ("Bash", f"cp -r ~ /tmp/h && cd /tmp/h/.cache && cd hungry-crab/{REPO} && python setup.py"),
    ],
    ids=["heredoc", "name", "xargs", "fd", "powershell", "copy"],
)
def test_the_fourth_review_cases_are_refused_through_the_launcher(
    tool: str, command: str, home: Path
) -> None:
    event = {"tool_name": tool, "tool_input": {"command": command}, "cwd": str(home / "work")}
    env = {key: value for key, value in os.environ.items() if key != "CRAB_CACHE_DIR"}
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "hooks" / "guard.py"), "prey"],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 2, result.stderr


# --- the fifth review ---------------------------------------------------------------------------

SPLIT = f"make -C ~ -f .cache/hungry-crab/{REPO}/Makefile"

# A directory option, or a word naming home or the cache's parent, moves a command's relative
# words: they are read from there. Each with a construct 0.6.1 refused beside a hint, too.
DIRECTORY_OPTIONS = [
    SPLIT,
    f"{SPLIT} -j$(nproc)",
    f'{SPLIT}; echo "exit=$?"',
    f"{SPLIT} -j`nproc`",
    f"{SPLIT} {{all,test}}",
    f"{SPLIT} && ls | xargs wc -l",
    f"{SPLIT} TARGET=${{T:-all}}",
    f"{SPLIT} BUILD_ID=$$",
    f"mapfile -t xs < list.txt; {SPLIT}",
    f"make -C ~/.cache -f hungry-crab/{REPO}/Makefile",
    f"make --directory=$HOME -f .cache/hungry-crab/{REPO}/Makefile",
    f"env -C ~ make -C .cache/hungry-crab/{REPO}",
    f"env -C ~ make -C .cache/hungry-crab/{REPO} -j$(nproc)",
    f"env --chdir=$HOME python .cache/hungry-crab/{TAIL}",
    f"uv run --directory ~ python .cache/hungry-crab/{TAIL}",
    f"uv run --directory ~ python .cache/hungry-crab/{TAIL}; echo $?",
    f"uv --directory ~/.cache run python hungry-crab/{TAIL}",
    f"npm --prefix ~/.cache run build --prefix hungry-crab/{REPO}",
    f"git -C ~/.cache -C hungry-crab/{REPO} commit -m x",
    f"git -C ~ -C .cache/hungry-crab/{REPO} config core.fsmonitor 'sh install.sh'",
    f"git -C ~ -C .cache/hungry-crab/{REPO} config diff.external ./install.sh",
    f"pytest --rootdir ~ .cache/hungry-crab/{REPO}",
    "tar -C ~ -czf /tmp/x.tgz .cache/hungry-crab",
    "tar -C ~/.cache -czf /tmp/x.tgz hungry-crab",
    "tar -C ~ -czf /tmp/crab-$(date +%s).tgz .cache/hungry-crab",
    f"go -C ~/.cache run ./hungry-crab/{REPO}",
    f"wsl --cd ~/.cache make -C hungry-crab/{REPO}",
    f"docker compose --project-directory ~/.cache -f hungry-crab/{REPO}/compose.yml up",
]

# A container that gets the cache, or the directory that holds it, mounted to run in.
CONTAINER_MOUNTS = [
    f"docker run --rm -v ~/.cache:/c -w /c/hungry-crab/{REPO} node:20 npm test",
    f"docker run --rm -u $(id -u):$(id -g) -v ~/.cache:/c -w /c/hungry-crab/{REPO} node npm test",
    "docker run --rm -v ~/.cache:/c alpine ls /c",
    "docker run --rm -v ~/.cache:/c:ro alpine ls /c",
    "docker run --rm --mount type=bind,src=$HOME/.cache,dst=/c alpine ls /c",
    f"podman run --rm -v $HOME/.cache:/c --workdir=/c/hungry-crab/{REPO} node:20 npm install",
    "nerdctl run --rm --volume=$HOME/.cache/hungry-crab:/src alpine ls /src",
    "docker compose run -v ~/.cache:/c app ls /c",
]

# Searches the guard read wrongly or whose output reached a runner it did not see.
MORE_SEARCHES = [
    "rg --pre python -g setup.py -l '' ~",
    "rg --pre=python -g setup.py -l '' ~",
    "ag -g setup.py ~ | xargs python",
    "ack -g setup.py ~ | xargs python",
    "find -O3 ~ -name setup.py | xargs python",
    "find -D tree ~ -name setup.py | xargs python",
    'find ~ -name setup.py > /tmp/l; while read f; do python "$f"; done < /tmp/l',
    'find ~ -name setup.py > /tmp/l && read -r f < /tmp/l && python "$f"',
    'find ~ -name setup.py | head -1 > /tmp/l; read f < /tmp/l; python "$f"',
    "find ~ -name setup.py > /tmp/l; python $(< /tmp/l)",
    "find ~ -name setup.py -fprint /tmp/l; xargs -a /tmp/l python",
    "find ~ -name setup.py -printf 'python %p\\n' > /tmp/r.sh; bash /tmp/r.sh",
    "find ~ -name setup.py | xargs -I{} sh -c 'python {}'",
    "find ~ -name setup.py | tee >(xargs python)",
    "find ~ -name setup.py | awk '{system(\"python \" $0)}'",
    "find ~ -name setup.py | sed 's/^/python /e'",
    "grep -rl --include=setup.py setup ~ | xargs -n1 python",
    "tree -fi ~ | grep 'widget/repo/setup.py$' | xargs python",
    'forfiles /p ~ /s /m setup.py /c "cmd /c python @path"',
    'cmd //c "for /r %USERPROFILE% %f in (setup.py) do @python %f"',
    "where //r ~ setup.py | xargs python",
    # A copy or a mount of home searched, entered or listed: it is home again.
    "cp -r ~ /tmp/h && find /tmp/h -name setup.py | xargs python",
    "cp -r ~ /tmp/h && find /tmp/h -name setup.py -exec python {} \\;",
    "rsync -a ~/ /tmp/h/ && fd setup.py /tmp/h -x python",
    "docker run --rm -v ~:/h alpine sh -c 'find /h -name setup.py -exec sh {} \\;'",
    "docker run --rm -v ~:/h -w /h node find . -name setup.py -exec python {} ;",
    f"ln -s ~ h && make -C h/.cache/./hungry-crab/{REPO}",
    f"cp -r ~ /tmp/h && cd /tmp/h/.cache && cd hungry-crab/{REPO} && python setup.py",
    f"cp -r ~ /tmp/h && cd /tmp/h/.c*/h*/{REPO} && python setup.py",
]


@pytest.mark.parametrize("command", DIRECTORY_OPTIONS + CONTAINER_MOUNTS + MORE_SEARCHES)
def test_a_path_the_shell_builds_is_judged_after_the_fifth_review(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert reason is not None
    assert "rule 3" in reason


def test_a_directory_option_moves_relative_words_under_a_custom_cache(tmp_path: Path) -> None:
    parent = tmp_path / "custom"
    root = parent / "prey"
    (root / REPO).mkdir(parents=True)
    cwd = tmp_path / "maw"
    cwd.mkdir()
    for command in (
        f"make -C {parent.as_posix()} -f prey/{REPO}/Makefile",
        f"make -C {parent.as_posix()} -f prey/{REPO}/Makefile -j$(nproc)",
        f"docker run --rm -v {parent.as_posix()}:/c -w /c/prey/{REPO} node:20 npm test",
        f"tar -C {parent.as_posix()} -czf /tmp/x.tgz prey",
    ):
        assert guard_reason(command, root=root, cwd=cwd) is not None, command


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash's drive spellings are Windows'")
def test_a_mount_spelled_the_way_git_bash_leaves_it(home: Path) -> None:
    """`//c/Users/...` is what Git Bash passes unconverted, and Docker reads it as `C:/...`."""
    posix = home.as_posix()
    doubled = f"//{posix[0].lower()}{posix[2:]}"
    single = f"/{posix[0].lower()}{posix[2:]}"
    root = home / ".cache" / "hungry-crab"
    for spelling in (doubled, single, posix):
        command = f"docker run --rm -v {spelling}/.cache:/c alpine ls /c"
        assert guard_reason(command, root=root, cwd=home / "work" / "maw") is not None, spelling


def test_powershell_working_directories_pipelines_and_mounts(home: Path) -> None:
    backslash = BACKSLASH_REPO
    for command in (
        (
            f"Start-Process -FilePath python -ArgumentList '.cache\\hungry-crab\\{backslash}"
            "\\setup.py' -WorkingDirectory $HOME"
        ),
        f"Start-Process make -ArgumentList '-C','hungry-crab/{REPO}' -WorkingDirectory ~\\.cache",
        f"Start-Job -WorkingDirectory $HOME {{ make -C .cache/hungry-crab/{REPO} }}",
        f"make -C $HOME -f .cache/hungry-crab/{REPO}/Makefile",
        f"Get-ChildItem ~\\.cache -Filter hungry-crab | Push-Location; make -C {backslash}",
        (
            "Get-ChildItem ~\\.cache -Directory | Where-Object Name -eq hungry-crab | "
            f"Set-Location; Set-Location {backslash}; make"
        ),
        f"docker run --rm -v ${{HOME}}\\.cache:/c -w /c/hungry-crab/{REPO} node:20 npm test",
        f'docker run --rm -v "$env:USERPROFILE\\.cache:/c" -w /c/hungry-crab/{REPO} node npm test',
        (
            "$f = Get-ChildItem ~ -Recurse -Filter setup.py | Select-Object -First 1; "
            "python $f.FullName"
        ),
        "$f = Get-ChildItem ~ -Recurse -Filter setup.py; python $f[0].FullName",
        "Get-ChildItem ~ -Depth 8 -Filter setup.py | ForEach-Object { python $_.FullName }",
        "where.exe /r $HOME setup.py | ForEach-Object { python $_ }",
        (
            "[IO.Directory]::EnumerateFiles($HOME, 'setup.py', 'AllDirectories') | "
            "ForEach-Object { python $_ }"
        ),
        'cmd /c "for /r %USERPROFILE% %f in (setup.py) do @python %f"',
        (
            "Get-ChildItem ~ -Recurse -Filter setup.py | Out-File l.txt; "
            "Get-Content l.txt | ForEach-Object { python $_ }"
        ),
        (
            "Get-ChildItem ~ -Recurse -Filter setup.py | Select-Object -ExpandProperty FullName "
            "| Set-Content l.txt; foreach ($f in Get-Content l.txt) { python $f }"
        ),
        (
            "Copy-Item -Recurse ~ C:\\tmp\\h; Get-ChildItem C:\\tmp\\h -Recurse -Filter setup.py "
            "| % { python $_ }"
        ),
    ):
        reason = _powershell(command, home)
        assert reason is not None, command
        assert "rule 3" in reason


def _clone(home: Path) -> Path:
    clone = home / ".cache" / "hungry-crab" / REPO
    (clone / ".git").mkdir(parents=True)
    (clone / "README.md").write_text("prey\n", encoding="utf-8")
    return clone


@pytest.mark.parametrize(
    "config",
    [
        "[core]\n\tfsmonitor = sh install.sh\n",
        "[core]\n\thooksPath = bin\n",
        "[core]\n\tpager = sh install.sh\n",
        '[diff "x"]\n\ttextconv = sh install.sh\n',
        "[diff.x]\n\tcommand = sh install.sh\n",
        "[diff]\n\texternal = ./install.sh\n",
        '[merge "x"]\n\tdriver = sh install.sh\n',
        '[filter "lfs"]\n\tsmudge = sh install.sh\n',
        "[alias]\n\tst = !sh install.sh\n",
        "[include]\n\tpath = ../evil.cfg\n",
        '[includeIf "gitdir:~/"]\n\tpath = evil.cfg\n',
        "[credential]\n\thelper = !sh install.sh\n",
        "[gpg]\n\tprogram = sh\n",
        "[pager]\n\tlog = sh install.sh\n",
        "[CORE] FSMONITOR = sh install.sh\n",
        "[core]\n\tfsmonitor\n",
        "[core\n",
        "#" * (70 * 1024),
        # Read the way git reads them (`git config -f FILE --get core.fsmonitor` finds each).
        "﻿[core]\n\tfsmonitor = sh install.sh\n",
        "[core]\r\n\tfsmonitor = sh install.sh\r\n",
        "[core]fsmonitor=sh install.sh\n",
        "[user]\n\tname = x\\\\\n[core]\n\tfsmonitor = sh install.sh\n",
        "[user]\n\tname = x ; note \\\n[core]\n\tfsmonitor = sh install.sh\n",
        "[core]\n\txnote = a\x1c[user]\n\tfsmonitor = sh install.sh\n",
        "[core]\n\txnote = a\u2028[user]\n\tfsmonitor = sh install.sh\n",
        "[core]\n\txnote = a\u0085[user]\n\tfsmonitor = sh install.sh\n",
        "[core]\n\tfsmonitor;note\n",
    ],
    ids=lambda config: config[:24],
)
def test_a_clone_whose_git_config_runs_a_program_is_no_read(config: str, home: Path) -> None:
    """A clone's `.git/config` rewritten (by a file tool, or an earlier command) to run a
    program turns `git -C <clone> status` into running it: the config is read first."""
    clone = _clone(home)
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    command = f"git -C {clone.as_posix()} status --short"
    assert guard_reason(command, root=root, cwd=cwd) is None
    (clone / ".git" / "config").write_text(config, encoding="utf-8")
    assert guard_reason(command, root=root, cwd=cwd) is not None


def test_a_clone_whose_git_dir_points_elsewhere_is_no_read(home: Path) -> None:
    """`commondir` makes git read the configuration of another directory, one the prey may
    have committed; it is refused rather than followed."""
    clone = _clone(home)
    (clone / ".git" / "commondir").write_text("../tools.git\n", encoding="utf-8")
    command = f"git -C {clone.as_posix()} status --short"
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is not None


def test_a_clone_with_the_config_catch_writes_stays_readable(home: Path) -> None:
    clone = _clone(home)
    (clone / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = false\n"
        '[remote "origin"]\n\turl = https://github.com/acme/widget\n'
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
        '[branch "main"]\n\tremote = origin\n\tmerge = refs/heads/main\n',
        encoding="utf-8",
    )
    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    spelled = clone.as_posix()
    for command in (
        f"git -C {spelled} log --oneline -5",
        f"git -C {spelled} log --oneline | head -20",
        f"git -C {spelled} log --format='%h %s' | grep fix | cut -c1-40 | tail -n 5 | wc -l",
    ):
        assert guard_reason(command, root=root, cwd=cwd) is None, command
    # `sort` and `uniq` write files and start programs, and GNU sort reads abbreviations and
    # clusters; neither is a filter a read may be piped into.
    for command in (
        f"git -C {spelled} log | sort",
        f"git -C {spelled} log | uniq -c",
        f"git -C {spelled} log | sort -S1K --co=sh",
        f"git -C {spelled} log | sort -S1K --compress-prog=sh",
        f"git -C {spelled} log | sort -S1K --compress-program=sh",
        f"git -C {spelled} log | sort -uo {spelled}/.git/config",
        f"git -C {spelled} log | sort -mo {spelled}/Makefile",
        f"git -C {spelled} log | sort -ro {spelled}/Makefile",
        f"git -C {spelled} log | sort -o {spelled}/Makefile",
        f"git -C {spelled} log | sort --o={spelled}/Makefile",
        f"git -C {spelled} log | sort --out=.git/hooks/pre-commit",
        f"git -C {spelled} log | sort --temp={spelled}",
        f"git -C {spelled} log | sort --temporary-d={spelled}",
        f"git -C {spelled} log | uniq - {spelled}/Makefile",
        f"git -C {spelled} log | python -c 'import sys'",
        f"git -C {spelled} log > {spelled}/x",
    ):
        assert guard_reason(command, root=root, cwd=cwd) is not None, command
    # PowerShell keeps 0.6.1's read cmdlets: Windows PowerShell 5.1 splits a quoted argument
    # with `\"` in it into two native arguments, so a `--output` reaches git unseen.
    for command in (
        f"git -C {spelled} log --oneline -5",
        f"git -C {spelled} log -1 '--format=%B \\' ' --output=Makefile'",
        f"git -C {spelled} grep -l 'x \\' ' -Osh' prey",
        f"git -C {spelled} log '--format=%B','--output=Makefile'",
    ):
        assert _powershell(command, home) is not None, command


@pytest.mark.parametrize(
    ("tool", "key"),
    [
        ("Write", "file_path"),
        ("Edit", "file_path"),
        ("MultiEdit", "file_path"),
        ("NotebookEdit", "notebook_path"),
    ],
)
def test_a_file_tool_may_not_write_into_the_cache(tool: str, key: str, home: Path) -> None:
    clone = _clone(home)
    root = home / ".cache" / "hungry-crab"
    cwd = str(home / "work" / "maw")
    for target in (
        str(clone / ".git" / "config"),
        clone.as_posix() + "/Makefile",
        f"~/.cache/hungry-crab/{REPO}/setup.py",
        str(home / ".cache" / "x" / ".." / "hungry-crab" / "new.py"),
    ):
        event = {"tool_name": tool, "tool_input": {key: target}, "cwd": cwd}
        reason = event_guard_reason(event, root=root)
        assert reason is not None, target
        assert "rule 3" in reason
    for target in (str(home / "work" / "maw" / "x.py"), str(home / ".cache" / "uv" / "x")):
        event = {"tool_name": tool, "tool_input": {key: target}, "cwd": cwd}
        assert event_guard_reason(event, root=root) is None, target
    assert event_guard_reason({"tool_name": tool, "tool_input": {}}, root=root) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="device and share spellings are Windows'")
def test_a_file_tool_path_through_a_device_or_a_share_is_the_same_file(home: Path) -> None:
    clone = _clone(home)
    root = home / ".cache" / "hungry-crab"
    windows = str(clone / ".git" / "config")
    without_drive = windows[3:]
    for target in (
        "\\\\?\\" + windows,
        "\\\\.\\" + windows,
        "\\\\localhost\\C$\\" + without_drive,
        "\\\\127.0.0.1\\c$\\" + without_drive,
        "\\\\?\\UNC\\localhost\\C$\\" + without_drive,
        "\\\\.\\UNC\\localhost\\C$\\" + without_drive,
        "//./UNC/localhost/C$/" + without_drive.replace("\\", "/"),
        "//?/UNC/localhost/C$/" + without_drive.replace("\\", "/"),
        "/" + windows[0].lower() + "/" + without_drive.replace("\\", "/"),
        "//" + windows[0].lower() + "/" + without_drive.replace("\\", "/"),
        "/cygdrive/" + windows[0].lower() + "/" + without_drive.replace("\\", "/"),
    ):
        event = {"tool_name": "Write", "tool_input": {"file_path": target}, "cwd": str(home)}
        assert event_guard_reason(event, root=root) is not None, target


TERMINAL = "mcp__terminal__run_in_terminal"


def test_the_desktop_terminal_is_judged_as_bash_and_as_powershell(home: Path) -> None:
    """The desktop app's terminal runs PowerShell or a login shell: its command is refused when
    either reading refuses, and its own `cwd` is where it runs."""
    clone = _clone(home)
    root = home / ".cache" / "hungry-crab"
    maw = str(home / "work" / "maw")
    for command, workdir in (
        (f"make -C ~/.cache/hungry-crab/{REPO}", None),
        (f"Set-Location ~\\.cache\\hungry-crab\\{BACKSLASH_REPO}; & .\\install.ps1", None),
        ("make", str(clone)),
        ("python setup.py", str(clone)),
    ):
        tool_input = {"command": command, "tab_id": "1"}
        if workdir is not None:
            tool_input["cwd"] = workdir
        event = {"tool_name": TERMINAL, "tool_input": tool_input, "cwd": maw}
        assert event_guard_reason(event, root=root) is not None, command
    for command in ("git status", "uv run pytest", "Get-ChildItem src"):
        event = {"tool_name": TERMINAL, "tool_input": {"command": command, "cwd": maw}, "cwd": maw}
        assert event_guard_reason(event, root=root) is None, command
    # Any command tool's `cwd` is where its command runs.
    event = {"tool_name": "Bash", "tool_input": {"command": "make", "cwd": str(clone)}, "cwd": maw}
    assert event_guard_reason(event, root=root) is not None


@pytest.mark.parametrize(
    ("ordinary", "twin"),
    [
        (
            "find ~ -maxdepth 3 -name .git -type d | xargs -n1 dirname",
            "find ~ -maxdepth 3 -name .git -type d | xargs -n1 make -C",
        ),
        ("fd -t d -d 3 '^.git$' ~ -x dirname", "fd -t d -d 3 '^.git$' ~ -x make -C"),
    ],
)
def test_printers_stay_allowed_after_the_fifth_review(ordinary: str, twin: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    for cwd in (home / "work" / "maw", home):
        assert guard_reason(ordinary, root=root, cwd=cwd) is None, cwd
    assert guard_reason(twin, root=root, cwd=home / "work" / "maw") is not None


# Message text is a channel like any other: recorded, read back and run. As in 0.6.1, a
# message that spells the cache counts as the cache.
MESSAGES = [
    'git commit -m "fix: refuse writes under ~/.cache/hungry-crab"',
    "git commit -m \"$(cat <<'EOF'\nfix: refuse writes in ~/.cache/hungry-crab\nEOF\n)\"",
    "git commit -F - <<'EOF'\nfix: refuse ~/.cache/hungry-crab writes\nEOF",
    "gh release create v1 --notes 'cache at ~/.cache/hungry-crab'",
    (
        "gh pr create --title 'fix(guard): hungry-crab' --body \"$(cat <<'EOF'\n"
        'Guard refuses ~/.cache/hungry-crab writes.\nEOF\n)"'
    ),
    (
        f'git commit --allow-empty -m "python ~/.cache/hungry-crab/{TAIL}" && '
        "git log -1 --format=%s | sh"
    ),
    (
        f"git commit --allow-empty -m $HOME/.cache/hungry-crab/{REPO} && "
        'make -C "$(git log -1 --format=%s)"'
    ),
    f'git notes add -m "make -C ~/.cache/hungry-crab/{REPO}" && git notes show | bash',
    (
        f"git tag -a t1 -m $HOME/.cache/hungry-crab/{TAIL} && "
        "python \"$(git tag -l --format='%(contents)' t1)\""
    ),
    (
        f"git commit --allow-empty -m ~/.cache/hungry-crab/{REPO} && "
        "git log -1 --format=%s | xargs make -C"
    ),
    (
        f"git commit --allow-empty -F - <<'EOF'\n~/.cache/hungry-crab/{TAIL}\nEOF\n"
        'python "$(git log -1 --format=%s)"'
    ),
    (
        f"git commit --allow-empty -m '/.cache/hungr'\"y-crab/{TAIL}\"\n"
        f"python - <<'EOF'\nimport runpy; runpy.run_path(r\"~/.cache/hungry-crab/{TAIL}\")\nEOF"
    ),
    (
        f"git commit --allow-empty -m $'/.cache/hungry\\x2dcrab/{TAIL}'\n"
        f"python - <<'EOF'\nimport runpy; runpy.run_path(r\"~/.cache/hungry-crab/{TAIL}\")\nEOF"
    ),
]


@pytest.mark.parametrize("command", MESSAGES)
def test_a_message_that_spells_the_cache_counts_as_it_did_in_0_6_1(
    command: str, home: Path
) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert reason is not None
    assert "rule 3" in reason


@pytest.mark.parametrize(
    "command",
    [
        'docker run --rm -v "$(pwd)":/w -w /w node:20 npm test',
        "docker run --rm -v ~/.cache/uv:/uv alpine ls /uv",
        "git -C src log --oneline -3",
        "make -C src test",
        "uv run --directory ../other pytest",
        "grep -C 3 TODO src/app.py",
        "Get-ChildItem src -OutVariable files; $files.Count",
        "cp -r src /tmp/s && find /tmp/s -name '*.py' | xargs python -m py_compile",
        "cp -r ~/.cache/uv /tmp/uv && ls /tmp/uv",
    ],
)
def test_ordinary_commands_stay_allowed_after_the_fifth_review(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    shell = "powershell" if command.startswith("Get-") else "bash"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw", shell=shell) is None


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Monitor", {"command": f"python ~/.cache/hungry-crab/{TAIL}", "description": "x"}),
        ("Monitor", {"command": f"make -C ~/.cache/hungry-crab/{REPO} 2>&1 | grep error"}),
        ("Bash", {"command": SPLIT}),
        ("Bash", {"command": "docker run --rm -v ~/.cache:/c alpine ls /c"}),
        ("Write", {"file_path": f"~/.cache/hungry-crab/{REPO}/.git/config", "content": "x"}),
        ("NotebookEdit", {"notebook_path": f"~/.cache/hungry-crab/{REPO}/x.ipynb"}),
        (TERMINAL, {"command": f"make -C ~/.cache/hungry-crab/{REPO}", "tab_id": "1"}),
        (
            TERMINAL,
            {"command": f"Set-Location ~\\.cache\\hungry-crab\\{BACKSLASH_REPO}; & .\\install.ps1"},
        ),
        (
            "Bash",
            {
                "command": (
                    f"git commit --allow-empty -m $HOME/.cache/hungry-crab/{REPO} && "
                    'make -C "$(git log -1 --format=%s)"'
                )
            },
        ),
        ("Bash", {"command": f"cat ~/.cache/hungry-crab/{REPO}/README.md | sort -S1K --co=sh"}),
    ],
    ids=[
        "monitor",
        "monitor-pipe",
        "split",
        "mount",
        "write",
        "notebook",
        "terminal-bash",
        "terminal-powershell",
        "message",
        "sort",
    ],
)
def test_the_fifth_review_cases_are_refused_through_the_launcher(
    tool: str, tool_input: dict[str, str], home: Path
) -> None:
    event = {"tool_name": tool, "tool_input": tool_input, "cwd": str(home / "work")}
    env = {key: value for key, value in os.environ.items() if key != "CRAB_CACHE_DIR"}
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "hooks" / "guard.py"), "prey"],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 2, result.stderr


# --- the seventh review -------------------------------------------------------------------------

LOCAL = f"localhost:.cache/hungry-crab/{REPO}"

# A copy, clone or shell over ssh from this machine names a local path (relative to home);
# a brace expansion or an array read beside a way to the cache is refused, as 0.6.1 did.
OVER_SSH = [
    f"scp {LOCAL}/Makefile .",
    f"scp {LOCAL}/install.sh .git/hooks/pre-commit",
    f"scp -r {LOCAL} /tmp/w$$",
    f"scp {LOCAL}/{{install.sh,Makefile}} .",
    f"scp {LOCAL}/Makefile{{,}} .",
    f"rsync -a {LOCAL}/{{Makefile,package.json}} ./",
    f"rsync {LOCAL}/install.sh .git/hooks/pre-commit",
    f"rsync {LOCAL}/{{install.sh,}} .git/hooks/pre-commit",
    f"read -a a < /dev/null; scp {LOCAL}/install.sh .git/hooks/pre-commit",
    f"readarray -t a < /dev/null; scp {LOCAL}/Makefile .",
    f"mapfile -t a < /dev/null; scp {LOCAL}/Makefile .",
    f"declare -a a; scp {LOCAL}/Makefile .",
    f"git clone {LOCAL} vendor/widget{{,}}",
    f"git clone {LOCAL} /tmp/w; echo $?",
    f"git clone {LOCAL} vendor/widget-$(date +%s)",
    f"git clone ssh://localhost/~/.cache/hungry-crab/{REPO} vendor/widget",
    f"git clone ssh://me@127.0.0.1:22/~/.cache/hungry-crab/{REPO} vendor/widget",
    f"git clone '[::1]:.cache/hungry-crab/{REPO}' vendor/widget",
    f"git archive --remote={LOCAL} HEAD | tar -x",
    f"sftp {LOCAL}/Makefile",
    f"rsync -a me@localhost:~/.cache/hungry-crab/{REPO}/ ./w/",
    f"ssh localhost 'make -C .cache/hungry-crab/{REPO}'",
    f"echo Makefile | xargs -I{{}} scp {LOCAL}/{{}} .",
    f"f=Makefile.bak; scp {LOCAL}/${{f%.bak}} .",
]


@pytest.mark.parametrize("command", OVER_SSH)
def test_a_copy_over_ssh_from_this_machine_is_a_local_path(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    reason = guard_reason(command, root=root, cwd=home / "work" / "maw")
    assert reason is not None
    assert "rule 3" in reason


def test_this_machine_by_its_own_name_and_from_powershell(home: Path) -> None:
    import socket

    root = home / ".cache" / "hungry-crab"
    cwd = home / "work" / "maw"
    own = socket.gethostname()
    command = f"scp {own.upper()}:.cache/hungry-crab/{REPO}/Makefile ."
    assert guard_reason(command, root=root, cwd=cwd) is not None
    assert _powershell(f"scp {LOCAL}/Makefile .", home) is not None


@pytest.mark.parametrize(
    "command",
    [
        "scp build-host:dist/x.tgz .",
        "rsync -a ./ build-host:/srv/app/",
        "git clone git@github.com:o/r.git",
        "git clone https://github.com/drevendev/HungryCrab hc",
        "ssh build-host 'make -C .cache/hungry-crab'",
        "rsync -a host::module/ ./mirror/",
        "ls ~/.cache/uv/{a,b}",
        "mapfile -t lines < notes.txt; echo ${#lines[@]}",
    ],
)
def test_other_hosts_stay_allowed_after_the_seventh_review(command: str, home: Path) -> None:
    root = home / ".cache" / "hungry-crab"
    assert guard_reason(command, root=root, cwd=home / "work" / "maw") is None
