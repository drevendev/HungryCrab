"""Fail-closed shell guard for commands that touch Hungry Crab's prey cache.

The guard does not try to parse all shell grammar. Once a command references the prey cache,
it allows only a small audited set of read-only command shapes. Unknown cache-touching shapes
are denied rather than assumed safe.

A command references the cache when one of its words can become a path in it after the shell
has built the word — variables, `~` forms, globs, brace expansion, substitutions, a `cd` earlier
in the command — or when a word names the directory that holds the cache. Ordinary commands
that never come near the cache stay out of scope, because the guard runs on every shell call.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TextIO

from .cache import cache_root
from .fs import is_reparse_point

Shell = Literal["bash", "powershell"]
# Claude Code's shell tools; both take a `command`.
_SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
_ENV_CACHE_REF = re.compile(
    r"(?:\$\{?CRAB_CACHE_DIR\}?|%CRAB_CACHE_DIR%|\$env:CRAB_CACHE_DIR)", re.IGNORECASE
)
# The cache root, and nothing that merely starts with its name: `hungry-crab-other` and
# `hungry-crab.old` are siblings, not the cache.
_PATH_BOUNDARY = r"(?![a-z0-9._-])"
# After a separator: `.cache/hungry-crab` alone, in a commit message or a grep pattern, is text.
_DEFAULT_CACHE_REF = re.compile(
    r"(?:^|[/\\])\.cache[/\\]hungry-crab" + _PATH_BOUNDARY, re.IGNORECASE
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_EXECUTION_SUBSTITUTION = re.compile(r"\$\(|[<>]\(|`")
_LINE_BREAK = re.compile(r"[\r\n]")
_READ_ONLY_COMMANDS = frozenset({"cat", "grep", "head", "ls", "rg", "stat", "tail", "wc"})
# Plumbing and porcelain that only read the repository. `show`, `diff`, `blame`, `shortlog`,
# `describe` and `grep` are what the historian agent is told to run in a clone; the arguments
# that would make any of them run something are refused below.
_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "blame",
        "cat-file",
        "describe",
        "diff",
        "for-each-ref",
        "grep",
        "log",
        "ls-files",
        "ls-tree",
        "rev-list",
        "rev-parse",
        "shortlog",
        "show",
        "show-ref",
        "status",
        "symbolic-ref",
    }
)
_GIT_GLOBAL_SAFE_FLAGS = frozenset(
    {"--no-pager", "--literal-pathspecs", "--no-optional-locks", "--no-replace-objects"}
)
# The forges `cache.prey_paths` lays clones out for: `<root>/github/<owner>/<repo>/repo` and
# `<root>/gitlab/<namespace...>/<project>/repo`.
_CLONE_FORGES = frozenset({"github", "gitlab"})
# `--output` writes what git prints to any path, which turns `log` into a way to land prey
# bytes where a later command runs them; `-O`/`--open-files-in-pager` hands `grep`'s matches to
# a program of the caller's choosing; `--filters` makes `cat-file` run the smudge filters a
# config names. git accepts any unambiguous abbreviation of a long option (`--open=sh`,
# `--textc`), so a prefix of one of these names is refused too, and `-O` is refused inside a
# cluster of short options (`-lOsh`).
_GIT_DANGEROUS_LONG_OPTIONS = (
    "ext-diff",
    "textconv",
    "filters",
    "exec-path",
    "config-env",
    "output",
    "open-files-in-pager",
)
_GIT_DANGEROUS_SHORT_OPTIONS = frozenset("O")
# ripgrep reads, except where an option hands work to another program: `--pre` runs a
# preprocessor on every file, `--hostname-bin` runs a program to name the host in a hyperlink,
# and `-z`/`--search-zip` starts a decompressor for every compressed file the prey chose to
# ship. ripgrep takes no abbreviations of long options, so the exact names are the whole set.
_RG_SPAWNING_OPTIONS = ("--pre", "--hostname-bin", "--search-zip")
_OPERATOR_CHARS = ";&|<>"
_DEVICE_PREFIX = re.compile(r"^(?:[\\/]{2}[?.][\\/]|\\\?\?\\)(UNC[\\/])?", re.IGNORECASE)


def strip_device_prefix(text: str) -> str:
    """The Win32 path a device-prefixed spelling names.

    ``\\\\?\\C:\\x``, ``\\\\.\\C:\\x`` and ``\\??\\C:\\x`` name ``C:\\x``, and
    ``\\\\?\\UNC\\host\\share`` names ``\\\\host\\share``. ``Path.resolve()`` keeps such a prefix
    verbatim, so a path compared with it attached never equals the cache root.
    """
    match = _DEVICE_PREFIX.match(text)
    if match is None:
        return text
    rest = text[match.end() :]
    return "\\\\" + rest if match.group(1) else rest


def _expand_home(path: Path) -> Path:
    """``~`` and ``~user`` expanded; a ``~name`` with no known home stays literal, as in a shell.

    ``Path.expanduser()`` raises for it instead, and any text that starts with ``~`` (Markdown's
    ``~~struck~~`` in an edit, say) would otherwise count as a path that cannot be judged.
    """
    try:
        return path.expanduser()
    except RuntimeError:
        return path


_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_DEVICE_DRIVE = re.compile(r"^(?:[\\/]{2}[?.][\\/]|\\\?\?\\)[A-Za-z]:(?:[\\/]|$)")
_DEVICE_VOLUME = re.compile(r"^(?:[\\/]{2}[?.][\\/]|\\\?\?\\)(?:Volume\{|GLOBALROOT[\\/])", re.I)
_UNC = re.compile(r"^(?:[\\/]{2}(?![?.][\\/])|\\\\\?\\UNC\\)([^\\/]+)[\\/]+([^\\/]+)(.*)$", re.I)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0--1.ipv6-literal.net", "[::1]"})


def is_drive_path(path: Path) -> bool:
    """Whether a Windows path is on a drive letter, not a share or a device."""
    return _DRIVE_PATH.match(str(path)) is not None


def _windows_spelling(text: str) -> tuple[str, bool]:
    """A Windows path as a drive path where that is a local question; whether to resolve it.

    `\\\\?\\C:\\x` is `C:\\x`. `\\\\?\\Volume{...}\\x` and `\\\\?\\GLOBALROOT\\...` name a local
    volume, so `resolve()` (a local call) turns them into the drive path. `\\\\localhost\\C$\\x`
    and its `\\\\?\\UNC\\` form are `C:\\x`. Any other share is left as written and is never
    resolved: that would ask the network, and a slow decision is an allowance, because a hook
    that times out lets the call proceed.
    """
    if _DEVICE_DRIVE.match(text):
        return strip_device_prefix(text), True
    if _DEVICE_VOLUME.match(text):
        try:
            resolved = strip_device_prefix(str(Path(text).resolve(strict=False)))
        except (OSError, RuntimeError, ValueError):
            return text, False
        return (resolved, True) if _DRIVE_PATH.match(resolved) else (text, False)
    unc = _UNC.match(text)
    if unc:
        host, share, rest = unc.groups()
        local = _LOCAL_HOSTS | {os.environ.get("COMPUTERNAME", "localhost").casefold()}
        if host.casefold() in local and re.fullmatch(r"[A-Za-z]\$", share):
            return f"{share[0]}:\\{rest.lstrip(chr(92) + '/')}", True
        return text, False
    if text[:2] in ("\\\\", "//") or text.startswith("\\??\\"):
        return text, False  # another device: a pipe, a console; nothing to open for a name
    return text, True


def canonical_path(path: Path, *, cwd: Path, expand_home: bool = True) -> Path:
    """The one spelling both guards compare: absolute, resolved, device prefix stripped.

    On Windows ``resolve()`` asks for the final path name of the longest existing prefix, which
    expands 8.3 short names (``HUNGRY~1``) and follows junctions; a device spelling is reduced
    to its drive path first (see ``_windows_spelling``), and a path on a share is normalised
    without asking the network. ``expand_home=False`` keeps a leading ``~`` as the directory
    of that name, which is what a program receives for a quoted ``'~/x'``.
    """
    text = os.fspath(path)
    local = True
    if os.name == "nt":
        text, local = _windows_spelling(text)
    candidate = _expand_home(Path(text)) if expand_home else Path(text)
    if not candidate.is_absolute():
        candidate = cwd / candidate
        if os.name == "nt" and not is_drive_path(cwd):
            local = False  # relative to a working directory on a share
    if not local:
        return Path(os.path.normpath(candidate))
    return candidate.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


# Every word is resolved against every directory the command can be in, and each resolution is
# a filesystem call. A decision that outlasts the hook timeout lets the call proceed, so one
# decision may spend at most this many; past it the guard decides from the text alone.
_RESOLUTION_BUDGET = 256
_budget: ContextVar[list[int] | None] = ContextVar("crab_guard_budget", default=None)


class _BudgetExceededError(Exception):
    """The command needs more filesystem work than one decision may spend."""


def _spend() -> None:
    left = _budget.get()
    if left is not None:
        left[0] -= 1
        if left[0] < 0:
            raise _BudgetExceededError


def _within_budget(decide: Callable[[], bool]) -> bool | None:
    """``decide()``, or ``None`` when it would spend more than the budget."""
    token = _budget.set([_RESOLUTION_BUDGET])
    try:
        return decide()
    except _BudgetExceededError:
        return None
    finally:
        _budget.reset(token)


# A leading `~` that was quoted or escaped: bash expands only an unquoted one, so `'~/x'` names a
# directory called `~` and `~/x` names the home directory's `x`.
_LITERAL_TILDE = "\ue006"
_ANSI_C_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}


def _ansi_c(text: str, start: int) -> tuple[str, int]:
    """Decode the `$'...'` body from ``start``: its text and the closing quote's index, or -1."""
    out: list[str] = []
    index = start
    while index < len(text):
        char = text[index]
        if char == "'":
            return "".join(out), index
        if char != "\\" or index + 1 >= len(text):
            out.append(char)
            index += 1
            continue
        code = text[index + 1]
        digits = {"x": (16, 2), "u": (16, 4), "U": (16, 8)}.get(code)
        if digits is not None or code in "01234567":
            base, limit = digits if digits is not None else (8, 3)
            first = index + 2 if digits is not None else index + 1
            end = first
            alphabet = "0123456789abcdefABCDEF"[: 22 if base == 16 else 8]
            while end < len(text) and end - first < limit and text[end] in alphabet:
                end += 1
            if end > first:
                out.append(chr(min(int(text[first:end], base), 0x10FFFF)))
            index = end if end > first else index + 2
        else:
            out.append(_ANSI_C_ESCAPES.get(code, "\\" + code))
            index += 2
    return "".join(out), -1


def _shell_tokens(command: str) -> list[str] | None:
    """Bash words, quotes and escapes removed, operator runs as tokens; ``None`` if left open.

    Split as `shlex` in POSIX mode would, but with bash's own rules where they decide which file
    a word names: `\\` inside double quotes escapes only ``$ ` " \\``, `$'...'` is decoded, and a
    word whose leading `~` was quoted or escaped starts with ``_LITERAL_TILDE`` instead.
    """
    tokens: list[str] = []
    word: list[str] | None = None
    bare_start = True  # whether the word's first character came from unquoted text
    # Bash expands a leading `~` only when nothing up to the first unquoted `/` is quoted:
    # `~"/x"`, `~\/x` and `~''/x` all name a directory called `~`.
    tilde_open = False
    quoted_prefix = False
    index = 0
    length = len(command)

    def finish() -> None:
        text = "".join(word or [])
        if text.startswith("~") and (not bare_start or quoted_prefix):
            text = _LITERAL_TILDE + text[1:]
        tokens.append(text)

    while index < length:
        char = command[index]
        if char in " \t\r\n" or char in _OPERATOR_CHARS:
            if word is not None:
                finish()
                word = None
            if char in _OPERATOR_CHARS:
                end = index
                while end < length and command[end] in _OPERATOR_CHARS:
                    end += 1
                tokens.append(command[index:end])
                index = end
            else:
                index += 1
            continue
        if word is None:
            word = []
            bare_start = char not in "\\'\"" and not command.startswith(("$'", '$"'), index)
            tilde_open = bare_start and char == "~"
            quoted_prefix = False
        if tilde_open and (char in "\\'\"" or command.startswith(("$'", '$"'), index)):
            quoted_prefix = True
        if char == "\\":
            if index + 1 >= length:
                return None
            if command[index + 1] != "\n":
                word.append(command[index + 1])
            index += 2
        elif char == "'":
            end = command.find("'", index + 1)
            if end < 0:
                return None
            word.append(command[index + 1 : end])
            index = end + 1
        elif char == "$" and command.startswith("'", index + 1):
            decoded, end = _ansi_c(command, index + 2)
            if end < 0:
                return None
            word.append(decoded)
            index = end + 1
        elif char == '"' or (char == "$" and command.startswith('"', index + 1)):
            position = index + (2 if char == "$" else 1)
            body: list[str] = []
            while position < length and command[position] != '"':
                if command[position] == "\\" and command[position + 1 : position + 2] in (
                    "$",
                    "`",
                    '"',
                    "\\",
                    "\n",
                ):
                    if command[position + 1] != "\n":
                        body.append(command[position + 1])
                    position += 2
                else:
                    body.append(command[position])
                    position += 1
            if position >= length:
                return None
            word.append("".join(body))
            index = position + 1
        else:
            if char == "/":
                tilde_open = False  # the tilde prefix ends at the first unquoted slash
            word.append(char)
            index += 1
    if word is not None:
        finish()
    return tokens


def _is_punctuation(token: str) -> bool:
    """`;`, `&&`, `|&`, `>|`, `&>>` ...: `shlex` keeps a run of operator characters together."""
    return bool(token) and all(char in _OPERATOR_CHARS for char in token)


def names_root(text: str, root: Path) -> bool:
    """Whether ``text`` spells the cache root, with a path boundary after it."""
    root_text = str(root).replace("\\", "/").casefold()
    if not root_text:
        return False
    normalized = text.replace("\\", "/").casefold()
    return re.search(re.escape(root_text) + _PATH_BOUNDARY, normalized) is not None


# --- the scope gate -------------------------------------------------------------------------
#
# A command is cache-touching when one of its words can become a path in the cache, not only
# when it spells one. The shell builds words at run time (variables, `~` forms, globs, brace
# expansion, substitutions, `cd`), so each word is expanded as far as the guard can know it:
# known variables and assignments in the same command are substituted, and whatever the shell
# decides at run time is marked with a private-use character that no judged path contains.

_STAR = "\ue000"  # a glob `*`: any text within one component, but not a leading dot
_ONE = "\ue001"  # a glob `?` or `[...]`: one character, but not a leading dot
_ANY = "\ue002"  # brace alternatives, a variable inside a word: any text within a component
_CROSS = "\ue003"  # `**`, a brace group holding a separator: any text, separators included
_UNKNOWN = "\ue004"  # an expansion the guard cannot evaluate; at a word's start, any directory
_SUBSTITUTION = "\ue005"  # where `$(...)`, `<(...)` or a backtick group stood
_MARKERS = frozenset({_STAR, _ONE, _ANY, _CROSS, _UNKNOWN})
_MAX_CANDIDATES = 16
_MAX_DEPTH = 3
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGNMENT_WORD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=(.*)$", re.DOTALL)
_PERCENT_VARIABLE = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")
_SPECIAL_PARAMETERS = frozenset("0123456789@*#?$!-")
_SEPARATORS = re.compile(r"[/\\]")
_DRIVE = re.compile(r"[A-Za-z]:")
_MSYS_DRIVE = re.compile(r"^(?:/cygdrive)?/([A-Za-z])(?=[/\\]|$)")
_LEADING_KEYWORDS = frozenset(
    {"!", "{", "if", "then", "else", "elif", "do", "while", "until", "time", "command", "builtin"}
)
_CD_COMMANDS = frozenset({"cd", "pushd", "chdir"})
# A token that could make a program the shell starts somewhere the guard did not judge.
_ASSEMBLY = re.compile(r"[$`*?\[{%\ue000-\ue006]|^~[^/\\]")


@dataclass
class _Scope:
    """What the guard knows about where one command's words can point."""

    root: Path
    cwd: Path
    home: Path
    variables: dict[str, list[str] | None] = field(default_factory=dict)
    cwds: list[Path] = field(default_factory=list)
    unknown_cwd: bool = False
    powershell: bool = False
    homes: list[Path] = field(default_factory=list)  # `~`: Python's home and `$HOME`
    hints: list[re.Pattern[str]] = field(default_factory=list)  # see `_hint_patterns`


def _closing_paren(text: str, start: int) -> int:
    """Index of the `)` or `}` closing the bracket at ``start``, skipping quotes; else the end."""
    opening = text[start]
    closing = "}" if opening == "{" else ")"
    depth = 0
    quote = ""
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def _extract_substitutions(command: str) -> tuple[str, list[str]]:
    """Replace each `$(...)`, `<(...)`, `>(...)` and backtick group with a placeholder.

    What the group prints is spliced into the word and cannot be known; the group's own text is
    returned so that it is judged as a command of its own.
    """
    out: list[str] = []
    groups: list[str] = []
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\":
            out.append(command[index : index + 2])
            index += 2
        elif char == "'" and not in_double:
            end = command.find("'", index + 1)
            end = len(command) - 1 if end < 0 else end
            out.append(command[index : end + 1])
            index = end + 1
        elif char == '"':
            in_double = not in_double
            out.append(char)
            index += 1
        elif char == "`":
            end = command.find("`", index + 1)
            end = len(command) if end < 0 else end
            groups.append(command[index + 1 : end])
            out.append(_SUBSTITUTION)
            index = end + 1
        elif char in "$<>" and command.startswith("(", index + 1):
            end = _closing_paren(command, index + 1)
            groups.append(command[index + 2 : end])
            out.append(_SUBSTITUTION)
            index = end + 1
        else:
            out.append(char)
            index += 1
    return "".join(out), groups


def _simple_commands(tokens: list[str]) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    for token in tokens:
        if _is_punctuation(token):
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _command_words(words: list[str]) -> list[str]:
    """One simple command from its command name on, without keywords and assignments."""
    for index, raw in enumerate(words):
        word = raw.lstrip("(")
        if word and word not in _LEADING_KEYWORDS and not _ASSIGNMENT_WORD.match(word):
            return [word, *words[index + 1 :]]
    return []


def _assign(scope: _Scope, name: str, value: str) -> None:
    values = scope.variables.get(name, [])
    if values is not None:
        values = [*values, value]
        scope.variables[name] = values if len(values) <= _MAX_CANDIDATES else None


def _build_scope(
    tokens: list[str], *, root: Path, cwd: Path, variables: dict[str, list[str] | None]
) -> _Scope:
    home = canonical_path(Path.home(), cwd=cwd)
    scope = _Scope(
        root=root,
        cwd=cwd,
        home=home,
        variables=dict(variables),
        cwds=[cwd],
        homes=_homes(cwd),
        hints=_hint_patterns(root, _homes(cwd)),
    )
    commands = _simple_commands(tokens)
    # Order does not matter: a value assigned or a directory entered anywhere in the command is
    # considered everywhere in it, which can only add places a word may point to.
    for words in commands:
        for word in words:
            match = _ASSIGNMENT_WORD.match(word.lstrip("("))
            if match:
                _assign(scope, match.group(1), match.group(2))
        command = _command_words(words)
        if len(command) >= 3 and command[0] in {"for", "select"} and command[2] == "in":
            values = command[3:]
            values = values[: values.index("do")] if "do" in values else values
            for value in values:
                _assign(scope, command[1], value)
        elif command and command[0] == "read":
            for name in command[1:]:
                if _NAME.fullmatch(name):
                    scope.variables[name] = None
    for words in commands:
        command = _command_words(words)
        if command and command[0] in _CD_COMMANDS:
            _change_directory(scope, command[1:])
    return scope


def _change_directory(scope: _Scope, args: list[str]) -> None:
    targets = [arg for arg in args if arg == "-" or not arg.startswith("-")]
    target = targets[0] if targets else "~"
    if target == "-":
        scope.unknown_cwd = True
        return
    for candidate in _expand_word(target, scope, []):
        if any(marker in candidate for marker in _MARKERS):
            scope.unknown_cwd = True
            continue
        for base in list(scope.cwds):
            _spend()
            try:
                path = _word_path(_msys_drive(candidate), cwd=base)
            except (OSError, RuntimeError, ValueError):
                scope.unknown_cwd = True
                continue
            if path not in scope.cwds:
                scope.cwds.append(path)
    if len(scope.cwds) > _MAX_CANDIDATES:
        del scope.cwds[_MAX_CANDIDATES:]
        scope.unknown_cwd = True


def _environment(name: str, scope: _Scope) -> str | None:
    if name.upper() == "PWD":
        return str(scope.cwd)
    value = os.environ.get(name)
    if value is None and name.upper() in {"HOME", "USERPROFILE"}:
        return str(scope.home)
    return value


def _variable(name: str, scope: _Scope, inner: list[str], depth: int) -> list[str]:
    """The texts a variable can hold: assigned in the command, inherited, or unknown."""
    if scope.powershell:
        name = name.casefold()  # PowerShell variable names ignore case
    if name in scope.variables:
        raw_values = scope.variables[name]
        if raw_values is None or depth >= _MAX_DEPTH:
            return [_UNKNOWN]
        values: list[str] = []
        for raw in raw_values:
            values.extend(_expand_word(raw, scope, inner, depth=depth + 1))
        return values if len(values) <= _MAX_CANDIDATES else [_UNKNOWN]
    if name.upper() in {"PWD", "OLDPWD"}:
        return _directories(scope)
    if name.upper() in {"HOME", "USERPROFILE"}:
        # PowerShell's `$HOME` is the profile directory whatever `HOME` says; bash's is `HOME`.
        # Both readings are judged.
        values = [str(home) for home in scope.homes or [scope.home]]
        values += [os.environ[key] for key in ("HOME", "USERPROFILE") if os.environ.get(key)]
        return list(dict.fromkeys(values))
    value = _environment(name, scope)
    if value is None:
        return [_UNKNOWN]
    return [value.replace("*", _STAR).replace("?", _ONE)]


def _parameter(
    word: str, index: int, scope: _Scope, inner: list[str], depth: int
) -> tuple[list[str], int]:
    """Expand the `$...` at ``index``: `$name`, `${name}`, `${name<op>word}`, `$env:NAME`."""
    rest = word[index + 1 :]
    if rest.startswith("{"):
        close = word.find("}", index + 2)
        if close < 0:
            return [_UNKNOWN], len(word)
        body = word[index + 2 : close]
        if body[:4].casefold() == "env:" and _NAME.fullmatch(body[4:]):
            return _variable(body[4:], scope, inner, depth), close + 1
        if _NAME.fullmatch(body):
            return _variable(body, scope, inner, depth), close + 1
        # `${x:-word}`, `${x/a/b}`, `${#x}`: the word part can name a path of its own.
        name = _NAME.match(body)
        inner.append(body[name.end() if name else 0 :].lstrip(":-=+?#%/^,@"))
        return [_UNKNOWN], close + 1
    if rest[:4].casefold() == "env:":
        name = _NAME.match(rest, 4)
        if name:
            return _variable(name.group(), scope, inner, depth), index + 1 + name.end()
    name = _NAME.match(rest)
    if name:
        return _variable(name.group(), scope, inner, depth), index + 1 + name.end()
    if rest[:1] in _SPECIAL_PARAMETERS and rest:
        return [_UNKNOWN], index + 2
    return ["$"], index + 1


def _brace_group(word: str, index: int) -> tuple[int, bool] | None:
    """End of the brace expansion at ``index`` and whether it holds a separator, or ``None``."""
    depth = 0
    alternatives = False
    crosses = False
    for position in range(index, len(word)):
        char = word[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                body = word[index + 1 : position]
                return (position, crosses) if alternatives or ".." in body else None
        elif char == "," and depth == 1:
            alternatives = True
        elif char in "/\\":
            crosses = True
    return None


def _directories(scope: _Scope) -> list[str]:
    """What `$PWD`, `$OLDPWD`, `~+` and `~-` can hold: any directory the command can be in.

    A `cd`, `pushd`, `popd` or `Set-Location` anywhere in the command moves them, so the event's
    working directory alone is not their value.
    """
    return [str(cwd) for cwd in scope.cwds] + ([_UNKNOWN] if scope.unknown_cwd else [])


def _tilde(prefix: str, scope: _Scope) -> list[str]:
    if prefix == "~":
        return [str(home) for home in scope.homes or [scope.home]]
    if prefix in ("~+", "~-"):
        return _directories(scope)
    try:
        expanded = str(Path(prefix).expanduser())  # `~user`
    except RuntimeError:
        return [_UNKNOWN]
    return [expanded if expanded != prefix else _UNKNOWN]


def _expand_word(word: str, scope: _Scope, inner: list[str], *, depth: int = 0) -> list[str]:
    """Every text the shell can make of ``word``, with markers where it decides at run time."""
    results = [""]
    index = 0
    device = _DEVICE_PREFIX.match(word) if os.name == "nt" else None
    if device:
        results = [word[: device.end()]]  # kept as written: its `?` is not a glob
        index = device.end()
    elif word.startswith(_LITERAL_TILDE) or (scope.powershell and word.startswith("~")):
        # A quoted `~` is a directory of that name; PowerShell passes `~` to a program as is but
        # its own cmdlets read it as the home directory. Both readings are judged.
        separator = _SEPARATORS.search(word)
        index = separator.start() if separator else len(word)
        name = word[1:index]
        results = [_LITERAL_TILDE + name, *_tilde("~" + name, scope)]
    elif word.startswith("~"):
        separator = _SEPARATORS.search(word)
        index = separator.start() if separator else len(word)
        results = _tilde(word[:index], scope)
    while index < len(word):
        char = word[index]
        pieces = [char]
        step = 1
        if char == "$":
            pieces, end = _parameter(word, index, scope, inner, depth)
            step = end - index
        elif char == "%" and (percent := _PERCENT_VARIABLE.match(word, index)):
            value = _environment(percent.group(1), scope)
            pieces = [percent.group() if value is None else value]
            step = percent.end() - index
        elif char == _SUBSTITUTION:
            pieces = [_UNKNOWN]
        elif scope.powershell:
            if char in "*?" or (char == "[" and word.find("]", index + 1) > 0):
                # PowerShell wildcards also match dot files; `[...]` is one character.
                pieces = [_ANY]
                step = word.find("]", index + 1) + 1 - index if char == "[" else 1
        elif word.startswith("{}", index):
            pieces = [_UNKNOWN]  # the placeholder `find -exec` and `xargs -I` fill in
            step = 2
        elif char in "?*+@!" and word.startswith("(", index + 1):
            pieces = [_ANY]  # an extended glob such as `@(a|b)` or `?(x)`
            step = _closing_paren(word, index + 1) + 1 - index
        elif char == "*":
            pieces = [_CROSS] if word.startswith("**", index) else [_STAR]
            step = 2 if word.startswith("**", index) else 1
        elif char == "?":
            pieces = [_ONE]
        elif char == "[" and (close := word.find("]", index + 2)) > 0:
            pieces = [_ONE]
            step = close + 1 - index
        elif char == "{" and (group := _brace_group(word, index)) is not None:
            pieces = [_CROSS if group[1] else _ANY]
            step = group[0] + 1 - index
        if len(results) * len(pieces) > _MAX_CANDIDATES:
            pieces = [_UNKNOWN]
        results = [result + piece for result in results for piece in pieces]
        index += step
    return results


def _msys_drive(text: str) -> str:
    """`/c/Users/...` is how Git Bash spells `C:/Users/...` on Windows."""
    if os.name != "nt":
        return text
    return _MSYS_DRIVE.sub(lambda match: f"{match.group(1).upper()}:", text, count=1)


def _wildcard_match(pattern: str, text: str) -> bool:
    """`_STAR` matches any run, `_ONE` one character, anything else itself.

    The two-pointer form keeps only the last star, so a word of a thousand `$v?` stays linear
    in practice and quadratic at worst; a regular expression of `.*.` runs backtracks without
    bound, and a slow decision is an allowance once the hook times out.
    """
    p = t = 0
    star = -1
    resume = 0
    while t < len(text):
        if p < len(pattern) and pattern[p] == _STAR:
            star, resume = p, t
            p += 1
        elif p < len(pattern) and pattern[p] in (_ONE, text[t]):
            p += 1
            t += 1
        elif star >= 0:
            p = star + 1
            resume += 1
            t = resume
        else:
            return False
    while p < len(pattern) and pattern[p] == _STAR:
        p += 1
    return p == len(pattern)


@dataclass(frozen=True)
class _Glob:
    """One path component of a word: literal characters and the shell's wildcards."""

    pattern: str
    dotless: bool  # a glob at the start does not match a leading dot
    literal: bool  # holds a character to anchor on

    def matches(self, name: str) -> bool:
        if self.dotless and name.startswith("."):
            return False
        return _wildcard_match(self.pattern, name.casefold())


def _component(text: str) -> _Glob | None:
    """The matcher for one component; ``None`` for one that can span separators."""
    if _CROSS in text:
        return None
    out: list[str] = []
    for char in text:
        if char in (_STAR, _ANY, _UNKNOWN):
            if not out or out[-1] != _STAR:
                out.append(_STAR)
        elif char == _ONE:
            out.append(_ONE)
        else:
            out.append(char.casefold())
    literal = any(char not in _MARKERS for char in text)
    return _Glob("".join(out), text[:1] in (_STAR, _ONE), literal)


def _walk(matchers: list[_Glob | None], remaining: tuple[str, ...]) -> bool:
    """Whether components matched against the root's remaining parts reach the root.

    Ending above the root is not reaching it: `~/.cache/*.json` and `du -sh ~/.c*` name the
    directory that holds the cache, not the cache.
    """
    for position, matcher in enumerate(matchers):
        if position >= len(remaining) or matcher is None:
            return True
        if not matcher.matches(remaining[position]):
            return False
    return len(matchers) >= len(remaining)


def _unanchored(components: list[str], scope: _Scope) -> bool:
    """A word whose start is unknown: does a component with a literal character name the root?

    Only the root's own name counts. `node_modules/.cache/*` under a directory the guard does
    not know could be anything; a wildcard where the cache's name would go is not a hint of it.
    """
    for component in components:
        if component in ("", ".", ".."):
            continue
        matcher = _component(component)
        if matcher is not None and matcher.literal and matcher.matches(scope.root.name):
            return True
    return False


def _off_the_drives(path: Path) -> bool:
    """A Windows path left on a share or a device: the guard cannot compare it with the root."""
    return os.name == "nt" and not is_drive_path(path)


def _text_hints(text: str, scope: _Scope) -> bool:
    """Whether a path the guard could not canonicalise still points at the cache by its text:
    a hint of the cache, or an 8.3 alias (`HUNGRY~1`) that could hide one."""
    normalized = text.replace("\\", "/").casefold()
    return re.search(r"~\d", text) is not None or any(
        pattern.search(normalized) for pattern in scope.hints
    )


def _anchored(directory: Path, components: list[str], scope: _Scope) -> bool:
    """A word whose literal part ends at ``directory``: can the rest of it reach the root?"""
    rest = [component for component in components if component not in ("", ".")]
    if ".." in rest:
        # `x/*/../y` is `x/y` for any directory `*` matched; a link it matched could lead
        # elsewhere, so the part after the last `..` is also judged on its own.
        stack: list[str] = []
        for component in rest:
            if component != "..":
                stack.append(component)
            elif stack:
                stack.pop()
            else:
                directory = directory.parent
        last = len(rest) - 1 - rest[::-1].index("..")
        return _anchored(directory, stack, scope) or _unanchored(rest[last + 1 :], scope)
    if _is_within(directory, scope.root):
        return True
    if not _is_within(scope.root, directory):
        return False
    remaining = scope.root.parts[len(directory.parts) :]
    return _walk([_component(component) for component in rest], remaining)


def _is_rooted(text: str) -> bool:
    return text[:1] in ("/", "\\") or Path(text).is_absolute()


def _word_path(text: str, *, cwd: Path) -> Path:
    """The path a word names; a quoted leading `~` stays a directory called `~`."""
    if text.startswith(_LITERAL_TILDE):
        return canonical_path(Path("~" + text[1:]), cwd=cwd, expand_home=False)
    return canonical_path(Path(text), cwd=cwd)


def _literal_reaches(text: str, scope: _Scope) -> bool:
    if not text:
        return False
    for candidate in dict.fromkeys((text, _msys_drive(text))):
        rooted = _is_rooted(candidate)
        for base in [scope.cwd] if rooted else scope.cwds:
            _spend()
            try:
                path = _word_path(candidate, cwd=base)
            except (OSError, RuntimeError, ValueError):
                continue
            if _is_within(path, scope.root):
                return True
            if _off_the_drives(path) and _text_hints(candidate, scope):
                return True
        if not rooted and scope.unknown_cwd and _unanchored(_components(candidate), scope):
            return True
    return False


def _components(text: str) -> list[str]:
    return _SEPARATORS.split(text)


def _candidate_reaches(text: str, scope: _Scope) -> bool:
    if not any(marker in text for marker in _MARKERS):
        return _literal_reaches(text, scope)
    if text.startswith(_UNKNOWN):
        return _unanchored(_components(text), scope)
    components = _components(_msys_drive(text))
    literal = 0
    while literal < len(components) and not any(m in components[literal] for m in _MARKERS):
        literal += 1
    prefix = "/".join(components[:literal])
    if not prefix and text[:1] in ("/", "\\"):
        prefix = "/"
    elif _DRIVE.fullmatch(prefix):
        prefix += "/"
    rooted = bool(prefix) and _is_rooted(prefix)
    for base in [scope.cwd] if rooted else scope.cwds:
        _spend()
        try:
            directory = _word_path(prefix or ".", cwd=base)
        except (OSError, RuntimeError, ValueError):
            continue
        if _anchored(directory, components[literal:], scope):
            return True
        if _off_the_drives(directory) and _text_hints(text, scope):
            return True
    return not rooted and scope.unknown_cwd and _unanchored(components, scope)


def _glob_markers(pattern: str) -> str:
    """A file-search glob in marker form, for a tool whose wildcards also match dot files."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        step = 1
        piece = char
        if pattern.startswith("**", index):
            piece, step = _CROSS, 2
        elif char in "*?":
            piece = _ANY
        elif char == "[" and (close := pattern.find("]", index + 2)) > 0:
            piece, step = _ANY, close + 1 - index
        elif char == "{" and (group := _brace_group(pattern, index)) is not None:
            piece, step = (_CROSS if group[1] else _ANY), group[0] + 1 - index
        out.append(piece)
        index += step
    return "".join(out)


def _globby(component: str) -> bool:
    """Whether a component of a device glob holds a wildcard; `?` there is the device prefix's."""
    braces = "{" in component and ("," in component or ".." in component)
    return "*" in component or "[" in component or braces


def _reduce_device_glob(pattern: str, *, base: Path) -> str | None:
    """A `\\\\?\\...`, `//?/...` or share glob with its literal head reduced to a drive path.

    `//?/GLOBALROOT/??/C:/Users/x/.cache/*/github/**` and `//?/Volume{...}/...` are local
    drives under other names; ``None`` when the head does not come down to one.
    """
    components = _SEPARATORS.split(pattern)
    head = 0
    while head < len(components) and not _globby(components[head]):
        head += 1
    try:
        reduced = canonical_path(Path("\\".join(components[:head])), cwd=base)
    except (OSError, RuntimeError, ValueError):
        return None
    if not is_drive_path(reduced):
        return None
    return "/".join([reduced.as_posix(), *components[head:]])


def glob_reaches(pattern: str, *, base: Path, root: Path) -> bool:
    """Whether a Glob ``pattern``, relative to ``base`` unless absolute, can match in ``root``.

    The literal part of the pattern is resolved like any path; the components after it are
    matched against the root's remaining components, so ``~/.cache/hungry-*/**`` and
    ``.cache/*crab/**`` from the home directory reach a default cache, ``src/**`` does not.
    """
    text = pattern
    if os.name == "nt" and pattern.startswith(("\\\\", "//", "\\??\\")):
        reduced = _reduce_device_glob(pattern, base=base)
        if reduced is None:
            return True  # a share or device the guard cannot place: treated as reaching it
        text = reduced
    if text.startswith("~"):
        separator = _SEPARATORS.search(text)
        end = separator.start() if separator else len(text)
        text = str(_expand_home(Path(text[:end]))) + text[end:]
    components = _components(_glob_markers(text))
    literal = 0
    while literal < len(components) and not any(m in components[literal] for m in _MARKERS):
        literal += 1
    prefix = "/".join(components[:literal])
    if not prefix and text[:1] in ("/", "\\"):
        prefix = "/"
    elif _DRIVE.fullmatch(prefix):
        prefix += "/"
    directory = canonical_path(Path(prefix or "."), cwd=base)
    scope = _Scope(root=root, cwd=base, home=base)
    return _anchored(directory, components[literal:], scope)


def _path_words(tokens: list[str]) -> list[str]:
    """The texts in a command that can name a path: arguments, option and assignment values."""
    words: list[str] = []
    for token in tokens:
        if _is_punctuation(token):
            continue
        word = token.lstrip("(").rstrip(")")
        assignment = _ASSIGNMENT_WORD.match(word)
        if assignment:
            value = assignment.group(2)
            words.extend([value, *value.split(":")])
        elif word.startswith("-"):
            if "=" in word:
                words.append(word.split("=", 1)[1])
        else:
            words.append(word)
    return words


def _reaches_cache(
    command: str,
    *,
    root: Path,
    cwd: Path,
    variables: dict[str, list[str] | None],
    depth: int,
) -> bool:
    if _ENV_CACHE_REF.search(command) or _DEFAULT_CACHE_REF.search(command):
        return True
    if names_root(command, root):
        return True
    text, inner = _extract_substitutions(command)
    # An unparsable command is still judged, word by whitespace-separated word: a quoting
    # trick that `shlex` reads differently from the shell must not hide a cache path.
    tokens = _shell_tokens(text) or _shell_tokens(command) or text.split()
    scope = _build_scope(tokens, root=root, cwd=cwd, variables=variables)
    for word in _path_words(tokens):
        if any(_candidate_reaches(c, scope) for c in _expand_word(word, scope, inner)):
            return True
    return depth < _MAX_DEPTH and any(
        _reaches_cache(group, root=root, cwd=cwd, variables=scope.variables, depth=depth + 1)
        for group in inner
    )


def _mentions_cache(command: str, *, root: Path, cwd: Path) -> bool:
    """Whether the command can touch the cache once the shell has built its words."""
    return _reaches_cache(command, root=root, cwd=cwd, variables={}, depth=0)


# --- the hint rule --------------------------------------------------------------------------
#
# The scope gate models plain variables, `~`, globs, assignments and `cd`. It does not model a
# nested shell, parameter operators, arrays, `$'...'`, brace expansion or substitutions, and it
# does not try to. A command that carries a hint of the cache *and* uses one of those is refused
# outright; a command with no hint keeps the scope gate's decision.

_SHELL_PROGRAMS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "busybox", "pwsh", "powershell"}
    | {"cmd", "eval"}
)
# Commands that run a program on paths the guard never sees.
_PATH_RUNNERS = frozenset({"xargs", "parallel", "-exec", "-execdir", "-ok", "-okdir"})
_BASH_UNMODELLED = re.compile(
    r"\$\{(?![A-Za-z_][A-Za-z0-9_]*\})"  # `${x%y}`, `${x:0:3}`, `${a[0]}`, `${!x}`, `${#x}`
    r"|\$[0-9@*#?$!-]"  # positional and special parameters
    r"|\$['\"]"  # `$'...'`, `$"..."`
    r"|\$\(|`|[<>]\("  # command and process substitution
    r"|(?:^|[\s;&|(])[A-Za-z_][A-Za-z0-9_]*\+?=\("  # an array assignment
    r"|\b(?:declare|typeset|local|readonly)\s+-\w*[aA]|\b(?:mapfile|readarray)\b|\bread\s+-\w*a"
    r"|\b(?:CDPATH|IFS)\b"
)
_HOME_SPELLING = r"(?:~|\$\{?home\}?|\$\{?env:(?:userprofile|home)\}?|%userprofile%|%home%)"
# PowerShell expressions: groups and subexpressions, script blocks (not `${name}`), `+`,
# format, join, replace and split operators, static method calls, the escape character, and the
# cmdlets that build or evaluate a path.
_POWERSHELL_UNMODELLED = re.compile(
    r"[()+`]|(?<!\$)\{|::"
    r"|(?<![\w-])-[ci]?(?:f|join|replace|split)(?![\w-])"
    r"|\b(?:join-path|split-path|resolve-path|convert-path|invoke-expression|iex"
    r"|invoke-command|icm)\b",
    re.IGNORECASE,
)
_QUOTED_HEREDOC = re.compile(r"<<-?[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\(\w+))")


def _homes(cwd: Path) -> list[Path]:
    """The home directory as Python sees it and as `$HOME` names it, when they differ."""
    homes = [canonical_path(Path.home(), cwd=cwd)]
    env_home = os.environ.get("HOME")
    if env_home:
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            homes.append(canonical_path(Path(_msys_drive(env_home)), cwd=cwd))
    return list(dict.fromkeys(homes))


def _hint_patterns(root: Path, homes: list[Path]) -> list[re.Pattern[str]]:
    """What in a command's text points at the cache: its name, and its parent under home."""
    patterns = [re.compile(re.escape(root.name.casefold()))]
    if root.name.casefold() == "hungry-crab":
        patterns.append(re.compile(r"hungry-"))  # not plain `hungry`: this repo is HungryCrab
    for home in homes:
        if root.parent == home or home not in root.parent.parents:
            continue
        relative = re.escape(root.parent.relative_to(home).as_posix().casefold())
        spellings = [_HOME_SPELLING, re.escape(home.as_posix().casefold())]
        if home.drive[:1].isalpha() and home.drive.endswith(":"):
            # Git Bash's `/c/Users/...` for `C:/Users/...`.
            spellings.append(re.escape(f"/{home.drive[0]}{home.as_posix()[2:]}".casefold()))
        for spelling in spellings:
            patterns.append(re.compile(spelling + r"/+" + relative + r"(?![a-z0-9_.-])"))
    return patterns


def _without_quoted_heredocs(command: str) -> str:
    """The command without the bodies of `<<'EOF'` here-documents, which are literal text."""
    out: list[str] = []
    position = 0
    for match in _QUOTED_HEREDOC.finditer(command):
        if match.start() < position:
            continue
        delimiter = match.group(1) or match.group(2) or match.group(3)
        body = command.find("\n", match.end())
        if body < 0:
            continue
        closing = re.compile(r"^\t*" + re.escape(delimiter) + r"[ \t]*$", re.MULTILINE)
        end = closing.search(command, body + 1)
        out.append(command[position : body + 1])
        position = end.start() if end else len(command)
    out.append(command[position:])
    return "".join(out)


def _program_names(tokens: list[str]) -> set[str]:
    return {Path(token.strip("();")).name.casefold().removesuffix(".exe") for token in tokens}


def _hinted(
    command: str, tokens: list[str], *, root: Path, homes: list[Path], powershell: bool = False
) -> bool:
    # A commit message written through `$(cat <<'EOF' ... EOF)` may name the cache; a quoted
    # here-document is literal text, unless a shell reads it as a script.
    shell_reads_it = bool(_program_names(tokens) & _SHELL_PROGRAMS)
    if not (shell_reads_it or powershell):
        stripped = _without_quoted_heredocs(command)
        if stripped != command:
            command, tokens = stripped, _shell_tokens(stripped) or stripped.split()
    # The raw text and the words as the shell reads them: `hungry\-crab`, `hungry`-crab` and
    # `.c''ache` hide nothing from the second.
    normalized = f"{command}\n{' '.join(tokens)}".replace("\\", "/").casefold()
    if powershell:
        normalized = normalized.replace("`", "")
    if any(pattern.search(normalized) for pattern in _hint_patterns(root, homes)):
        return True
    if not powershell:
        return False
    # `Join-Path $HOME .cache ...`, `$HOME + '\.cache\hun' + 'gry-crab'`: PowerShell builds the
    # path from separate strings, so the parent's name as a component anywhere, beside a home
    # spelling anywhere, is a hint.
    # Only toward the cache: `.cache` alone, or followed by the start of the cache's name
    # (`'\.cache\hun'`), not by another child (`'.cache\pip'`).
    if re.search(_HOME_SPELLING, normalized) is None:
        return False
    name = root.name.casefold()
    for home in homes:
        if home not in root.parent.parents:
            continue
        first = re.escape(root.parent.relative_to(home).parts[0].casefold())
        pattern = rf"(?:^|[/'\"\s(,]){first}(/[^/'\"\s),]*)?(?=$|[/'\"\s),])"
        for match in re.finditer(pattern, normalized):
            child = match.group(1)
            if child is None or name.startswith(child[1:]):
                return True
    return False


def _has_brace_expansion(text: str) -> bool:
    """`{a,b}` or `{1..3}` anywhere, found in one pass: a `{` whose run up to `}` holds no blank."""
    opened = -1
    alternative = False
    for index, char in enumerate(text):
        if char == "{":
            opened, alternative = index, False
        elif opened < 0:
            continue
        elif char == "}":
            if alternative:
                return True
            opened = -1
        elif char in " \t\r\n":
            opened = -1
        elif char == "," or (char == "." and text.startswith("..", index)):
            alternative = True
    return False


def _bash_unmodelled(command: str, tokens: list[str]) -> bool:
    if _BASH_UNMODELLED.search(command) or _has_brace_expansion(command):
        return True
    return bool(_program_names(tokens) & (_SHELL_PROGRAMS | _PATH_RUNNERS))


def _powershell_unmodelled(command: str, tokens: list[str]) -> bool:
    if _POWERSHELL_UNMODELLED.search(command):
        return True
    return bool(_program_names(tokens) & _SHELL_PROGRAMS)


def _unmodelled_reason(
    command: str, *, root: Path, cwd: Path, powershell: bool = False
) -> str | None:
    """A refusal for a cache-hinting command that uses a construct the guard does not model."""
    parsed = _powershell_tokens(command) if powershell else _shell_tokens(command)
    tokens = parsed or command.split()
    if not _hinted(command, tokens, root=root, homes=_homes(cwd), powershell=powershell):
        return None
    unmodelled = _powershell_unmodelled if powershell else _bash_unmodelled
    if not unmodelled(command, tokens):
        return None
    return (
        "command points at the prey cache through a construct the guard does not model "
        "(AGENTS.md rule 3)"
    )


# --- PowerShell -----------------------------------------------------------------------------
#
# Claude Code's PowerShell tool takes a `command` like the Bash tool. PowerShell keeps
# backslashes, quotes with `'...'` (doubled `''`) and `"..."` (backtick escapes), runs
# `$(...)`, `@(...)`, `(...)` and `{...}` script blocks, and starts programs with `&` and `.`.
# A cache-touching PowerShell command is allowed only as plain read cmdlets with literal
# arguments, optionally piped into a few filters.

# PowerShell reads typographic quotes and dashes as their ASCII forms.
_POWERSHELL_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
    }
)
_POWERSHELL_READERS = frozenset(
    {
        "get-content",
        "gc",
        "cat",
        "type",
        "get-childitem",
        "gci",
        "ls",
        "dir",
        "select-string",
        "sls",
        "test-path",
        "get-item",
        "gi",
    }
)
_POWERSHELL_FILTERS = frozenset(
    {"select-string", "sls", "select-object", "select", "measure-object", "measure"}
)
_POWERSHELL_CD_COMMANDS = frozenset({"cd", "chdir", "sl", "set-location", "pushd", "push-location"})
_POWERSHELL_ASSIGNMENT = re.compile(r"^\$(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\+?=(.*)$", re.DOTALL)
_POWERSHELL_FOREACH = re.compile(r"^\s*\$([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.*)$", re.I | re.S)
# Anything that makes PowerShell evaluate rather than read: variables, subexpressions, arrays,
# script blocks and the escape character.
_POWERSHELL_EVALUATION = re.compile(r"[$@(){}`]")
_GLOB_CHARS = frozenset("*?[")


def _powershell_quote_end(text: str, start: int) -> int:
    """Index of the `'` closing the single-quoted string at ``start``, or -1."""
    index = start + 1
    while index < len(text):
        if text[index] == "'":
            if text.startswith("'", index + 1):
                index += 2
                continue
            return index
        index += 1
    return -1


def _powershell_groups(command: str) -> tuple[str, list[str]]:
    """Replace `$(...)`, `@(...)`, `(...)` and `{...}` with a placeholder; return their text."""
    out: list[str] = []
    groups: list[str] = []
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "`":
            out.append(command[index : index + 2])
            index += 2
        elif char == "'" and not in_double:
            end = _powershell_quote_end(command, index)
            end = len(command) - 1 if end < 0 else end
            out.append(command[index : end + 1])
            index = end + 1
        elif char == '"':
            in_double = not in_double
            out.append(char)
            index += 1
        elif char == "$" and command.startswith("{", index + 1):
            end = command.find("}", index + 2)  # `${env:NAME}` is a variable, not a block
            end = len(command) - 1 if end < 0 else end
            out.append(command[index : end + 1])
            index = end + 1
        elif (char in "$@" and command.startswith("(", index + 1)) or (
            char in "({" and not in_double
        ):
            opening = index + 1 if char in "$@" else index
            end = _closing_paren(command, opening)
            groups.append(command[opening + 1 : end])
            out.append(_SUBSTITUTION)
            index = end + 1
        else:
            out.append(char)
            index += 1
    return "".join(out), groups


_POWERSHELL_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "n": "\n"}
_POWERSHELL_ESCAPES |= {"r": "\r", "t": "\t", "v": "\v"}


def _powershell_unescape(text: str) -> str:
    """Resolve PowerShell's backtick escapes: `` `x `` is `x`, `` `n `` a newline."""
    return re.sub(r"`(.)", lambda m: _POWERSHELL_ESCAPES.get(m.group(1), m.group(1)), text)


def _powershell_tokens(command: str) -> list[str] | None:
    """PowerShell words with quotes removed and backslashes kept; ``None`` if a quote is open."""
    tokens: list[str] = []
    word: list[str] | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if char in " \t\r\n" or char in _OPERATOR_CHARS:
            if word is not None:
                tokens.append("".join(word))
                word = None
            if char in _OPERATOR_CHARS:
                end = index
                while end < len(command) and command[end] in _OPERATOR_CHARS:
                    end += 1
                tokens.append(command[index:end])
                index = end
            else:
                if char in "\r\n":
                    tokens.append(";")
                index += 1
            continue
        word = [] if word is None else word
        if char == "'":
            end = _powershell_quote_end(command, index)
            if end < 0:
                return None
            word.append(command[index + 1 : end].replace("''", "'"))
            index = end + 1
        elif char == '"':
            end = index + 1
            while end < len(command) and command[end] != '"':
                end += 2 if command[end] == "`" else 1
            if end >= len(command):
                return None
            word.append(_powershell_unescape(command[index + 1 : end]))
            index = end + 1
        elif char == "`":
            # `hungry`-crab` is `hungry-crab` to PowerShell; the allowlist still refuses the
            # escape character itself in a cache-touching command.
            word.append(_powershell_unescape(command[index : index + 2]))
            index += 2
        else:
            word.append(char)
            index += 1
    if word is not None:
        tokens.append("".join(word))
    return tokens


def _powershell_target(args: list[str]) -> str:
    """The directory `Set-Location` and its aliases go to: `-Path`, `-LiteralPath`, positional."""
    for index, arg in enumerate(args):
        lowered = arg.casefold()
        if lowered.startswith("-") and ":" in arg:
            name, value = arg.split(":", 1)
            if "-path".startswith(name.casefold()) or "-literalpath".startswith(name.casefold()):
                return value
        elif lowered.startswith("-"):
            if index + 1 < len(args) and lowered in {"-path", "-literalpath", "-lp", "-p"}:
                return args[index + 1]
        else:
            return arg
    return "~"


def _build_powershell_scope(
    tokens: list[str],
    groups: list[str],
    *,
    root: Path,
    cwd: Path,
    variables: dict[str, list[str] | None],
) -> _Scope:
    home = canonical_path(Path.home(), cwd=cwd)
    scope = _Scope(
        root=root,
        cwd=cwd,
        home=home,
        variables=dict(variables),
        cwds=[cwd],
        homes=_homes(cwd),
        hints=_hint_patterns(root, _homes(cwd)),
        powershell=True,
    )
    commands = _simple_commands(tokens)
    for words in commands:
        for index, word in enumerate(words):
            match = _POWERSHELL_ASSIGNMENT.match(word)
            if match and match.group(2):
                _assign(scope, match.group(1).casefold(), match.group(2))
            elif match and index + 1 < len(words):
                _assign(scope, match.group(1).casefold(), words[index + 1])
            elif word.startswith("$") and index + 2 < len(words) and words[index + 1] == "=":
                name = _POWERSHELL_ASSIGNMENT.match(f"{word}=")
                if name:
                    _assign(scope, name.group(1).casefold(), words[index + 2])
    for group in groups:
        loop = _POWERSHELL_FOREACH.match(group)
        if loop:
            for value in re.split(r"[\s,]+", loop.group(2).strip()):
                if value:
                    _assign(scope, loop.group(1).casefold(), value)
    for words in commands:
        if words and words[0].casefold() in _POWERSHELL_CD_COMMANDS:
            _change_directory(scope, [_powershell_target(words[1:])])
    return scope


def _powershell_path_words(tokens: list[str]) -> list[str]:
    words: list[str] = []
    for token in tokens:
        if _is_punctuation(token) or token in {"=", "+="}:
            continue
        if token.startswith("-"):
            if ":" in token:
                words.append(token.split(":", 1)[1])  # `-Path:C:\x`
            continue
        assignment = _POWERSHELL_ASSIGNMENT.match(token)
        value = assignment.group(2) if assignment else token
        words.extend(piece for piece in value.split(",") if piece)  # `a,b` is an array
    return words


def _powershell_reaches(
    command: str,
    *,
    root: Path,
    cwd: Path,
    variables: dict[str, list[str] | None],
    depth: int,
) -> bool:
    if _ENV_CACHE_REF.search(command) or _DEFAULT_CACHE_REF.search(command):
        return True
    if names_root(command, root):
        return True
    text, inner = _powershell_groups(command)
    tokens = _powershell_tokens(text) or _powershell_tokens(command) or text.split()
    if os.name != "nt":
        tokens = [token.replace("\\", "/") for token in tokens]  # pwsh reads both separators
    scope = _build_powershell_scope(tokens, inner, root=root, cwd=cwd, variables=variables)
    for word in _powershell_path_words(tokens):
        if any(_candidate_reaches(c, scope) for c in _expand_word(word, scope, inner)):
            return True
    return depth < _MAX_DEPTH and any(
        _powershell_reaches(group, root=root, cwd=cwd, variables=scope.variables, depth=depth + 1)
        for group in inner
    )


def _is_pattern_parameter(name: str) -> bool:
    """`-Pattern` or an unambiguous abbreviation of it (`-Path` shares `-pat`)."""
    lowered = name.casefold()
    return len(lowered) >= 5 and "-pattern".startswith(lowered)


def _powershell_read_only(command: str, tokens: list[str]) -> bool:
    """Read cmdlets with literal arguments, optionally piped into `Select-*`/`Measure-Object`.

    No variable, subexpression, script block, escape, call operator (`&`), dot-sourcing,
    redirection or command separator; no wildcard outside a `-Pattern` value; the cmdlet is
    named exactly, never by a path that could be a program of the prey's.
    """
    if _POWERSHELL_EVALUATION.search(command):
        return False
    stages: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            stages.append([])
        elif _is_punctuation(token):
            return False
        else:
            stages[-1].append(token)
    for position, stage in enumerate(stages):
        allowed = _POWERSHELL_READERS if position == 0 else _POWERSHELL_FILTERS
        if not stage or stage[0].casefold() not in allowed:
            return False
        previous = ""
        for arg in stage[1:]:
            name, value = arg.split(":", 1) if arg.startswith("-") and ":" in arg else ("", arg)
            pattern = _is_pattern_parameter(name or previous)
            if not pattern and any(char in _GLOB_CHARS for char in value):
                return False
            previous = arg
    return True


def _overflow_reason(command: str, *, root: Path, cwd: Path, powershell: bool) -> str | None:
    """The decision for a command too large to resolve in time: by its text alone.

    Refused when it hints at the cache or runs inside it; otherwise it never names the cache
    and is let through, as it would most likely have been anyway.
    """
    parsed = _powershell_tokens(command) if powershell else _shell_tokens(command)
    tokens = parsed or command.split()
    if _is_within(cwd, root) or _hinted(
        command, tokens, root=root, homes=_homes(cwd), powershell=powershell
    ):
        return "command names the prey cache and is too large to judge in time (AGENTS.md rule 3)"
    return None


def _powershell_guard_reason(command: str, *, root: Path, cwd: Path) -> str | None:
    command = command.translate(_POWERSHELL_TRANSLATION)
    unmodelled = _unmodelled_reason(command, root=root, cwd=cwd, powershell=True)
    if unmodelled is not None:
        return unmodelled
    touches = _within_budget(
        lambda: _powershell_reaches(command, root=root, cwd=cwd, variables={}, depth=0)
    )
    if touches is None:
        return _overflow_reason(command, root=root, cwd=cwd, powershell=True)
    if not touches:
        return None
    if _LINE_BREAK.search(command):
        return "cache-touching multi-line command is not established read-only (AGENTS.md rule 3)"
    tokens = _powershell_tokens(command)
    if tokens is None:
        return "cache-touching command could not be parsed safely (AGENTS.md rule 3)"
    if not _powershell_read_only(command, tokens):
        return (
            "cache-touching PowerShell command is not a read cmdlet with literal arguments "
            "(AGENTS.md rule 3)"
        )
    return None


def _program(token: str) -> str:
    return Path(token).name.casefold().removesuffix(".exe")


def _program_token_points_into_cache(token: str, *, root: Path, cwd: Path) -> bool:
    """Return whether an explicit executable token can resolve inside the prey cache."""
    if _ENV_CACHE_REF.search(token) or _DEFAULT_CACHE_REF.search(token):
        return True
    if _ASSEMBLY.search(token):
        # `~/.cache/hungry-*/.../bin/cat` ends in a reader's name, but the shell picks the
        # program; in a cache-touching command an assembled program token is not judged.
        return True
    if names_root(token, root):
        return True

    # A bare command name such as ``cat`` is resolved by the trusted host PATH. Only explicit
    # path-shaped program tokens are resolved against cwd here; otherwise a cache cwd would make
    # every ordinary reader look like ``<cache>/cat`` even though the shell does not resolve it
    # that way.
    if "/" not in token and "\\" not in token:
        return False
    try:
        return _is_within(canonical_path(Path(token), cwd=cwd), root)
    except (OSError, RuntimeError, ValueError):
        # This is already a cache-touching command. If an explicit executable path cannot be
        # classified, fail closed rather than applying the basename allowlist.
        return True


def _is_clone_root(directory: Path, root: Path) -> bool:
    """Whether ``directory`` is a clone ``crab catch`` made, with a ``.git`` it wrote itself.

    A prey can commit an ordinary directory that git accepts as a repository (``tools.git/``
    with its own ``HEAD``, ``config`` and ``objects/``); git refuses only ``.git`` path
    components, so the clone root's ``.git`` directory is the one repository in the cache whose
    config the prey did not supply. A gitfile, a link or a junction there is not that.
    """
    try:
        parts = directory.relative_to(root).parts
    except ValueError:
        return False
    if len(parts) < 4 or parts[0] not in _CLONE_FORGES or parts[-1] != "repo":
        return False
    if parts[0] == "github" and len(parts) != 4:
        return False
    try:
        info = os.lstat(directory / ".git")
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not is_reparse_point(info)


def _git_is_read_only(tokens: list[str], *, root: Path, cwd: Path) -> bool:
    # git opens the repository it discovers from its working directory: the event cwd, moved by
    # every `-C` in turn. That repository's config decides which programs `status`, `diff`,
    # `log -p` or `blame` start (`core.fsmonitor`, `diff.external`, textconv drivers), so it
    # has to be a clone root. `--git-dir`, `--work-tree`, `--namespace` and every other option
    # before the subcommand are refused rather than interpreted.
    directory = cwd
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-C":
            if index + 1 >= len(tokens) or _ASSEMBLY.search(tokens[index + 1]):
                return False
            if tokens[index + 1]:
                try:
                    target = _msys_drive(tokens[index + 1])  # Git Bash's `/c/...`, as for `cat`
                    directory = canonical_path(Path(target), cwd=directory)
                except (OSError, RuntimeError, ValueError):
                    return False
            index += 2
            continue
        if token in _GIT_GLOBAL_SAFE_FLAGS:
            index += 1
            continue
        if token.startswith("-"):
            return False
        break

    if index >= len(tokens) or not _is_clone_root(directory, root):
        return False
    subcommand = tokens[index].casefold()
    if subcommand not in _READ_ONLY_GIT_SUBCOMMANDS:
        return False

    args = tokens[index + 1 :]
    if subcommand == "symbolic-ref":
        # `symbolic-ref HEAD refs/heads/x` writes and `-d HEAD` deletes; only the one-name read
        # form `symbolic-ref [-q] [--short] <name>` is a read.
        names = [arg for arg in args if not arg.startswith("-")]
        flags = [arg for arg in args if arg.startswith("-")]
        return len(names) == 1 and set(flags) <= {"-q", "--quiet", "--short"}
    return not any(_git_arg_is_dangerous(arg) for arg in args)


def _git_arg_is_dangerous(arg: str) -> bool:
    if arg == "-c":
        return True
    if arg.startswith("--"):
        name = arg[2:].split("=", 1)[0]
        return bool(name) and any(option.startswith(name) for option in _GIT_DANGEROUS_LONG_OPTIONS)
    if arg.startswith("-"):
        # A short cluster: `-lOsh` is `-l -O sh`. A value attached to an earlier flag that
        # happens to hold an `O` is refused too; the guard does not read each flag's grammar.
        return any(char in _GIT_DANGEROUS_SHORT_OPTIONS for char in arg[1:])
    return False


def _simple_read_only(tokens: list[str], *, root: Path, cwd: Path) -> bool:
    if not tokens or _ASSIGNMENT.match(tokens[0]):
        # Environment prefixes can change executable resolution (PATH) or inject code into an
        # otherwise allowed reader (for example LD_PRELOAD). A cache-touching command therefore
        # cannot carry assignment prefixes unless a future audited subset is introduced.
        return False

    program_token = tokens[0]
    if _program_token_points_into_cache(program_token, root=root, cwd=cwd):
        return False

    command = _program(program_token)
    args = tokens[1:]
    if command == "git":
        return _git_is_read_only(tokens, root=root, cwd=cwd)
    if command not in _READ_ONLY_COMMANDS:
        return False
    return command != "rg" or _rg_is_read_only(args)


def _rg_is_read_only(args: list[str]) -> bool:
    """No option that makes ripgrep start a program, spelled out or in a cluster of short flags.

    A short cluster such as ``-iz`` carries ``-z``; one that attaches a value (``-ezip``) is
    refused too, which is a false refusal the guard prefers to reading the value's grammar.
    """
    for arg in args:
        if any(arg == option or arg.startswith(f"{option}=") for option in _RG_SPAWNING_OPTIONS):
            return False
        if arg.startswith("-") and not arg.startswith("--") and "z" in arg[1:]:
            return False
    return True


def guard_reason(
    command: str,
    *,
    root: Path | None = None,
    cwd: Path | None = None,
    shell: Shell = "bash",
) -> str | None:
    """Return why a shell command must be denied, or ``None`` when this guard allows it.

    Commands that do not mention the cache are outside this guard's scope. Once the cache is
    mentioned, the burden flips: the complete command shape must be one of the small read-only
    forms above. This deliberately prefers a false refusal over silently executing prey.
    ``shell`` is the grammar the command is written in: Bash, or PowerShell.
    """
    here = canonical_path(cwd or Path.cwd(), cwd=Path.cwd())  # no network for a share
    cache = canonical_path(root or cache_root(), cwd=here)
    if not command.strip():
        return None
    if shell == "powershell":
        return _powershell_guard_reason(command, root=cache, cwd=here)
    unmodelled = _unmodelled_reason(command, root=cache, cwd=here)
    if unmodelled is not None:
        return unmodelled
    touches = _within_budget(lambda: _mentions_cache(command, root=cache, cwd=here))
    if touches is None:
        return _overflow_reason(command, root=cache, cwd=here, powershell=False)
    if not touches:
        return None
    if _LINE_BREAK.search(command):
        # A line break separates commands exactly as `;` does, and `shlex` reads it as
        # whitespace: `cat <cache>/README.md` on one line and `python <cache>/setup.py` on the
        # next would otherwise be judged as one long `cat`.
        return "cache-touching multi-line command is not established read-only (AGENTS.md rule 3)"
    if _EXECUTION_SUBSTITUTION.search(command):
        return "cache-touching shell substitution is not established read-only (AGENTS.md rule 3)"

    tokens = _shell_tokens(command)
    if tokens is None:
        return "cache-touching command could not be parsed safely (AGENTS.md rule 3)"
    if any(_is_punctuation(token) for token in tokens):
        return (
            "cache-touching shell composition or redirection is not established read-only "
            "(AGENTS.md rule 3)"
        )
    if not _simple_read_only(tokens, root=cache, cwd=here):
        return "cache-touching command is not in the audited read-only allowlist (AGENTS.md rule 3)"
    return None


def _event_cwd(event: dict[object, object]) -> Path:
    process_cwd = Path.cwd().resolve(strict=False)
    raw = event.get("cwd")
    if not isinstance(raw, str):
        return process_cwd
    try:
        return canonical_path(Path(raw), cwd=process_cwd)
    except (OSError, RuntimeError, ValueError):
        return process_cwd


def event_guard_reason(event: object, *, root: Path | None = None) -> str | None:
    """Apply the shell decision to one PreToolUse event without trusting its shape."""
    if not isinstance(event, dict):
        return None
    tool_name = event.get("tool_name")
    if tool_name is not None and tool_name not in _SHELL_TOOLS:
        return None
    tool = "PowerShell" if tool_name == "PowerShell" else "Bash"

    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return f"{tool} tool call denied: the tool input could not be inspected."
    command = tool_input.get("command")
    if not isinstance(command, str):
        return f"{tool} tool call denied: the shell command could not be inspected."
    shell: Shell = "powershell" if tool == "PowerShell" else "bash"
    return guard_reason(command, root=root, cwd=_event_cwd(event), shell=shell)


def read_hook_event(stream: TextIO | None = None) -> object:
    """One ``PreToolUse`` event, decoded the way Claude Code writes it: JSON in UTF-8.

    The hook runs Python with ``-I``, which ignores ``PYTHONUTF8`` and ``PYTHONIOENCODING``, so
    a text read of a piped stdin on Windows decodes with the ANSI code page and turns every
    non-ASCII path into mojibake. The guards read the bytes instead. An invalid UTF-8 byte is
    kept as a surrogate escape, the form a POSIX path with that byte has, so one bad byte in a
    description cannot make the whole event unreadable and its command unjudged. Raises
    ``OSError`` or ``ValueError`` for input that cannot be read or parsed.
    """
    source = stream if stream is not None else sys.stdin
    buffer = getattr(source, "buffer", None)
    if buffer is None:
        return json.loads(source.read())
    data = buffer.read()
    encoding = json.detect_encoding(data)
    if encoding in ("utf-8", "utf-8-sig"):
        text = data.decode("utf-8", errors="surrogateescape").removeprefix("\ufeff")
    else:
        text = data.decode(encoding, errors="surrogatepass")
    return json.loads(text)


def main(*, stdin: TextIO | None = None) -> int:
    """Read one PreToolUse event from stdin; exit 2 when the command is denied."""
    try:
        event = read_hook_event(stdin)
    except (OSError, ValueError):
        # A transport failure — unreadable, undecodable or malformed input — does not establish
        # that a Bash command touched the cache. A traceback here would exit 1, which the agent
        # also reads as "allow", only louder. The live hook/protocol proof is tracked separately
        # from the decision logic in issue #83.
        return 0

    reason = event_guard_reason(event)
    if reason is None:
        return 0
    print(f"crab: refusing unsafe prey-cache command: {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
