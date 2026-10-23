"""Fail-closed shell guard for commands that touch Hungry Crab's prey cache.

The guard does not try to parse all shell grammar. Once a command references the prey cache,
it allows only a small audited set of read-only command shapes. Unknown cache-touching shapes
are denied rather than assumed safe.

A command references the cache when one of its words can become a path in it after the shell
has built the word — variables, `~` forms, globs, brace expansion, parameter defaults,
substitutions, a `cd` anywhere in the command, a script handed to a nested shell or `eval` — or
when a word names the directory that holds the cache. Ordinary commands that never come near
the cache stay out of scope, because the guard runs on every shell call. One decision runs on
one budget of work; a command the guard cannot parse, or cannot finish judging within that
budget, passes only when its text is plain and names nothing near the cache.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import itertools
import json
import os
import re
import socket
import stat
import sys
import time
import unicodedata
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TextIO, TypeVar

from .cache import cache_root
from .fs import is_reparse_point

Shell = Literal["bash", "powershell"]
# Claude Code's tools that run a `command` in a shell: Bash, PowerShell, and Monitor, which
# runs a bash script in the same environment as Bash; and the desktop app's terminal tool,
# which runs PowerShell or a login shell, so its command is judged both ways. Any other tool
# whose input carries a string `command` is judged as bash too, should a matcher send it
# here. A `cwd` in the input is where the command runs.
_TERMINAL_TOOLS = frozenset({"mcp__terminal__run_in_terminal"})
_SHELL_TOOLS = frozenset({"Bash", "PowerShell", "Monitor"}) | _TERMINAL_TOOLS
# Tools that write a file the path names: a prey file rewritten (a clone's `.git/config`,
# its Makefile) runs when an allowed read or a later command touches it.
_FILE_TOOLS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
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
# `\\host\share`, and the same through any device prefix: `\\?\UNC\`, `\\.\UNC\`, `//./UNC/`.
_UNC = re.compile(
    r"^(?:[\\/]{2}(?![?.][\\/])|(?:[\\/]{2}[?.][\\/]|\\\?\?\\)UNC[\\/])"
    r"([^\\/]+)[\\/]+([^\\/]+)(.*)$",
    re.I,
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0--1.ipv6-literal.net", "[::1]"})
_LOOPBACK = re.compile(r"127(?:\.[0-9]{1,3}){3}")  # every 127.x.y.z is this machine
# Past this many components a path is normalised, not resolved: resolving one on Windows takes
# time that grows with the square of its length.
_MAX_RESOLVED_COMPONENTS = 64
# Win32 drops trailing dots and spaces from each component and opens a directory's index
# stream (`::$INDEX_ALLOCATION`) as the directory itself.
_WIN32_STREAMS = ("::$index_allocation", ":$i30:$index_allocation")


def _win32_component(piece: str) -> str:
    while True:
        stripped = piece.rstrip(". ")
        lowered = stripped.casefold()
        suffix = next((s for s in _WIN32_STREAMS if lowered.endswith(s)), "")
        if not suffix:
            return stripped or piece
        piece = stripped[: -len(suffix)]


def _win32_trim(text: str) -> str:
    """The spelling Win32 opens: each component without trailing dots, spaces or stream."""
    pieces = re.split(r"([\\/])", text)
    return "".join(
        piece if piece in ("", ".", "..", "/", "\\") else _win32_component(piece)
        for piece in pieces
    )


def is_drive_path(path: Path) -> bool:
    """Whether a Windows path is on a drive letter, not a share or a device."""
    return _DRIVE_PATH.match(str(path)) is not None


def _windows_spelling(text: str, *, lexical: bool = False) -> tuple[str, bool]:
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
        if lexical:
            return text, False
        try:
            resolved = strip_device_prefix(str(Path(text).resolve(strict=False)))
        except (OSError, RuntimeError, ValueError):
            return text, False
        return (resolved, True) if _DRIVE_PATH.match(resolved) else (text, False)
    unc = _UNC.match(text)
    if unc:
        host, share, rest = unc.groups()
        local = _LOCAL_HOSTS | {os.environ.get("COMPUTERNAME", "localhost").casefold()}
        loopback = _LOOPBACK.fullmatch(host) is not None or host.casefold().rstrip(".") in local
        if loopback and re.fullmatch(r"[A-Za-z]\$", share):
            return f"{share[0]}:\\{rest.lstrip(chr(92) + '/')}", True
        return text, False
    if text[:2] in ("\\\\", "//") or text.startswith("\\??\\"):
        return text, False  # another device: a pipe, a console; nothing to open for a name
    return text, True


def canonical_path(
    path: Path, *, cwd: Path, expand_home: bool = True, lexical: bool = False
) -> Path:
    """The one spelling both guards compare: absolute, resolved, device prefix stripped.

    On Windows ``resolve()`` asks for the final path name of the longest existing prefix, which
    expands 8.3 short names (``HUNGRY~1``) and follows junctions; a device spelling is reduced
    to its drive path first (see ``_windows_spelling``), and a path on a share is normalised
    without asking the network. ``expand_home=False`` keeps a leading ``~`` as the directory
    of that name, which is what a program receives for a quoted ``'~/x'``. ``lexical=True``
    normalises the spelling without any filesystem call.
    """
    text = os.fspath(path)
    local = True
    if text.count("/") + text.count("\\") > _MAX_RESOLVED_COMPONENTS:
        lexical = True
    if os.name == "nt":
        if lexical:
            text = _win32_trim(text)
        text, local = _windows_spelling(text, lexical=lexical)
    candidate = _expand_home(Path(text)) if expand_home else Path(text)
    if not candidate.is_absolute():
        candidate = cwd / candidate
        if os.name == "nt" and not is_drive_path(cwd):
            local = False  # relative to a working directory on a share
    if lexical or not local:
        return Path(os.path.normpath(candidate))
    return candidate.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    """``path == root or root in path.parents`` for canonical paths, without building parents."""
    inner = os.path.normcase(str(path))
    outer = os.path.normcase(str(root))
    if inner == outer:
        return True
    return inner.startswith(outer if outer.endswith(("/", "\\")) else outer + os.sep)


# --- the budget -----------------------------------------------------------------------------
#
# A decision that outlasts the hook timeout lets the call proceed, so every decision runs on one
# budget. Steps count the work of reading and expanding a command — lexing, expansion, brace
# alternatives, nested scripts, matching — and resolutions count filesystem calls. Past the
# resolution budget a path is compared by its spelling alone; past the step budget the command
# is decided from its text (`_text_only_reason`), which refuses it when it points near the cache.

_STEP_BUDGET = 200_000  # a step is about a microsecond or two of work
_RESOLUTION_BUDGET = 256
_RESOLUTION_COST = 128  # a filesystem call, in steps
_COMPONENT_COST = 16  # and one more for each component it resolves
_NORMALISATION_COST = 8  # a path normalised without one
# The steps are counted, so a decision does not depend on the machine; this bound in seconds
# only catches work the steps do not foresee, well inside the hook's ten-second timeout.
_DEADLINE = 2.5
_T = TypeVar("_T")


@dataclass
class _Budget:
    steps: int = field(default_factory=lambda: _STEP_BUDGET)
    resolutions: int = field(default_factory=lambda: _RESOLUTION_BUDGET)
    deadline: float = field(default_factory=lambda: time.perf_counter() + _DEADLINE)
    paths: dict[tuple[str, str], Path] = field(default_factory=dict)
    homes: list[Path] | None = None
    lexical: int = 0  # spellings compared without asking the filesystem


_budget: ContextVar[_Budget | None] = ContextVar("crab_guard_budget", default=None)


class _BudgetExceededError(Exception):
    """The command needs more work than one decision may spend."""


class _UnparsableError(_BudgetExceededError):
    """The command, or a script inside it, cannot be read the way the shell reads it."""


def _spend(cost: int = 1) -> None:
    budget = _budget.get()
    if budget is not None:
        budget.steps -= cost
        if budget.steps < 0 or time.perf_counter() > budget.deadline:
            raise _BudgetExceededError


def _resolving() -> bool:
    """Whether paths are still resolved through the filesystem in this decision."""
    budget = _budget.get()
    return budget is None or budget.resolutions > 0


def _within_budget(decide: Callable[[], _T], *, deadline: float | None = None) -> _T | None:
    """``decide()``, or ``None`` when it would spend more than the budget or cannot parse.
    ``deadline`` is shared by the readings of one command (see `guard_reason`)."""
    budget = _Budget() if deadline is None else _Budget(deadline=deadline)
    token = _budget.set(budget)
    try:
        return decide()
    except (_BudgetExceededError, RecursionError):
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


# --- reading a bash command -------------------------------------------------------------------
#
# The lexer reads a command the way bash does where that decides which file a word names:
# quotes and escapes are removed, `$'...'` is decoded, a `#` at the start of a word opens a
# comment, a here-document's body is set aside, and every `$(...)`, `` `...` ``, `<(...)` and
# `$((...))` becomes a placeholder whose text is read as a command of its own. A command it
# cannot read is never split on whitespace instead: it is decided from its text alone.

_SUBSTITUTION = "\ue005"  # where `$(...)`, `<(...)` or a backtick group stood
_SPECIAL_DOLLARS = frozenset("$#?!-@*0123456789")
_MAX_NESTING = 24
_REDIRECTIONS = frozenset({"<", ">", ">>", ">|", "<>", "<&", ">&", "&>", "&>>", "<<", "<<<"})
_LEADING_KEYWORDS = frozenset(
    {"!", "{", "if", "then", "else", "elif", "do", "while", "until", "time", "command", "builtin"}
)


class _LexError(Exception):
    """The text leaves a quote, a substitution or a parameter expansion open."""


@dataclass
class _Heredoc:
    body: str
    quoted: bool
    command: int  # index into `_Lexed.commands` of the command it feeds


@dataclass
class _Lexed:
    tokens: list[str] = field(default_factory=list)  # words and operator runs, in order
    commands: list[list[str]] = field(default_factory=lambda: [[]])  # words per simple command
    groups: list[tuple[str, _Lexed]] = field(default_factory=list)  # substitutions, read
    heredocs: list[_Heredoc] = field(default_factory=list)
    active: list[str] = field(default_factory=list)  # the text the shell interprets
    arithmetic: bool = False  # `$((...))`, which evaluates the text of the values it names
    fed: list[bool] = field(default_factory=lambda: [False])  # input from a pipe or a here-string

    @property
    def simple_commands(self) -> list[list[str]]:
        return [words for words in self.commands if words]


class _BashLexer:
    def __init__(self, text: str, start: int = 0, *, nesting: int = 0, closing: bool = False):
        if nesting > _MAX_NESTING:
            raise _LexError
        self.text = text
        self.pos = start
        self.nesting = nesting
        self.closing = closing  # inside `$(...)`: an unmatched `)` ends the text
        self.out = _Lexed()
        self.pending: list[tuple[str, bool, bool, int]] = []
        self.parens = 0
        self.cases = 0

    def run(self) -> int:
        """Read to the end, or past the `)` closing a substitution; the index reached."""
        text = self.text
        length = len(text)
        while self.pos < length:
            char = text[self.pos]
            if char in " \t":  # bash's blanks; a carriage return is part of a word
                self.out.active.append(" ")
                self.pos += 1
            elif char == "\n":
                self._separator(";")
                self.pos += 1
                if self.pending:
                    self._heredoc_bodies()
            elif char == "\\" and text.startswith("\n", self.pos + 1):
                self.pos += 2
            elif char == "#":
                end = text.find("\n", self.pos)
                self.pos = length if end < 0 else end
            elif char == ")" and self.closing and self.parens == 0 and self.cases == 0:
                return self.pos + 1
            elif char in _OPERATOR_CHARS and not self._process_substitution():
                self._operator()
            else:
                self._word()
        if self.closing:
            raise _LexError
        if self.pending:
            self._heredoc_bodies()
        return length

    def _process_substitution(self) -> bool:
        return self.text[self.pos] in "<>" and self.text.startswith("(", self.pos + 1)

    def _separator(self, run: str) -> None:
        _spend()
        self.out.tokens.append(run)
        self.out.commands.append([])
        self.out.fed.append(run in ("|", "|&"))
        self.out.active.append(run)

    def _operator(self) -> None:
        text = self.text
        start = self.pos
        while self.pos < len(text) and text[self.pos] in _OPERATOR_CHARS:
            if self.pos > start and self._process_substitution():
                break
            self.pos += 1
        run = text[start : self.pos]
        if run not in _REDIRECTIONS:
            self._separator(run)
            return
        _spend()
        self.out.tokens.append(run)
        self.out.active.append(run)
        if run in ("<<", "<<<"):
            self.out.fed[-1] = True
        if run == "<<":
            strip = text.startswith("-", self.pos)
            if strip:
                self.pos += 1
            self._heredoc_delimiter(strip)

    def _heredoc_delimiter(self, strip: bool) -> None:
        text = self.text
        while self.pos < len(text) and text[self.pos] in " \t":
            self.pos += 1
        start = self.pos
        out: list[str] = []
        quoted = False
        while self.pos < len(text):
            char = text[self.pos]
            if char in " \t\n" or char in _OPERATOR_CHARS:
                break
            if char == "\\":
                quoted = True
                out.append(text[self.pos + 1 : self.pos + 2])
                self.pos += 2
            elif char in "'\"":
                end = text.find(char, self.pos + 1)
                if end < 0:
                    raise _LexError
                out.append(text[self.pos + 1 : end])
                quoted = True
                self.pos = end + 1
            elif char == "$" and text.startswith(("'", '"'), self.pos + 1):
                self.pos += 1
            else:
                out.append(char)
                self.pos += 1
        if self.pos == start:
            raise _LexError
        self.pending.append(("".join(out), strip, quoted, len(self.out.commands) - 1))

    def _heredoc_bodies(self) -> None:
        text = self.text
        for delimiter, strip, quoted, command in self.pending:
            lines: list[str] = []
            while self.pos < len(text):
                _spend()
                end = text.find("\n", self.pos)
                line = text[self.pos :] if end < 0 else text[self.pos : end]
                self.pos = len(text) if end < 0 else end + 1
                line = line.lstrip("\t") if strip else line
                # Bash compares the whole line: a carriage return is part of the delimiter word
                # (`<<EOF\r` ends at `EOF\r`) and of the line (`EOF\r` does not end `<<EOF`).
                if line == delimiter:
                    break
                lines.append(line)
            body = "\n".join(lines)
            if not quoted:
                # The shell expands an unquoted body as it reads it: a substitution there runs.
                reader = _BashLexer(body, nesting=self.nesting + 1)
                reader._double([], terminated=False)
                self.out.groups.extend(reader.out.groups)
            self.out.heredocs.append(_Heredoc(body, quoted, command))
        self.pending.clear()

    def _word(self) -> None:
        text = self.text
        length = len(text)
        word: list[str] = []
        start = self.pos
        bare_start = text[start] not in "\\'\"" and not text.startswith(("$'", '$"'), start)
        # Bash expands a leading `~` only when nothing up to the first unquoted `/` is quoted:
        # `~"/x"`, `~\/x` and `~''/x` all name a directory called `~`.
        tilde_open = bare_start and text[start] == "~"
        quoted_prefix = False
        braces = 0  # inside `${...}`, where blanks and operators do not end the word
        while self.pos < length:
            char = text[self.pos]
            if not braces:
                if char in " \t\n":
                    break
                if char in _OPERATOR_CHARS and not self._process_substitution():
                    break
                if (
                    char == ")"
                    and self.closing
                    and self.parens == 0
                    and (self.cases == 0 or "".join(word) == "esac")  # `$(case ...;; esac)`
                ):
                    break
            if tilde_open and (char in "\\'\"" or text.startswith(("$'", '$"'), self.pos)):
                quoted_prefix = True
            if char == "\\":
                if self.pos + 1 >= length:
                    raise _LexError
                if text[self.pos + 1] != "\n":
                    word.append(text[self.pos + 1])
                self.out.active.append(text[self.pos : self.pos + 2])
                self.pos += 2
            elif char == "'":
                end = text.find("'", self.pos + 1)
                if end < 0:
                    raise _LexError
                word.append(text[self.pos + 1 : end])
                self.out.active.append("''")
                self.pos = end + 1
            elif char == "$" and text[self.pos + 1 : self.pos + 2] in _SPECIAL_DOLLARS:
                # `$$`, `$#`, `$1`: one parameter, so a `{`, `'` or `(` after it starts nothing.
                word.append(text[self.pos : self.pos + 2])
                self.out.active.append(text[self.pos : self.pos + 2])
                self.pos += 2
            elif char == "$" and text.startswith("'", self.pos + 1):
                decoded, end = _ansi_c(text, self.pos + 2)
                if end < 0:
                    raise _LexError
                word.append(decoded)
                self.out.active.append("$''")
                self.pos = end + 1
            elif char == '"' or (char == "$" and text.startswith('"', self.pos + 1)):
                if char == "$":
                    self.out.active.append("$")
                    self.pos += 1
                self._double(word)
            elif char == "$" and text.startswith("((", self.pos + 1):
                self._arithmetic(word)
            elif (char == "$" or char in "<>") and text.startswith("(", self.pos + 1):
                self._substitution(word)
            elif char == "`":
                self._backtick(word)
            elif char == "$" and text.startswith("{", self.pos + 1):
                self._check_parameter()
                braces += 1
                word.append("${")
                self.out.active.append("${")
                self.pos += 2
            else:
                if braces and char == "{":
                    braces += 1
                elif braces and char == "}":
                    braces -= 1
                elif char == "/":
                    tilde_open = False  # the tilde prefix ends at the first unquoted slash
                elif char == "(" and not braces:
                    self.parens += 1
                elif char == ")" and not braces and self.parens:
                    self.parens -= 1
                word.append(char)
                self.out.active.append(char)
                self.pos += 1
        if braces:
            raise _LexError
        value = "".join(word)
        if value.startswith("~") and (not bare_start or quoted_prefix):
            value = _LITERAL_TILDE + value[1:]
        self._emit(value)

    def _emit(self, value: str) -> None:
        _spend(len(value) // 16 + 1)
        words = self.out.commands[-1]
        if value == "case" and (not words or words[-1] in _LEADING_KEYWORDS):
            self.cases += 1
        elif value == "esac" and self.cases:
            self.cases -= 1
        self.out.tokens.append(value)
        words.append(value)
        self.out.active.append(" ")

    def _double(self, word: list[str], *, terminated: bool = True) -> None:
        """A double-quoted string from the quote at ``pos``, or a here-document body."""
        text = self.text
        length = len(text)
        if terminated:
            self.pos += 1
            self.out.active.append('"')
        braces = 0
        while True:
            if self.pos >= length:
                if terminated:
                    raise _LexError
                return
            char = text[self.pos]
            if char == '"' and terminated and not braces:
                self.pos += 1
                self.out.active.append('"')
                return
            if char == "\\" and text[self.pos + 1 : self.pos + 2] in ("$", "`", '"', "\\", "\n"):
                if text[self.pos + 1] != "\n":
                    word.append(text[self.pos + 1])
                self.out.active.append(text[self.pos : self.pos + 2])
                self.pos += 2
            elif char == "$" and text[self.pos + 1 : self.pos + 2] in _SPECIAL_DOLLARS:
                word.append(text[self.pos : self.pos + 2])
                self.out.active.append(text[self.pos : self.pos + 2])
                self.pos += 2
            elif char == "$" and text.startswith("((", self.pos + 1):
                self._arithmetic(word)
            elif char == "$" and text.startswith("(", self.pos + 1):
                self._substitution(word)
            elif char == "`":
                self._backtick(word)
            elif char == "$" and text.startswith("{", self.pos + 1):
                self._check_parameter()
                braces += 1
                word.append("${")
                self.out.active.append("${")
                self.pos += 2
            elif braces and char in "'\"":
                end = text.find(char, self.pos + 1)
                if end < 0:
                    raise _LexError
                word.append(text[self.pos + 1 : end])
                self.pos = end + 1
            else:
                if braces and char == "{":
                    braces += 1
                elif braces and char == "}":
                    braces -= 1
                word.append(char)
                self.out.active.append(char)
                self.pos += 1

    def _check_parameter(self) -> None:
        """`${ cmd; }` and `${| cmd; }` run a command in this shell (bash 5.3): not read here."""
        if self.text[self.pos + 2 : self.pos + 3] in (" ", "\t", "\n", "|"):
            raise _LexError

    def _substitution(self, word: list[str]) -> None:
        start = self.pos + 2
        inner = _BashLexer(self.text, start, nesting=self.nesting + 1, closing=True)
        end = inner.run()
        self.out.groups.append((self.text[start : end - 1], inner.out))
        word.append(_SUBSTITUTION)
        self.out.active.append(_SUBSTITUTION)
        self.pos = end

    def _arithmetic(self, word: list[str]) -> None:
        """`$((...))`: a number, but `$(...)` inside it still runs."""
        text = self.text
        start = self.pos + 3
        depth = 0
        position = start
        while position < len(text):
            char = text[position]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            position += 1
        if position >= len(text) or not text.startswith(")", position + 1):
            self._substitution(word)  # `$( (...) )` written without the space
            return
        body = text[start:position]
        self.out.arithmetic = True
        # An expression bash runs commands from (`$(...)`, `${ ...; }`) but this lexer cannot
        # read makes the whole command unreadable: it is decided from its text.
        inner = _BashLexer(body, nesting=self.nesting + 1)
        inner.run()
        self.out.groups.append((body, inner.out))
        word.append(_SUBSTITUTION)
        self.out.active.append(_SUBSTITUTION)
        self.pos = position + 2

    def _backtick(self, word: list[str]) -> None:
        text = self.text
        position = self.pos + 1
        content: list[str] = []
        while position < len(text) and text[position] != "`":
            if text[position] == "\\" and text[position + 1 : position + 2] in ("$", "`", "\\"):
                content.append(text[position + 1])
                position += 2
            else:
                content.append(text[position])
                position += 1
        if position >= len(text):
            raise _LexError
        body = "".join(content)
        inner = _BashLexer(body, nesting=self.nesting + 1)
        inner.run()
        self.out.groups.append((body, inner.out))
        word.append(_SUBSTITUTION)
        self.out.active.append(_SUBSTITUTION)
        self.pos = position + 1


def _lex_bash(command: str) -> _Lexed | None:
    """The command as bash reads it, or ``None`` when it leaves a quote or a group open."""
    lexer = _BashLexer(command)
    try:
        lexer.run()
    except _LexError:
        return None
    return lexer.out


def _shell_tokens(command: str) -> list[str] | None:
    """Bash words, quotes and escapes removed, operator runs as tokens; ``None`` if left open."""
    lexed = _lex_bash(command)
    return None if lexed is None else lexed.tokens


def _is_punctuation(token: str) -> bool:
    """`;`, `&&`, `|&`, `>|`, `&>>` ...: a run of operator characters is one token."""
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
# known variables and assignments in the same command are substituted, brace alternatives are
# enumerated, and whatever the shell decides at run time is marked with a private-use character
# that no judged path contains.

_STAR = "\ue000"  # a glob `*`: any text within one component, but not a leading dot
_ONE = "\ue001"  # a glob `?` or `[...]`: one character, but not a leading dot
_ANY = "\ue002"  # a brace sequence, a PowerShell wildcard: any text within a component
_CROSS = "\ue003"  # `**`: any text, separators included
# An expansion the guard cannot evaluate. It can hold separators and `..`, so the text after
# it is judged as if it could start anywhere; at a word's start it is any directory.
_UNKNOWN = "\ue004"
_MARKERS = frozenset({_STAR, _ONE, _ANY, _CROSS, _UNKNOWN})
_MAX_CANDIDATES = 16
_MAX_DEPTH = 3  # how often a variable's value is expanded again
_MAX_EXPANSION_NESTING = 8  # braces in braces, parameters in parameters
_MAX_ALTERNATIVES = 64
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGNMENT_WORD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)$", re.DOTALL)
_PERCENT_VARIABLE = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")
_SPECIAL_PARAMETERS = frozenset("0123456789@*#?$!-")
_SEPARATORS = re.compile(r"[/\\]")
_DRIVE = re.compile(r"[A-Za-z]:")
_MSYS_DRIVE = re.compile(r"^(?:/cygdrive)?/([A-Za-z])(?=[/\\]|$)")
_MSYS_TMP = re.compile(r"^/tmp(?=[/\\]|$)")
_PROC_LINK = re.compile(r"^/proc/[^/\\]+/(cwd|root|fd|map_files)(?=[/\\]|$)")
_CD_COMMANDS = frozenset({"cd", "pushd", "chdir"})
# A token that could make a program the shell starts somewhere the guard did not judge.
_ASSEMBLY = re.compile(r"[$`*?\[{%\ue000-\ue006]|^~[^/\\]")
# `${x:-word}`, `${x=word}`, `${x:+word}`, `${x?word}`: the value, or the word.
_PARAMETER_DEFAULT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!_-])(:?[-=+?])(.*)", re.S)
# A Windows 8.3 short name: up to six characters of the long name's basis, `~`, a number.
_SHORT_NAME = re.compile(r"([^~\\/]{1,6})~([0-9]{1,6})(?:\.[^.\\/]{0,3})?")
# The characters that start something `_expand_text` evaluates.
_EXPANDABLE = re.compile(r"[$%*?\[{+@!\ue005]")
_POWERSHELL_EXPANDABLE = re.compile(r"[$%!*?\[\ue005]")


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
    expansions: dict[tuple[str, int], list[str]] = field(default_factory=dict)
    anchors: list[Path] | None = None  # the homes above the cache's parent
    mutated: bool = False  # the command changes what the guard reads as known: `HOME=`, ...
    cmd: bool = False  # a script for cmd.exe, read with PowerShell's word rules
    # Copies and mounts of a directory above the cache: (where, of what), see `_aliases`.
    aliases: list[tuple[str, str]] = field(default_factory=list)

    def child(self, *, powershell: bool | None = None, cmd: bool = False) -> _Scope:
        """A scope for a nested script: it inherits the variables and directories."""
        return _Scope(
            root=self.root,
            cwd=self.cwd,
            home=self.home,
            variables=dict(self.variables),
            cwds=list(self.cwds),
            unknown_cwd=self.unknown_cwd,
            powershell=self.powershell if powershell is None else powershell,
            homes=self.homes,
            hints=self.hints,
            cmd=cmd,
            aliases=list(self.aliases),
        )

    related_cache: dict[str, bool] = field(default_factory=dict)

    def related(self, path: Path) -> bool:
        """Whether ``path`` is the cache, inside it, or a directory on the way to it."""
        key = str(path)
        known = self.related_cache.get(key)
        if known is None:
            known = _is_within(path, self.root) or _is_within(self.root, path)
            self.related_cache[key] = known
        return known


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
                _spend((index - start) // 16 + 1)
                return index
    _spend((len(text) - start) // 16 + 1)
    return len(text)


def _closing_brace(word: str, start: int) -> int:
    """Index of the `}` closing the `{` at ``start``, or -1."""
    depth = 0
    for index in range(start, len(word)):
        char = word[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                _spend((index - start) // 16 + 1)
                return index
    _spend((len(word) - start) // 16 + 1)
    return -1


def _piped_commands(tokens: list[str]) -> list[tuple[list[str], bool]]:
    """The simple commands, each with whether it reads the pipe of the one before."""
    commands: list[tuple[list[str], bool]] = [([], False)]
    for token in tokens:
        if _is_punctuation(token):
            commands.append(([], token == "|"))
        else:
            commands[-1][0].append(token)
    return [(words, piped) for words, piped in commands if words]


def _simple_commands(tokens: list[str]) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    for token in tokens:
        if _is_punctuation(token):
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _command_index(words: list[str]) -> int:
    """Where one simple command's name is, past keywords and assignments; else its length."""
    for index, raw in enumerate(words):
        word = raw.lstrip("(")
        if word and word not in _LEADING_KEYWORDS and not _ASSIGNMENT_WORD.match(word):
            return index
    return len(words)


def _command_words(words: list[str]) -> list[str]:
    """One simple command from its command name on, without keywords and assignments."""
    index = _command_index(words)
    if index >= len(words):
        return []
    return [words[index].lstrip("("), *words[index + 1 :]]


def _assign(scope: _Scope, name: str, value: str, *, append: bool = False) -> None:
    values = scope.variables.get(name, [])
    if values is None:
        return
    # `x+=y` appends to whatever `x` held, which may be text the guard never saw.
    added = [_UNKNOWN + value, *(old + value for old in values)] if append else [value]
    values = [*values, *added]
    scope.variables[name] = values if len(values) <= _MAX_CANDIDATES else None
    scope.expansions.clear()


_PRINTF_DIRECTIVE = re.compile(r"%(?:%|[-+ #0]*(?:\*|[0-9]+)?(?:\.(?:\*|[0-9]+))?[A-Za-z])")


def _printf_values(form: str, args: list[str]) -> list[str]:
    """What `printf -v name FORMAT ARGS` can store: `%s` filled in, other directives unknown."""
    text = "'".join(_ansi_c(piece, 0)[0] for piece in form.split("'"))
    remaining = list(args)

    def fill(match: re.Match[str]) -> str:
        directive = match.group()
        if directive == "%%":
            return "%"
        if remaining:
            value = remaining.pop(0)
            if directive.endswith("s"):
                return value
        return _UNKNOWN

    filled = _PRINTF_DIRECTIVE.sub(fill, text)
    unknown = _PRINTF_DIRECTIVE.sub(lambda m: "%" if m.group() == "%%" else _UNKNOWN, text)
    return [filled, unknown + (_UNKNOWN if args else "")]


def _build_scope(commands: list[list[str]], parent: _Scope) -> _Scope:
    scope = parent.child(powershell=False)
    last_words: list[str] = []
    # Order does not matter: a value assigned or a directory entered anywhere in the command is
    # considered everywhere in it, which can only add places a word may point to.
    for words in commands:
        _spend(len(words))
        last_words.append(words[-1])
        for word in words:
            match = _ASSIGNMENT_WORD.match(word.lstrip("("))
            if match:
                value = match.group(3).lstrip("(").rstrip(")")  # `a=(x)`: `$a` is `x`
                _assign(scope, match.group(1), value, append=bool(match.group(2)))
            if "${" in word:
                _assign_defaults(scope, word)
        command = _command_words(words)
        if not command:
            continue
        head = command[0]
        if len(command) >= 3 and head in {"for", "select"} and command[2] == "in":
            values = command[3:]
            values = values[: values.index("do")] if "do" in values else values
            for value in values:
                _assign(scope, command[1], value)
        elif head in {"read", "mapfile", "readarray", "getopts"}:
            for name in command[1:]:
                if _NAME.fullmatch(name):
                    scope.variables[name] = None
        elif head in {"declare", "typeset", "local", "readonly", "export"} and any(
            word.startswith("-") and "n" in word for word in command[1:]
        ):
            # A name reference (`declare -n r=x`) reads another variable by name.
            for word in command[1:]:
                reference = _ASSIGNMENT_WORD.match(word)
                if reference or _NAME.fullmatch(word):
                    scope.variables[reference.group(1) if reference else word] = None
        elif head == "printf" and len(command) >= 3 and command[1].startswith("-v"):
            # `printf -v name FORMAT`, `printf -vname FORMAT`, `printf -v 'a[0]' FORMAT`
            attached = command[1] != "-v"
            target = command[1][2:] if attached else command[2]
            rest = command[2:] if attached else command[3:]
            rest = rest[1:] if rest[:1] == ["--"] else rest
            stored = _NAME.match(target)
            if stored is None or stored.end() != len(target):
                scope.mutated = True  # an array element, or a name the guard cannot read
            if stored is None:
                continue
            if not rest:
                scope.variables[stored.group()] = None
                continue
            for value in _printf_values(rest[0], rest[1:]):
                _assign(scope, stored.group(), value)
    if not scope.powershell and "HOME" in scope.variables:
        scope.mutated = True  # `~` and a bare `cd` follow it
    # `$_` is the last word of the command before.
    for word in last_words:
        _assign(scope, "_", word)
    for words in commands:
        position = _command_index(words)
        for index, word in enumerate(words):
            name = word.lstrip("(")
            if name in _CD_COMMANDS:
                # Wherever bash runs it: in a function body, a case arm, after an expansion that
                # may be empty. A bare `cd` goes home only where it is the command.
                _change_directory(scope, words[index + 1 :], explicit=index != position)
            elif index == position and _may_change_directory(word, scope):
                _change_directory(scope, words[index + 1 :], explicit=True)
    return scope


_DEFAULT_ASSIGNMENT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?=")


def _assign_defaults(scope: _Scope, word: str) -> None:
    """`${x=word}` and `${x:=word}` assign the word to `x` when it is unset or empty."""
    for match in _DEFAULT_ASSIGNMENT.finditer(word):
        close = _closing_brace(word, match.start() + 1)
        if close < 0:
            scope.variables[match.group(1)] = None
            continue
        _assign(scope, match.group(1), word[match.end() : close])


def _may_change_directory(word: str, scope: _Scope) -> bool:
    """A command name the shell builds, which could turn out to be `cd`."""
    if "$" not in word and _SUBSTITUTION not in word:
        return False
    return any(
        candidate in _CD_COMMANDS or candidate.strip(_UNKNOWN) == ""
        for candidate in _expand_word(word, scope)
    )


def _add_directory(scope: _Scope, path: Path) -> None:
    key = os.path.normcase(str(path))
    if any(os.path.normcase(str(cwd)) == key for cwd in scope.cwds):
        return
    if len(scope.cwds) >= (_MAX_CANDIDATES * 2 if scope.related(path) else _MAX_CANDIDATES):
        scope.unknown_cwd = True
        return
    scope.cwds.append(path)


def _bases(text: str, scope: _Scope) -> list[Path]:
    """The directories a word is resolved against.

    Once paths are only normalised, a relative word without `..` can reach the cache only from
    a directory that is in it or on the way to it, so the others are skipped.
    """
    if _is_rooted(text):
        return [scope.cwd]
    if _resolving() or ".." in _components(text):
        return scope.cwds
    return [base for base in scope.cwds if scope.related(base) or _off_the_drives(base)]


_MAX_CD_TARGETS = 4


def _change_directory(scope: _Scope, args: list[str], *, explicit: bool = False) -> None:
    targets = [arg for arg in args if arg == "-" or not arg.startswith("-")]
    if not targets:
        if explicit:
            return
        targets = ["~"]
    # `cd` takes its first argument, but a redirection's words sit among the arguments too
    # (`cd 2>/dev/null ~/.cache`), so the first few are all taken as places it may go.
    for target in targets[:_MAX_CD_TARGETS]:
        if target == "-":
            scope.unknown_cwd = True
        else:
            _enter(scope, target)
    if len(targets) > _MAX_CD_TARGETS:
        scope.unknown_cwd = True


def _enter(scope: _Scope, target: str) -> None:
    for candidate in _expand_word(target, scope):
        _spend()
        if candidate.startswith(_UNKNOWN):
            scope.unknown_cwd = True
            continue
        if any(marker in candidate for marker in _MARKERS):
            # A glob can be a directory on the way to the cache: `cd ~/.c*; cd *`.
            scope.unknown_cwd = True
            for directory in _pattern_directories(candidate, scope):
                _add_directory(scope, directory)
            continue
        bases = _bases(candidate, scope)
        if len(scope.cwds) >= _MAX_CANDIDATES and ".." not in _components(candidate):
            # A directory reached from one that leads nowhere near the cache would be dropped.
            bases = [base for base in bases if scope.related(base) or _is_rooted(candidate)]
        for base in list(bases):
            for spelling in _msys_spellings(candidate):
                try:
                    path = _word_path(spelling, cwd=base)
                except (OSError, RuntimeError, ValueError):
                    scope.unknown_cwd = True
                    continue
                _add_directory(scope, path)


def _environment(name: str, scope: _Scope) -> str | None:
    if name.upper() == "PWD":
        return str(scope.cwd)
    if name == "_":
        return None  # the hook's own `$_` says nothing about the shell's
    value = os.environ.get(name)
    if value is None and name.upper() in {"HOME", "USERPROFILE"}:
        return str(scope.home)
    return value


def _variable(name: str, scope: _Scope, depth: int) -> list[str]:
    """The texts a variable can hold: assigned in the command, inherited, or unknown."""
    if scope.powershell:
        name = name.casefold()  # PowerShell variable names ignore case
    key = (name, depth)
    known = scope.expansions.get(key)
    if known is None:
        known = _variable_values(name, scope, depth)
        scope.expansions[key] = known
    return known


def _variable_values(name: str, scope: _Scope, depth: int) -> list[str]:
    inherited = _inherited(name, scope)
    if name in scope.variables:
        raw_values = scope.variables[name]
        if raw_values is None or depth >= _MAX_DEPTH:
            return [_UNKNOWN]
        values: dict[str, None] = {}
        for raw in raw_values:
            values.update(dict.fromkeys(_expand_word(raw, scope, depth=depth + 1)))
        # Before the assignment runs, the variable still holds what it inherited.
        values.update(dict.fromkeys(inherited or []))
        return list(values) if len(values) <= _MAX_CANDIDATES else [_UNKNOWN]
    return inherited if inherited is not None else [_UNKNOWN]


def _inherited(name: str, scope: _Scope) -> list[str] | None:
    """What a variable holds when the command starts: the shell's own or the environment's."""
    if name.upper() in {"PWD", "OLDPWD"}:
        return _directories(scope)
    if name.upper() in {"HOME", "USERPROFILE"}:
        # PowerShell's `$HOME` is the profile directory whatever `HOME` says; bash's is `HOME`.
        # Both readings are judged.
        homes = [str(home) for home in scope.homes or [scope.home]]
        homes += [os.environ[key] for key in ("HOME", "USERPROFILE") if os.environ.get(key)]
        return list(dict.fromkeys(homes))
    value = _environment(name, scope)
    if value is None:
        return None
    return [value.replace("*", _STAR).replace("?", _ONE)]


def _parameter(word: str, index: int, scope: _Scope, depth: int) -> tuple[list[str], int]:
    """Expand the `$...` at ``index``: `$name`, `${name}`, `${name:-word}`, `$env:NAME`."""
    start = index + 1
    if word.startswith("{", start):
        close = _closing_brace(word, start)
        if close < 0:
            return [_UNKNOWN], len(word)
        body = word[index + 2 : close]
        if body[:4].casefold() == "env:" and _NAME.fullmatch(body[4:]):
            return _variable(body[4:], scope, depth), close + 1
        if _NAME.fullmatch(body):
            return _variable(body, scope, depth), close + 1
        form = _PARAMETER_DEFAULT.fullmatch(body)
        if form is None or depth >= _MAX_EXPANSION_NESTING:
            # `${x%y}`, `${x/a/b}`, `${x:0:3}`, `${!x}`: text the guard does not compute.
            return [_UNKNOWN], close + 1
        name, operator, value = form.groups()
        current = _variable(name, scope, depth) if _NAME.fullmatch(name) else [_UNKNOWN]
        alternative = _expand_word(value, scope, depth=depth + 1)
        if operator[-1] in "-=":
            values = [*current, *alternative]
        elif operator[-1] == "+":
            values = ["", *alternative]
        else:
            values = current
        values = list(dict.fromkeys(values))
        return (values if len(values) <= _MAX_CANDIDATES else [_UNKNOWN]), close + 1
    if word[start : start + 4].casefold() == "env:":
        name = _NAME.match(word, start + 4)
        if name:
            return _variable(name.group(), scope, depth), name.end()
    name = _NAME.match(word, start)
    if name:
        return _variable(name.group(), scope, depth), name.end()
    if start < len(word) and word[start] in _SPECIAL_PARAMETERS:
        return [_UNKNOWN], index + 2
    return ["$"], index + 1


@dataclass
class _BraceFrame:
    start: int
    brace: bool  # `{...}` rather than `${...}`
    commas: list[int] = field(default_factory=list)
    sequence: bool = False
    found: list[tuple[int, int, list[int]]] = field(default_factory=list)


def _brace_groups(word: str) -> list[tuple[int, int, list[int]]]:
    """The outermost brace expansions in ``word``, in one pass: start, end, the commas.

    `{a,b}` and `{1..3}` expand; `{x}`, `{}` and `${...}` do not. A group inside one that does
    not expand still does (`{x{a,b}}`), and one inside one that does is found again when the
    alternative holding it is expanded.
    """
    top: list[tuple[int, int, list[int]]] = []
    stack: list[_BraceFrame] = []
    index = 0
    length = len(word)
    while index < length:
        char = word[index]
        if char == "$" and word.startswith("{", index + 1):
            stack.append(_BraceFrame(index + 1, brace=False))
            index += 2
            continue
        if char == "{":
            stack.append(_BraceFrame(index, brace=True))
        elif char == "}" and stack:
            frame = stack.pop()
            found = frame.found
            if not frame.brace:
                found = []
            elif frame.commas or frame.sequence:
                found = [(frame.start, index, frame.commas)]
            (stack[-1].found if stack else top).extend(found)
        elif stack and stack[-1].brace:
            if char == ",":
                stack[-1].commas.append(index)
            elif char == "." and word.startswith("..", index):
                stack[-1].sequence = True
        index += 1
    for frame in stack:
        top.extend(frame.found)
    _spend(length // 16 + 1)
    return sorted(top)


def _brace_candidates(word: str, *, nesting: int = 0) -> list[str]:
    """The words brace expansion makes of ``word``; groups beyond the cap become unknown text."""
    if "{" not in word:
        return [word]
    if nesting > _MAX_EXPANSION_NESTING:
        return [_UNKNOWN]
    groups = _brace_groups(word)
    if not groups:
        return [word]
    expansions: list[list[str]] = []
    for start, end, commas in groups:
        if not commas:
            expansions.append([_ANY])  # a sequence: `{1..9}`, `{a..z}`
            continue
        alternatives: list[str] = []
        bounds = [start, *commas, end]
        for left, right in itertools.pairwise(bounds):
            alternatives.extend(_brace_candidates(word[left + 1 : right], nesting=nesting + 1))
            if len(alternatives) > _MAX_ALTERNATIVES:
                alternatives = [_UNKNOWN]
                break
        _spend(len(alternatives))
        expansions.append(list(dict.fromkeys(alternatives)))
    literals: list[str] = []
    position = 0
    for start, end, _ in groups:
        literals.append(word[position:start])
        position = end + 1
    tail = word[position:]
    total = 1
    for alternatives in expansions:
        total *= len(alternatives)
        if total > _MAX_CANDIDATES:
            break
    if total <= _MAX_CANDIDATES:
        results = [""]
        for literal, alternatives in zip(literals, expansions, strict=True):
            results = [result + literal + choice for result in results for choice in alternatives]
        return [result + tail for result in results]
    # Too many words to enumerate together: each group in turn, the others unknown.
    results = []
    for chosen, alternatives in enumerate(expansions):
        for choice in alternatives:
            pieces = [
                literal + (choice if group == chosen else _UNKNOWN)
                for group, literal in enumerate(literals)
            ]
            results.append("".join(pieces) + tail)
    return list(dict.fromkeys(results))


def _directories(scope: _Scope) -> list[str]:
    """What `$PWD`, `$OLDPWD`, `~+` and `~-` can hold: any directory the command can be in.

    A `cd`, `pushd`, `popd` or `Set-Location` anywhere in the command moves them, so the event's
    working directory alone is not their value.
    """
    return [str(cwd) for cwd in scope.cwds] + ([_UNKNOWN] if scope.unknown_cwd else [])


def _tilde(prefix: str, scope: _Scope) -> list[str]:
    if prefix == "~":
        homes = [str(home) for home in scope.homes or [scope.home]]
        if not scope.powershell and "HOME" in scope.variables:
            homes += _variable("HOME", scope, 0)  # `HOME=...; cd ~`: the tilde follows `HOME`
        return list(dict.fromkeys(homes))
    if prefix in ("~+", "~-"):
        return _directories(scope)
    try:
        expanded = str(Path(prefix).expanduser())  # `~user`
    except RuntimeError:
        return [_UNKNOWN]
    return [expanded if expanded != prefix else _UNKNOWN]


def _expand_word(word: str, scope: _Scope, *, depth: int = 0) -> list[str]:
    """Every text the shell can make of ``word``, with markers where it decides at run time.

    The word as written is always one of them, with its glob and brace characters taken
    literally: a quoted `{/../x,}` or `[/../x]` is not expanded, and Windows collapses the `..`
    in it whether or not the directory before it exists.
    """
    _spend(len(word) // 32 + 2)
    texts = [word] if scope.powershell else _brace_candidates(word)
    results: dict[str, None] = {}
    for text in texts:
        results.update(dict.fromkeys(_expand_text(text, scope, depth)))
    if _WILDCARDS.search(word):
        results.update(dict.fromkeys(_expand_text(word, scope, depth, literal=True)))
    return list(results)


_WILDCARDS = re.compile(r"[*?\[{}@!+]")


def _variable_reference(name: str, scope: _Scope, depth: int) -> list[str] | None:
    """`%name%` (cmd) or `!name!` (cmd's delayed expansion): set in the command, or inherited."""
    key = name.casefold() if scope.powershell else name
    if key in scope.variables:
        return _variable(key, scope, depth)
    value = _environment(name, scope)
    return None if value is None else [value]


def _expand_text(word: str, scope: _Scope, depth: int, *, literal: bool = False) -> list[str]:
    results: list[list[str]] = [[]]
    index = 0
    device = _DEVICE_PREFIX.match(word) if os.name == "nt" else None
    if device:
        results = [[word[: device.end()]]]  # kept as written: its `?` is not a glob
        index = device.end()
    elif word.startswith(_LITERAL_TILDE) or (scope.powershell and word.startswith("~")):
        # A quoted `~` is a directory of that name; PowerShell passes `~` to a program as is but
        # its own cmdlets read it as the home directory. Both readings are judged.
        separator = _SEPARATORS.search(word)
        index = separator.start() if separator else len(word)
        name = word[1:index]
        results = [[choice] for choice in [_LITERAL_TILDE + name, *_tilde("~" + name, scope)]]
    elif word.startswith("~"):
        separator = _SEPARATORS.search(word)
        index = separator.start() if separator else len(word)
        results = [[choice] for choice in _tilde(word[:index], scope)]
    elif proc := _PROC_LINK.match(word):
        # `/proc/self/cwd` is the shell's working directory, not the guard's; `/proc/<pid>/fd`
        # and `/proc/<pid>/root` lead wherever that process has them.
        index = proc.end()
        targets = _directories(scope) if proc.group(1) == "cwd" else [_UNKNOWN]
        results = [[choice] for choice in targets]
    expandable = _POWERSHELL_EXPANDABLE if scope.powershell else _EXPANDABLE
    length = len(word)
    while index < length:
        found = expandable.search(word, index)
        if found is None:
            for result in results:
                result.append(word[index:])
            break
        if found.start() > index:
            for result in results:
                result.append(word[index : found.start()])
            index = found.start()
        _spend()
        char = word[index]
        pieces = [char]
        step = 1
        if char == "$":
            pieces, end = _parameter(word, index, scope, depth)
            step = end - index
        elif char == "%" and (percent := _PERCENT_VARIABLE.match(word, index)):
            values = _variable_reference(percent.group(1), scope, depth)
            pieces = [percent.group()] if values is None else values
            step = percent.end() - index
        elif char == "!" and scope.cmd and (delayed := _DELAYED_VARIABLE.match(word, index)):
            values = _variable_reference(delayed.group(1), scope, depth)
            pieces = [delayed.group()] if values is None else values
            step = delayed.end() - index
        elif char == _SUBSTITUTION:
            pieces = [_UNKNOWN]
        elif literal:
            pass  # glob and brace characters as written
        elif scope.powershell:
            close = _bracket_end(word, index) if char == "[" else index
            if char in "*?" or close > index:
                # PowerShell wildcards also match dot files; `[...]` is one character.
                pieces = [_ANY]
                step = close + 1 - index if char == "[" else 1
        elif word.startswith("{}", index):
            pieces = [_UNKNOWN]  # the placeholder `find -exec` and `xargs -I` fill in
            step = 2
        elif char in "?*+@!" and word.startswith("(", index + 1):
            close = _closing_paren(word, index + 1)
            if close < length and not _SEPARATORS.search(word, index, close):
                pieces = [_ANY]  # an extended glob such as `@(a|b)` or `?(x)`
                step = close + 1 - index
            elif char in "*?":
                pieces = [_STAR if char == "*" else _ONE]
        elif char == "*":
            pieces = [_CROSS] if word.startswith("**", index) else [_STAR]
            step = 2 if word.startswith("**", index) else 1
        elif char == "?":
            pieces = [_ONE]
        elif char == "[":
            close = _bracket_end(word, index)
            if close > index:
                pieces = [_ONE]
                step = close + 1 - index
        if len(results) * len(pieces) > _MAX_CANDIDATES:
            pieces = [_UNKNOWN]
        if len(pieces) == 1:
            for result in results:
                result.append(pieces[0])
        else:
            results = [[*result, piece] for result in results for piece in pieces]
        index += step
    return ["".join(result) for result in results]


_DELAYED_VARIABLE = re.compile(r"!([A-Za-z_][A-Za-z0-9_]*)!")


def _bracket_end(word: str, index: int) -> int:
    """The `]` closing a bracket expression at ``index``, or ``index`` when there is none.

    A bracket expression never spans a separator: in `[/../x]` the brackets are literal.
    """
    close = word.find("]", index + 2)
    _spend(((close if close > 0 else len(word)) - index) // 64)
    if close < 0 or _SEPARATORS.search(word, index, close):
        return index
    return close


def _msys_drive(text: str) -> str:
    """`/c/Users/...` is how Git Bash spells `C:/Users/...` on Windows."""
    if os.name != "nt":
        return text
    return _MSYS_DRIVE.sub(lambda match: f"{match.group(1).upper()}:", text, count=1)


_MSYS_DOUBLED_DRIVE = re.compile(r"^//([A-Za-z])(?=/|$)")


def _msys_spellings(text: str) -> list[str]:
    """A path as written and as Git Bash's mount table reads it: `/c/` is `C:/`, `/tmp` is
    the user's temporary directory."""
    if os.name != "nt" or text[:1] != "/":
        return [text]
    spellings = [text]
    drive = _msys_drive(text)
    if drive != text:
        spellings.append(drive)
    doubled = _MSYS_DOUBLED_DRIVE.match(text)
    if doubled:
        # `//c/Users/...`: what Git Bash leaves unconverted, and Docker reads as `C:/Users/...`.
        spellings.append(f"{doubled.group(1).upper()}:/{text[doubled.end() :].lstrip('/')}")
    if _MSYS_TMP.match(text):
        temp = os.environ.get("TEMP") or os.environ.get("TMP")
        if temp:
            spellings.append(temp.rstrip("\\/") + text[4:])
    return spellings


def _msys_mounted(text: str) -> bool:
    """A Git Bash path under a mount the guard cannot map (`/usr`, `/opt`, `/`)."""
    return (
        os.name == "nt"
        and text[:1] == "/"
        and text[1:2] not in ("/", "\\")
        and _MSYS_DRIVE.match(text) is None
    )


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


def _short_basis(name: str) -> str:
    """The text Windows builds an 8.3 alias from: no leading dots, spaces or inner dots."""
    stripped = name.casefold().lstrip(".").replace(" ", "")
    base = stripped.rsplit(".", 1)[0] if "." in stripped else stripped
    return re.sub(r"[+,;=\[\]]", "_", base.replace(".", ""))


def _alias_can_name(alias: str, name: str) -> bool:
    """Whether an 8.3 alias such as `HUNGRY~1` or `CACHE~1` can be the short name of ``name``."""
    match = _SHORT_NAME.fullmatch(alias.casefold())
    if match is None:
        return False
    prefix = match.group(1)
    basis = _short_basis(name)
    if not basis:
        return False
    if basis.startswith(prefix):
        return True
    # Past a few collisions Windows keeps two characters and appends a hash of the name.
    return (
        len(prefix) > 2
        and basis.startswith(prefix[:2])
        and re.fullmatch(r"[0-9a-f]{1,4}", prefix[2:]) is not None
    )


@dataclass(frozen=True)
class _Glob:
    """One path component of a word: literal characters and the shell's wildcards."""

    pattern: str
    dotless: bool  # a glob at the start does not match a leading dot
    literal: bool  # holds a character to anchor on
    alias: bool = False  # an 8.3 short name, which stands for a longer one
    width: int = 0  # the characters a match needs at least

    def matches(self, name: str) -> bool:
        if self.alias:
            return self.pattern == name.casefold() or _alias_can_name(self.pattern, name)
        if self.width > len(name) or (self.dotless and name.startswith(".")):
            return False
        return _wildcard_match(self.pattern, name.casefold())


def _component(text: str) -> _Glob | None:
    """The matcher for one component; ``None`` for one that can span separators."""
    if _CROSS in text:
        return None
    if not any(marker in text for marker in _MARKERS) and _SHORT_NAME.fullmatch(text):
        return _Glob(text.casefold(), dotless=False, literal=True, alias=True)
    out: list[str] = []
    for char in text:
        if char in (_STAR, _ANY, _UNKNOWN):
            if not out or out[-1] != _STAR:
                out.append(_STAR)
        elif char == _ONE:
            out.append(_ONE)
        else:
            out.append(char.casefold())
    pattern = "".join(out)
    literal = any(char not in _MARKERS for char in text)
    width = len(pattern) - pattern.count(_STAR)
    return _Glob(pattern, text[:1] in (_STAR, _ONE), literal, width=width)


def _dynamic(component: str) -> bool:
    """A component matched by pattern rather than resolved: a wildcard, or an 8.3 alias."""
    return any(marker in component for marker in _MARKERS) or (
        _SHORT_NAME.fullmatch(component) is not None
    )


def _walk(components: list[str], remaining: tuple[str, ...]) -> bool:
    """Whether components matched against the root's remaining parts reach the root.

    Ending above the root is not reaching it: `~/.cache/*.json` and `du -sh ~/.c*` name the
    directory that holds the cache, not the cache.
    """
    for position, component in enumerate(components):
        if position >= len(remaining):
            return True
        glued = _glued(component)
        if glued is not None:
            return glued.matches(remaining[position])
        matcher = _component(component)
        if matcher is None:
            return True
        if not matcher.matches(remaining[position]):
            return False
    return len(components) >= len(remaining)


def _glued(component: str) -> _Glob | None:
    """The matcher for the head of a component that has unknown text glued to it (`me<U>` from
    `$HOME$x`, `.cach<U>`): that text can hold separators, so past a head that fits, the word
    can lead anywhere below. ``None`` for a component without one."""
    position = component.find(_UNKNOWN)
    if position <= 0:
        return None
    return _component(component[: position + 1])


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
    return _walk(rest, remaining)


def _pattern_directories(candidate: str, scope: _Scope) -> list[Path]:
    """The directories on the way to the cache (or the cache itself) a pattern can name."""
    components = _components(candidate)
    literal = 0
    while literal < len(components) and not _dynamic(components[literal]):
        literal += 1
    prefix = "/".join(components[:literal])
    if not prefix and candidate[:1] in ("/", "\\"):
        prefix = "/"
    elif _DRIVE.fullmatch(prefix):
        prefix += "/"
    rooted = bool(prefix) and _is_rooted(prefix)
    found: list[Path] = []
    for base in [scope.cwd] if rooted else list(_bases(candidate, scope)):
        try:
            directory = _word_path(prefix or ".", cwd=base)
        except (OSError, RuntimeError, ValueError):
            continue
        found.extend(_walk_directories(directory, components[literal:], scope.root))
    return found


def _walk_directories(directory: Path, components: list[str], root: Path) -> list[Path]:
    stack: list[str] = []
    for component in components:
        if component in ("", "."):
            continue
        if component != "..":
            stack.append(component)
        elif stack:
            stack.pop()
        else:
            directory = directory.parent
    if _is_within(directory, root):
        return [root]
    if not _is_within(root, directory):
        return []
    remaining = root.parts[len(directory.parts) :]
    depth = len(directory.parts)
    for position, component in enumerate(stack):
        if position >= len(remaining):
            return [root]
        glued = _glued(component)
        if glued is not None:
            if not glued.matches(remaining[position]):
                return []
            return [Path(*root.parts[: depth + k]) for k in range(position + 1, len(remaining) + 1)]
        matcher = _component(component)
        if matcher is None:
            return [Path(*root.parts[: depth + k]) for k in range(position, len(remaining) + 1)]
        if not matcher.matches(remaining[position]):
            return []
    return [Path(*root.parts[: len(directory.parts) + len(stack)])]


def _is_rooted(text: str) -> bool:
    return text[:1] in ("/", "\\") or (os.name == "nt" and _DRIVE_PATH.match(text) is not None)


def _word_path(text: str, *, cwd: Path) -> Path:
    """The path a word names; a quoted leading `~` stays a directory called `~`.

    Within a decision every spelling is resolved once, and past the resolution budget it is
    only normalised.
    """
    budget = _budget.get()
    key = (text, str(cwd))
    lexical = False
    if budget is not None:
        known = budget.paths.get(key)
        if known is not None:
            return known
        lexical = budget.resolutions <= 0
        budget.resolutions -= 1
        components = text.count("/") + text.count("\\")
        deep = components > _MAX_RESOLVED_COMPONENTS
        if lexical or deep:
            budget.lexical += 1
            _spend(_NORMALISATION_COST + len(text) // 64)
        else:
            # Resolving asks the filesystem about each component in turn.
            _spend(_RESOLUTION_COST + _COMPONENT_COST * components + len(text) // 64)
    if text.startswith(_LITERAL_TILDE):
        path = canonical_path(Path("~" + text[1:]), cwd=cwd, expand_home=False, lexical=lexical)
    else:
        path = canonical_path(Path(text), cwd=cwd, lexical=lexical)
    if budget is not None:
        budget.paths[key] = path
    return path


def _literal_reaches(text: str, scope: _Scope) -> bool:
    if not text:
        return False
    rooted = _is_rooted(text)
    for base in _bases(text, scope):
        _spend()
        try:
            path = _word_path(text, cwd=base)
        except (OSError, RuntimeError, ValueError):
            continue
        if _is_within(path, scope.root):
            return True
        if _names_a_cache_copy(str(path), scope) and (
            _moved_to(base, scope) or _copy_on_disk(path, scope)
        ):
            # A copy of the cache reached from a directory the command moves to, or one already
            # on disk; `.cache/hungry-crab` as text where the command starts is no path.
            return True
        if _off_the_drives(path) and _text_hints(text, scope):
            return True
    return not rooted and scope.unknown_cwd and _unanchored(_components(text), scope)


def _moved_to(directory: Path, scope: _Scope) -> bool:
    """Whether ``directory`` is one the command changes to, not the one it starts in."""
    return os.path.normcase(str(directory)) != os.path.normcase(str(scope.cwd))


def _copy_on_disk(path: Path, scope: _Scope) -> bool:
    """Whether the copy of the cache ``path`` names exists: a copy an earlier call made."""
    for candidate in (path, *path.parents):
        if not _names_a_cache_copy(str(candidate), scope):
            return False
        if candidate.exists():
            return True
    return False


def _in_a_cache_copy(scope: _Scope) -> bool:
    """Whether the command can run in a copy of the cache: `cp -r ~ /tmp/h && cd /tmp/h/.cache
    && cd hungry-crab/...`, or a working directory left there by an earlier call."""
    return any(_names_a_cache_copy(str(cwd), scope) for cwd in scope.cwds)


def _components(text: str) -> list[str]:
    return _SEPARATORS.split(text)


def _candidate_reaches(text: str, scope: _Scope) -> bool:
    _spend(len(text) // 32 + 2)
    if text.startswith(_UNKNOWN):
        return _unanchored(_components(text), scope)
    spans = text.find(_UNKNOWN)
    if spans > 0 and _unanchored(_components(text[spans:]), scope):
        # `~/.cach${x:-e/hungr}y-crab`: the unknown part can hold `/` and `..`, so what
        # follows it can start anywhere.
        return True
    if any(_spelling_reaches(spelling, scope) for spelling in _msys_spellings(text)):
        return True
    return _msys_mounted(text) and _unanchored(_components(text), scope)


def _spelling_reaches(text: str, scope: _Scope) -> bool:
    components = _components(text)
    literal = 0
    while literal < len(components) and not _dynamic(components[literal]):
        literal += 1
    if literal == len(components):
        return _literal_reaches(text, scope)
    prefix = "/".join(components[:literal])
    if not prefix and text[:1] in ("/", "\\"):
        prefix = "/"
    elif _DRIVE.fullmatch(prefix):
        prefix += "/"
    rooted = bool(prefix) and _is_rooted(prefix)
    for base in [scope.cwd] if rooted else _bases(text, scope):
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
    groups = {start: end for start, end, _ in _brace_groups(pattern)}
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
        elif char == "{" and index in groups:
            body = pattern[index : groups[index] + 1]
            piece = _CROSS if _SEPARATORS.search(body) else _ANY
            step = groups[index] + 1 - index
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


# --- the words of one command ---------------------------------------------------------------


def _list_elements(text: str) -> list[str]:
    """The entries of a `;` or `:` separated path list.

    A drive letter's colon (`C:\\x`) and a URL's (`https://`) separate nothing.
    """
    if ";" not in text and ":" not in text:
        return []
    elements: list[str] = []
    for chunk in text.split(";"):
        start = 0
        for position, char in enumerate(chunk):
            if char != ":":
                continue
            if position - start == 1 and chunk[start].isalpha():
                continue  # `C:\x`, `C:/x`
            if chunk.startswith("//", position + 1):
                continue  # `https://host/x`
            elements.append(chunk[start:position])
            start = position + 1
        elements.append(chunk[start:])
    return [element for element in elements if element and element != text]


def _option_values(word: str) -> list[str]:
    """What an option word can hand a program as a path: `--x=v`, `-x=v`, `-Cpath`."""
    if word.startswith("--"):
        return [word.split("=", 1)[1]] if "=" in word else []
    values: list[str] = []
    if "=" in word:
        values.append(word.split("=", 1)[1])
    if len(word) > 2:
        values.append(word[2:])  # `-C/path`, `-I$HOME/lib`, `-e./src`
        letters = 1
        while letters < len(word) and word[letters].isalnum():
            letters += 1
        if 2 < letters < len(word):
            values.append(word[letters:])  # `-xvf/tmp/a.tar`: a cluster, then the value
    return values


def _path_words(words: list[str]) -> list[tuple[int, str]]:
    """The texts in one command that can name a path, with the word each comes from."""
    found: list[tuple[int, str]] = []
    for index, raw in enumerate(words):
        word = raw.lstrip("(").rstrip(")")
        assignment = _ASSIGNMENT_WORD.match(word)
        if assignment:
            texts = [assignment.group(3).lstrip("(")]  # an array's first element: `a=(~/x`
        elif word.startswith("-") and len(word) > 1:
            texts = _option_values(word)
        elif word.startswith("@") and len(word) > 1:
            texts = [word, word[1:]]  # a response file: `gcc @args.rsp`
        else:
            texts = [word]
        for text in texts:
            found.append((index, text))
            found.extend((index, element) for element in _list_elements(text))
    return found


_SHELLS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "busybox", "rbash", "csh", "tcsh"}
)
_POWERSHELLS = frozenset({"pwsh", "powershell"})
# Programs that run the string they are given as a shell command. The guard reads that string
# as a script of its own; it is not a construct the guard fails to follow.
_SCRIPT_RUNNERS = frozenset({"watch", "script", "flock", "tmux", "screen", "hyperfine"})
# Programs that start a shell reading its commands from input when given no command.
_LOGIN_SHELLS = frozenset({"su", "runuser", "sudo", "doas"})
_FIND_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_XARGS_VALUE_FLAGS = frozenset("adELnPsRS")
_XARGS_VALUE_LONG = frozenset(
    {"arg-file", "delimiter", "eof", "max-lines", "max-args", "max-procs", "max-chars"}
)
_PARALLEL_PLACEHOLDER = re.compile(r"\{[^{}\s]*\}")
_SSH_VALUE_FLAGS = frozenset("bcDEeFIiJLlmOopQRSWw")
# Variables whose value a program runs through a shell: `GIT_SSH_COMMAND`, `GIT_CONFIG_VALUE_0`
# with a `core.*` key, a pager, an editor, a prompt.
_COMMAND_VARIABLE = re.compile(
    r"GIT_[A-Z0-9_]*|[A-Z0-9_]*_COMMAND|[A-Z0-9_]*PAGER|[A-Z0-9_]*EDITOR|VISUAL|BROWSER"
    r"|PROMPT_COMMAND|BASH_ENV|ENV|PS[0-4]|[A-Z0-9_]*ASKPASS|LESSOPEN|LESSCLOSE",
    re.IGNORECASE,
)
_POWERSHELL_ENV_ASSIGNMENT = re.compile(r"^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*(=?)(.*)$", re.I | re.S)
# Programs that link, copy or mount what they are given somewhere else.
_LINKERS = frozenset(
    {"ln", "cp", "mv", "rsync", "install", "ditto", "scp", "rclone", "robocopy", "xcopy"}
    | {"copy", "move", "mklink", "subst", "mount", "bindfs", "junction", "tar", "zip", "7z"}
    | {"copy-item", "cpi", "move-item", "mi", "new-psdrive", "ndr", "net", "compress-archive"}
)
_LINK_ITEM_TYPES = frozenset({"junction", "symboliclink", "hardlink"})
# Words that make bash evaluate the text of a value again: arithmetic, `eval`, prompts.
_REEVALUATORS = frozenset({"let", "eval", "source", ".", "trap", "[[", "test", "["})
_WRAPPERS = frozenset(
    {"sudo", "doas", "env", "nice", "nohup", "time", "timeout", "stdbuf", "exec", "command"}
    | {"builtin", "ionice", "chrt", "taskset", "unbuffer"}
)
# The programs `_inspect_command` reads the rest of a command for.
_INSPECTED = (
    _SHELLS
    | _POWERSHELLS
    | _SCRIPT_RUNNERS
    | _LOGIN_SHELLS
    | _LINKERS
    | _REEVALUATORS
    | {"cmd", "invoke-expression", "iex", "invoke-command", "icm", "eval", "trap", "source"}
    | {"alias", "git", "ssh", "env", "read", "mapfile", "readarray", "declare", "typeset"}
    | {"local", "readonly", "export", "xargs", "parallel", "call", "new-psdrive", "ndr"}
    | {"find", "new-item", "ni", "docker", "podman", "nerdctl"}
)


# How a script is read: text a shell has to parse (`bash -c`, `-EncodedCommand`), text a program
# runs (`ssh host '...'`, `GIT_SSH_COMMAND`, `tmux new '...'`), and text that may be a script (a
# script file's name, a hinted word with a blank). One a shell has to parse and cannot be read
# makes the command unreadable; one a program runs is judged by its hints instead; one that only
# may be a script is dropped.
_MAYBE, _RUNS, _MUST = 0, 1, 2
_Script = tuple[str, str, int]  # text, grammar, how it is read


@dataclass
class _CommandInfo:
    """What one simple command does with its words beyond naming paths."""

    words: list[str]
    patterns: set[int] = field(default_factory=set)  # search patterns, which are no hint
    code: bool = False  # turns text the guard never sees into commands: a shell reading input
    value: bool = False  # computes a value the guard does not follow: an array, a reference
    scripts: list[_Script] = field(default_factory=list)  # text, grammar, how it is read
    stdin: str | None = None  # the grammar of a shell that reads this command's here-documents
    extra: list[str] = field(default_factory=list)  # paths `find` hands to what it runs
    sources: set[int] = field(default_factory=set)  # what it links, copies or mounts elsewhere
    reevaluates: bool = False  # evaluates the text of a value again
    opaque: bool = False  # hands a shell a script the guard cannot decode
    fed: bool = False  # reads a value from what an earlier part of the command prints
    array_read: bool = False  # `read -a`, `mapfile`, `readarray`
    searches: list[str] = field(default_factory=list)  # where a search or listing starts
    directories: list[tuple[int, str]] = field(default_factory=list)  # `-C dir` and kin
    mounts: list[str] = field(default_factory=list)  # host sides of container bind mounts


def _program(token: str) -> str:
    return Path(token).name.casefold().removesuffix(".exe")


# Programs that print what they are handed, or a name computed from it, and run nothing.
_PRINTERS = frozenset({"echo", "printf", "dirname", "basename", "realpath", "readlink"})


def _runs_a_reader(command: list[str]) -> bool:
    """Whether a command handed paths the guard cannot see only reads them."""
    if not command:
        return True  # `xargs` alone echoes
    head = command[0]
    if _ASSEMBLY.search(head) or "/" in head or "\\" in head:
        return False
    program = _program(head)
    if program in _PRINTERS:
        return True
    if program not in _READ_ONLY_COMMANDS:
        return False
    return program != "rg" or _rg_is_read_only(command[1:])


def _action_command(words: list[str], index: int) -> list[str]:
    """The command a `find -exec` runs, up to its `;` or `+`."""
    command: list[str] = []
    for word in words[index + 1 :]:
        if word in (";", "+"):
            break
        command.append(word)
    return command


def _find_starts(words: list[str], index: int) -> tuple[list[str], int]:
    """`find`'s start paths (the working directory when it names none), and where its
    expression begins."""
    position = index + 1
    while position < len(words):
        word = words[position]
        if word in ("-H", "-L", "-P") or re.fullmatch(r"-O[0-9]*", word):
            position += 1
        elif word == "-D":
            position += 2  # `-D tree,search`: debug options
        else:
            break
    starts: list[str] = []
    while position < len(words) and not words[position].startswith(("-", "(", "!", ")", ",")):
        starts.append(words[position])
        position += 1
    return starts or ["."], position


def _find_descendants(words: list[str], index: int) -> list[str]:
    """`find`'s start paths and everything below them, when it runs or deletes what it finds."""
    starts, position = _find_starts(words, index)
    risky = "-delete" in words[position:] or any(
        word in _FIND_ACTIONS and not _runs_a_reader(_action_command(words, at))
        for at, word in enumerate(words[position:], start=position)
    )
    if not risky:
        return []
    return [start.rstrip("/\\") + "/**" for start in starts]


# Searches and recursive listings print paths below where they start; `locate` prints any.
_LOCATORS = frozenset({"locate", "plocate", "mlocate", "slocate"})
_FD = frozenset({"fd", "fdfind"})
_FD_EXEC = frozenset({"-x", "--exec", "-X", "--exec-batch"})
_PATTERN_FIRST = frozenset({"fd", "fdfind", "ag", "ack", "rg", "grep", "egrep", "fgrep"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
_POWERSHELL_LISTINGS = frozenset({"get-childitem", "gci", "ls", "dir"})
_SEARCHERS = (
    _LOCATORS
    | _FD
    | _GREPS
    | _POWERSHELL_LISTINGS
    | {"find", "rg", "ag", "ack", "tree", "du", "where", "for", "forfiles"}
)
# `--pre PROGRAM` runs PROGRAM on every file; `--pre-glob` only narrows which.
_RG_PRE = re.compile(r"--pre(?:=|$)")


def _search_runner(words: list[str], index: int, program: str) -> bool:
    """Whether a search runs a program of its own on what it finds: `fd -x`, `rg --pre`,
    cmd's `for /r ... do`, `forfiles /c`."""
    rest = words[index + 1 :]
    if program in _FD:
        return not _runs_a_reader(_fd_command(words, index))
    if program == "rg":
        return any(_RG_PRE.match(arg) for arg in rest)
    if program == "for":
        folded = [arg.casefold() for arg in rest]
        if "do" not in folded:
            return False
        command = [arg.lstrip("@") for arg in rest[folded.index("do") + 1 :]]
        return not _runs_a_reader(command)
    if program == "forfiles":
        for flag, value in itertools.pairwise(rest):
            if flag.casefold() in ("/c", "//c", "-c"):
                return re.fullmatch(r"\s*(?:cmd\s+/+c\s+)?echo\b.*", value, re.I | re.S) is None
    return False


def _fd_command(words: list[str], index: int) -> list[str]:
    """The command `fd -x` / `--exec` / `-X` runs on what it finds; empty without one."""
    for at in range(index + 1, len(words)):
        if words[at] in _FD_EXEC:
            return words[at + 1 :]
    return ["echo"]


def _recursive(program: str, args: list[str], grammar: str) -> bool:
    """Whether a listing or a search descends: `ls -R`, `du -a`, `grep -r`, `tree`, `fd`,
    `rg`, `Get-ChildItem -Recurse`, `dir /s`."""
    if grammar == "powershell":
        return program in _POWERSHELL_LISTINGS and any(
            (len(name) >= 2 and "-recurse".startswith(name))
            or (len(name) >= 3 and "-depth".startswith(name))
            for name in (arg.casefold().split(":")[0] for arg in args)
        )
    if grammar == "cmd":
        return program == "dir" and any(arg.casefold() == "/s" for arg in args)
    flags = "".join(arg[1:] for arg in args if arg.startswith("-") and not arg.startswith("--"))
    longs = {arg[2:].split("=", 1)[0] for arg in args if arg.startswith("--")}
    if program in _FD | {"rg", "ag", "ack", "tree"}:
        return True
    if program == "ls":
        return "R" in flags or "recursive" in longs
    if program == "du":
        return "a" in flags or "all" in longs
    if program in _GREPS:
        return bool(set(flags) & set("rR")) or bool(longs & {"recursive", "dereference-recursive"})
    return False


def _search_roots(words: list[str], index: int, program: str, grammar: str) -> list[str]:
    """Where a search or a recursive listing starts, the working directory when it names
    none; empty for any other command. `locate` searches everything."""
    if program in _LOCATORS:
        return ["/"]
    if program == "find":
        return _find_starts(words, index)[0]
    rest = words[index + 1 :]
    if program == "where":
        # `where /r DIR name` (`//r` from Git Bash) searches DIR and below.
        return [
            following
            for flag, following in itertools.pairwise(rest)
            if flag.casefold() in ("/r", "//r", "-r")
        ]
    if program == "for":
        # cmd's `for /r [DIR] %f in (set) do command`: DIR and below, else here.
        if grammar != "cmd" or not rest or rest[0].casefold() != "/r":
            return []
        loop_variable = len(rest) < 2 or re.fullmatch(r"%%?[A-Za-z]", rest[1]) is not None
        return ["."] if loop_variable else [rest[1]]
    if program == "forfiles":
        # `forfiles /p DIR /s /c command`: one directory, or all below it.
        return [
            following
            for flag, following in itertools.pairwise(rest)
            if flag.casefold() in ("/p", "//p", "-p")
        ] or ["."]
    if program in _FD:
        rest = rest[: next((at for at, arg in enumerate(rest) if arg in _FD_EXEC), len(rest))]
    if not _recursive(program, rest, grammar):
        return []
    if grammar == "powershell":
        return _powershell_listing_roots(rest)
    if grammar == "cmd":
        return [arg for arg in rest if not arg.startswith("/")] or ["."]
    values = _GREP_VALUES.get(program) or _SEARCH_VALUES.get(program, _NO_VALUES)
    operands, given = _operands(rest, values)
    # grep's and rg's patterns may come from `-e`, `-f` or nowhere (`rg --files`); fd's `-e`
    # is an extension.
    named = (program in _GREPS | {"rg"} and given & {"e", "f", "regexp", "file", "files"}) or (
        program in ("ag", "ack") and given & {"g", "G", "f", "file-search-regex"}
    )
    if program in _PATTERN_FIRST and not named and operands:
        # The first operand is the pattern, unless an option's value was taken for one: it
        # stays a candidate start, and with no other the search starts where it runs.
        operands = [*operands[1:], operands[0]] if len(operands) > 1 else [operands[0], "."]
    if program in _FD:
        # `--search-path DIR` and `--base-directory DIR` name where fd searches.
        operands += [
            value
            for flag, value in itertools.pairwise(rest)
            if flag in ("--search-path", "--base-directory")
        ]
        operands += [
            arg.partition("=")[2]
            for arg in rest
            if arg.startswith(("--search-path=", "--base-directory="))
        ]
    return operands or ["."]


_NO_VALUES: tuple[frozenset[str], frozenset[str]] = (frozenset(), frozenset())
# The options of the other searches and listings that take a value.
_SEARCH_VALUES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "fd": (
        frozenset("eEtdSjco"),
        frozenset(
            {"extension", "exclude", "type", "max-depth", "min-depth", "exact-depth", "size"}
            | {"threads", "color", "owner", "changed-within", "changed-before", "max-results"}
            | {"path-separator", "ignore-file", "format", "batch-size", "search-path"}
            | {"base-directory"}
        ),
    ),
    "ls": (
        frozenset("IwT"),
        frozenset(
            {"block-size", "color", "format", "hide", "ignore", "indicator-style", "sort"}
            | {"quoting-style", "time", "time-style", "width", "tabsize"}
        ),
    ),
    "tree": (frozenset("LPIoHT"), frozenset({"charset", "filelimit", "timefmt", "sort"})),
    "du": (
        frozenset("dBt"),
        frozenset({"max-depth", "exclude", "block-size", "threshold", "time-style", "files0-from"}),
    ),
    "ag": (
        frozenset("ABCGgmp"),
        frozenset({"depth", "file-search-regex", "ignore", "path-to-ignore"}),
    ),
    "ack": (frozenset("ABCgm"), frozenset({"type", "ignore-dir", "ignore-file", "match"})),
}
_SEARCH_VALUES["fdfind"] = _SEARCH_VALUES["fd"]


def _operands(
    args: list[str], values: tuple[frozenset[str], frozenset[str]]
) -> tuple[list[str], set[str]]:
    """A command's operands, past its options and their values; and the options it was given,
    short letters and long names."""
    short, long = values
    operands: list[str] = []
    given: set[str] = set()
    index = 0
    options = True
    while index < len(args):
        arg = args[index]
        if options and arg == "--":
            options = False
        elif options and arg.startswith("--") and len(arg) > 2:
            name, equals, _ = arg[2:].partition("=")
            given.add(name)
            index += 1 if name in long and not equals else 0
        elif options and arg.startswith("-") and len(arg) > 1:
            for offset in range(1, len(arg)):
                given.add(arg[offset])
                if arg[offset] in short:
                    index += 1 if offset + 1 == len(arg) else 0
                    break
        else:
            operands.append(arg)
        index += 1
    return operands, given


_POWERSHELL_PATH_PARAMETERS = ("-path", "-literalpath", "-lp", "-pspath")
_POWERSHELL_LISTING_VALUES = ("-filter", "-include", "-exclude", "-depth", "-attributes")


def _powershell_listing_roots(args: list[str]) -> list[str]:
    """Where `Get-ChildItem -Recurse` starts: `-Path` or `-LiteralPath`, else its first
    positional argument (the second is `-Filter`), else the working directory."""
    roots: list[str] = []
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("-") and len(arg) > 1:
            name, colon, _ = arg.casefold().partition(":")
            path = len(name) > 2 and any(p.startswith(name) for p in _POWERSHELL_PATH_PARAMETERS)
            valued = len(name) > 2 and any(v.startswith(name) for v in _POWERSHELL_LISTING_VALUES)
            if path or valued:
                value = arg.partition(":")[2] if colon else (args[index + 1 : index + 2] or [""])[0]
                if path:
                    roots.extend(piece for piece in value.split(",") if piece)
                index += 1 if colon else 2
                continue
        else:
            positional.append(arg)
        index += 1
    for first in positional[:1]:
        roots.extend(piece for piece in first.split(",") if piece)  # `a,b` is an array
    return roots or ["."]


def _aliases(commands: list[list[str]], scope: _Scope) -> list[tuple[str, str]]:
    """Where one command copies, links or mounts a directory above the cache (home, its
    `.cache`, the root): `cp -r ~ /tmp/h`, `ln -s ~ h`, `docker run -v ~:/h`. A path below
    such a place is read as the same path below the original, so a search of the copy is
    a search of home."""
    found: list[tuple[str, str]] = []
    for words in commands:
        for index, raw in enumerate(words):
            program = _program(raw.lstrip("(")) if raw.lstrip("(") else ""
            pairs: list[tuple[str, str]] = []
            if program in _POWERSHELL_COPIERS:
                pairs = _powershell_copy(words[index + 1 :])
            elif program in _LINKERS:
                sources = _link_sources(words, index, program)
                operands = [
                    at
                    for at in range(index + 1, len(words))
                    if not words[at].startswith("-") and at not in sources
                ]
                if operands:
                    pairs = [(words[operands[-1]], words[at]) for at in sorted(sources)]
            elif program in _CONTAINER_TOOLS:
                pairs = _container_mounts(words[index + 1 :])
            for destination, source in pairs:
                _spend()
                if len(found) >= _MAX_ANCHORS:
                    return found
                if any(_anchors(piece, scope) for piece in _expand_word(source, scope)):
                    for target in _expand_word(destination, scope):
                        if not any(marker in target for marker in _MARKERS):
                            place = target.replace("\\", "/").rstrip("/")
                            found.append((place, source))
                            name = source.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                            if name and name not in ("~", ".", ".."):
                                found.append((f"{place}/{name}", source))  # into a dir
    return found


def _powershell_copy(args: list[str]) -> list[tuple[str, str]]:
    """`Copy-Item [-Path] SRC [-Destination] DEST`: as (DEST, SRC)."""
    named: dict[str, str] = {}
    positional: list[str] = []
    at = 0
    while at < len(args):
        arg = args[at]
        if arg.startswith("-") and len(arg) > 1:
            name, colon, _ = arg.casefold().partition(":")
            if any(full.startswith(name) for full in ("-path", "-literalpath", "-destination")):
                named[name[:3]] = (
                    arg.partition(":")[2] if colon else (args[at + 1 : at + 2] or [""])[0]
                )
                at += 1 if colon else 2
                continue
        else:
            positional.append(arg)
        at += 1
    source = named.get("-pa") or named.get("-li") or (positional[:1] or [""])[0]
    destination = named.get("-de") or (positional[1:2] or [""])[0]
    return [(destination, source)] if source and destination else []


def _unalias(text: str, scope: _Scope) -> str | None:
    """``text`` read from the original of a copy or a mount it is below, else ``None``."""
    spelled = text.replace("\\", "/")
    folded = spelled.casefold() if os.name == "nt" else spelled
    for place, source in scope.aliases:
        key = place.casefold() if os.name == "nt" else place
        if key and (folded == key or folded.startswith(key + "/")):
            return source.rstrip("/\\") + spelled[len(place) :]
    return None


def _search_starts(info: _CommandInfo) -> list[str]:
    """A search's starts, and the relative ones read from the directories the command's
    options set too (`docker run -w /h ... find .`, `env -C ~ find .`)."""
    starts = list(info.searches)
    for _, directory in info.directories[:_MAX_ANCHORS]:
        starts.extend(
            f"{directory.rstrip('/')}/{start}" for start in info.searches if _relative_text(start)
        )
    return starts


def _searches_near(roots: list[str], scope: _Scope) -> bool:
    """Whether a search starting at one of ``roots`` walks through the cache: it starts in
    the cache, at the directory that holds it, or at any directory above it, home and the
    working directory included when they are one of those. A start the guard does not know
    (`find "$(echo ~)"`) may be any of them."""
    for root in roots:
        original = _unalias(root, scope)
        if original is not None and _searches_near([original], scope):
            return True  # `cp -r ~ /tmp/h && find /tmp/h ...`
        for candidate in _expand_word(root, scope):
            if candidate.startswith(_UNKNOWN):
                return True
            mapped = _unalias(candidate, scope)
            if mapped is not None and mapped != root and _searches_near([mapped], scope):
                return True
            if not _is_rooted(candidate) and scope.unknown_cwd:
                return True
            if any(marker in candidate for marker in _MARKERS):
                if _pattern_directories(candidate, scope):
                    return True
                continue
            for spelling in _msys_spellings(candidate):
                for base in _bases(spelling, scope):
                    try:
                        path = _word_path(spelling, cwd=base)
                    except (OSError, RuntimeError, ValueError):
                        continue
                    if scope.related(path):
                        return True
    return False


def _xargs_options(words: list[str], index: int) -> tuple[int, str | None]:
    """Where the command `xargs` runs starts, and its replace string."""
    position = index + 1
    replace: str | None = None
    while position < len(words):
        word = words[position]
        if word == "--":
            position += 1
            break
        if not word.startswith("-") or word == "-":
            break
        if word.startswith("--"):
            name, equals, value = word[2:].partition("=")
            if name == "replace":
                replace = value if equals else "{}"
            elif name in _XARGS_VALUE_LONG and not equals:
                position += 1
            position += 1
            continue
        flag, attached = word[1], word[2:]
        if flag in "IJ":
            if attached:
                replace = attached
            elif position + 1 < len(words):
                replace = words[position + 1]
                position += 1
        elif flag == "i":
            replace = attached or "{}"
        elif flag in _XARGS_VALUE_FLAGS and not attached:
            position += 1
        position += 1
    return position, replace


def _xargs(words: list[str], index: int) -> tuple[list[str], bool]:
    """The words after `xargs`, its replace string marked unknown; whether it runs a reader."""
    position, replace = _xargs_options(words, index)
    rest = words[index + 1 :]
    if replace:
        offset = position - (index + 1)
        rest = rest[:offset] + [word.replace(replace, _UNKNOWN) for word in rest[offset:]]
    return rest, _runs_a_reader(words[position:])


def _run_index(words: list[str]) -> int | None:
    """The word naming the program that actually runs: past keywords, assignments, wrappers."""
    index = _command_index(words)
    while index < len(words):
        word = words[index].lstrip("(")
        program = _program(word) if word else ""
        if program == "xargs":
            index = _xargs_options(words, index)[0]
            continue
        if program not in _WRAPPERS:
            return index
        index += 1
        duration = program == "timeout"
        while index < len(words):
            word = words[index]
            if word.startswith("-") or _ASSIGNMENT_WORD.match(word):
                index += 1
            elif duration:
                duration = False
                index += 1
            else:
                break
    return None


def _shell_scripts(args: list[str]) -> list[_Script]:
    """What a shell can run as a script: the `-c` text, strictly; any other word, if it reads."""
    scripts: list[_Script] = []
    command_next = False
    for arg in args:
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 1:
            command_next = command_next or "c" in arg[1:]
            continue
        scripts.append((arg, "bash", _MUST if command_next else _MAYBE))
        command_next = False
    return scripts


def _decode_powershell(text: str) -> str | None:
    """An `-EncodedCommand` argument as PowerShell decodes it: whitespace and padding forgiven,
    an odd trailing byte dropped. ``None`` when it is no base64 at all."""
    compact = re.sub(r"\s+", "", text)
    if not compact or re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact) is None:
        return None
    compact = compact.rstrip("=").replace("-", "+").replace("_", "/")
    try:
        data = base64.b64decode(compact + "=" * (-len(compact) % 4))
    except (binascii.Error, ValueError):
        return None
    return data[: len(data) - len(data) % 2].decode("utf-16-le", errors="replace")


def _powershell_parameter(arg: str) -> str | None:
    """`-Command`, `/Command`, `--command`: the parameter's name, lowercased; else ``None``."""
    if arg[:1] not in ("-", "/") or len(arg) < 2:
        return None
    return arg.lstrip("-/").casefold()


def _powershell_scripts(args: list[str], info: _CommandInfo) -> None:
    for position, arg in enumerate(args):
        name = _powershell_parameter(arg)
        if name and "command".startswith(name):
            info.scripts.append((" ".join(args[position + 1 :]), "powershell", _MUST))
            return
        if name and (name in ("e", "ec") or (len(name) > 1 and "encodedcommand".startswith(name))):
            decoded = _decode_powershell(args[position + 1]) if position + 1 < len(args) else None
            if decoded is None:
                info.opaque = True
            else:
                info.scripts.append((decoded, "powershell", _MUST))
            return
        info.scripts.append((arg, "powershell", _MAYBE))


def _ssh_scripts(args: list[str], info: _CommandInfo) -> None:
    """ssh runs the rest of its arguments, joined, in a shell; with none it starts one. On
    this machine that shell starts in the home directory."""
    position = 0
    while position < len(args) and args[position].startswith("-"):
        flag = args[position]
        attached = len(flag) > 2 and flag[1] in _SSH_VALUE_FLAGS
        position += 1 if attached or flag[-1] not in _SSH_VALUE_FLAGS else 2
    remote = args[position + 1 :]
    destination = args[position] if position < len(args) else ""
    if remote:
        script = " ".join(remote)
        if _local_destination(destination):
            script = f"cd; {script}"  # `ssh localhost 'make -C .cache/...'`
        info.scripts.append((script, "bash", _RUNS))
    else:
        info.code = True
        info.stdin = info.stdin or "bash"


def _env_split_string(rest: list[str]) -> str | None:
    """The string `env -S` / `env -iS` / `--split-string` splits into a command."""
    for at, arg in enumerate(rest):
        if arg.startswith("--split-string"):
            value = arg.partition("=")[2]
            return value or (rest[at + 1] if at + 1 < len(rest) else "")
        if arg.startswith("-") and not arg.startswith("--") and "S" in arg[1:]:
            value = arg[arg.index("S", 1) + 1 :]
            return value or (rest[at + 1] if at + 1 < len(rest) else "")
        if not arg.startswith("-"):
            return None
    return None


_LINK_VALUE_PARAMETERS = ("-target", "-value", "-root")


def _link_sources(words: list[str], index: int, program: str) -> set[int]:
    """The words a linker takes from: everything but the destination, or a named source."""
    if program in ("new-item", "ni", "new-psdrive", "ndr"):
        return {
            at + 1
            for at in range(index + 1, len(words) - 1)
            if any(name.startswith(words[at].casefold()) for name in _LINK_VALUE_PARAMETERS)
            and len(words[at]) > 2
        }
    if program in _POWERSHELL_COPIERS:
        # Everything but the destination: `-Path:~\.cache` carries its value attached.
        sources: set[int] = set()
        skip = False
        for at in range(index + 1, len(words)):
            word = words[at].casefold()
            if skip:
                skip = False
            elif word.startswith("-dest"):
                skip = ":" not in word
            else:
                sources.add(at)
        return sources
    operands = [at for at in range(index + 1, len(words)) if not words[at].startswith("-")]
    if program in ("mklink", "junction", "subst"):
        return set(operands[-1:])  # `mklink /J link target`
    targeted = any(
        word in ("-t", "--target-directory") or word.startswith(("--target-directory=", "-t/"))
        for word in words[index + 1 :]
    )
    if program in ("tar", "zip", "7z", "net", "mount", "bindfs") or targeted:
        return set(operands)
    return set(operands[:-1]) if len(operands) > 1 else set(operands)


_POWERSHELL_COPIERS = frozenset(
    {"copy-item", "cpi", "move-item", "mi", "compress-archive", "copy", "move"}
)
_PYTHON = re.compile(r"(?:python[0-9.]*w?|py|pypy[0-9.]*)")
_PYTHON_COPIERS = frozenset({"zipfile", "tarfile", "shutil"})


def _module_sources(words: list[str], index: int) -> set[int]:
    """`python -m zipfile -c out.zip DIR`, `-m tarfile`: what the module archives."""
    rest = words[index + 1 :]
    for at, word in enumerate(rest[:-1]):
        if word == "-m":
            if rest[at + 1] not in _PYTHON_COPIERS:
                return set()
            start = index + at + 3
            return {position for position in range(start, len(words)) if words[position][:1] != "-"}
        if not word.startswith("-"):
            return set()  # a script runs, not a module
    return set()


# Container tools whose `-v`/`--volume`/`--mount` binds a host directory into a container
# the program then runs in, read-write unless told otherwise.
_CONTAINER_TOOLS = frozenset({"docker", "podman", "nerdctl"})
_MOUNT_HOST = re.compile(
    r"(?:[A-Za-z]:(?=[\\/]))?(?:\$env:|\$\{env:[^}]*\}|\$\{[^}]*\}|[^:])*", re.IGNORECASE
)


def _container_mounts(args: list[str]) -> list[tuple[str, str]]:
    """`-v HOST:PATH` and `--mount src=HOST,dst=PATH` as (PATH, HOST)."""
    pairs: list[tuple[str, str]] = []
    for at, arg in enumerate(args):
        following = args[at + 1] if at + 1 < len(args) else ""
        if arg in ("-v", "--volume") or arg.startswith("--volume="):
            spec = following if "=" not in arg else arg.partition("=")[2]
            match = _MOUNT_HOST.match(spec)
            if match and match.group() and spec[match.end() : match.end() + 1] == ":":
                inside = spec[match.end() + 1 :].split(":", 1)[0]
                pairs.append((inside, match.group()))
        elif arg == "--mount" or arg.startswith("--mount="):
            fields = dict(
                field.strip().partition("=")[::2]
                for field in (arg.partition("=")[2] if "=" in arg else following).split(",")
            )
            host = fields.get("source") or fields.get("src")
            mounted = fields.get("target") or fields.get("dst") or fields.get("destination")
            if host and mounted:
                pairs.append((mounted, host))
    return pairs


def _mount_hosts(args: list[str]) -> list[str]:
    """The host sides of `-v HOST:PATH`, `--volume=HOST:PATH` and `--mount
    type=bind,src=HOST,...`: a drive letter's colon and `$env:NAME` separate nothing."""
    hosts: list[str] = []
    for at, arg in enumerate(args):
        following = args[at + 1] if at + 1 < len(args) else ""
        if arg in ("-v", "--volume"):
            spec = following
        elif arg.startswith("--volume="):
            spec = arg.partition("=")[2]
        elif arg == "--mount" or arg.startswith("--mount="):
            fields = (arg.partition("=")[2] if "=" in arg else following).split(",")
            hosts.extend(
                field.partition("=")[2]
                for field in fields
                if field.partition("=")[0].strip().casefold() in ("source", "src")
            )
            continue
        else:
            continue
        match = _MOUNT_HOST.match(spec)
        if match and match.group():
            hosts.append(match.group())
    return hosts


_MAPFILE_VALUE_FLAGS = frozenset("dnOsuCc")


def _callback(args: list[str]) -> str | None:
    """The `-C callback` `mapfile` and `readarray` evaluate, attached or not; else ``None``."""
    at = 0
    while at < len(args):
        arg = args[at]
        if not arg.startswith("-") or arg in ("-", "--"):
            return None
        at += 1
        for offset in range(1, len(arg)):
            if arg[offset] not in _MAPFILE_VALUE_FLAGS:
                continue
            attached = arg[offset + 1 :]
            value = attached or (args[at] if at < len(args) else "")
            if arg[offset] == "C":
                return value
            at += 0 if attached else 1  # the value was the next word
            break
    return None


# Options that set the directory a program works in, and so the one its relative arguments
# name paths from: `make -C`, `git -C`, `env -C`, `tar -C`, `uv --directory`, `npm
# --prefix`, `yarn --cwd`, `docker compose --project-directory`, `wsl --cd`, PowerShell's
# `-WorkingDirectory`.
_DIRECTORY_SHORT = frozenset({"-C"})
_DIRECTORY_LONG = frozenset(
    {"--directory", "--chdir", "--cwd", "--prefix", "--project-directory", "--cd", "--dir"}
    | {"--project", "--rootdir", "--working-directory", "--workdir-host"}
)


def _directory_option(words: list[str], index: int) -> str | None:
    """The directory ``words[index]`` sets as an option, attached or in the next word."""
    word = words[index]
    following = words[index + 1] if index + 1 < len(words) else None
    if word in _DIRECTORY_SHORT:
        return following
    if word.startswith("-C") and len(word) > 2 and word[2] not in "-=":
        return word[2:]  # `-Cdir`
    name, equals, value = word.partition("=")
    if name in _DIRECTORY_LONG:
        return value if equals else following
    folded, colon, _ = word.casefold().partition(":")
    if len(folded) >= 4 and "-workingdirectory".startswith(folded):
        return word.partition(":")[2] if colon else following
    return None


# Programs that copy, clone or open a shell over ssh: `[user@]host:path` names a path on
# that host, and on this machine it is a local path (relative to home).
_TRANSPORTS = frozenset({"scp", "rsync", "sftp", "git", "ssh"})
_REMOTE_SPEC = re.compile(r"^(?:[^@\s/]+@)?(\[[^\]]+\]|[^:/\s\[\]]+):(?!:)(.*)$", re.S)
_REMOTE_URL = re.compile(
    r"^(?:git\+)?ssh(?:\+git)?://(?:[^@/\s]+@)?(\[[^\]]+\]|[^:/\s\[\]]+)(?::[0-9]*)?(/.*)?$",
    re.I | re.S,
)
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "::1", "[::1]", "0.0.0.0"})


def _this_machine(host: str) -> bool:
    """`localhost`, a loopback address, or this machine's own name."""
    name = host.casefold().rstrip(".")
    if name in _LOCAL_NAMES or _LOOPBACK.fullmatch(name) is not None:
        return True
    own = {os.environ.get("COMPUTERNAME", "").casefold()}
    with contextlib.suppress(OSError):
        own.add(socket.gethostname().casefold())
    own |= {entry.split(".", 1)[0] for entry in own}
    return bool(name) and (name in own or name.split(".", 1)[0] in own - {""})


def _local_destination(word: str) -> bool:
    """Whether an ssh destination (`[user@]host`, `ssh://...`) is this machine."""
    url = _REMOTE_URL.match(word)
    host = url.group(1) if url else word.rpartition("@")[2]
    return _this_machine(host)


def _local_transport_paths(args: list[str]) -> list[str]:
    """The paths `scp localhost:.cache/x .`, `rsync user@127.0.0.1:x`, `git clone
    ssh://localhost/~/x` and kin name on this machine: a relative path is relative to home,
    `~/` likewise, an absolute path is itself."""
    paths: list[str] = []
    for arg in args:
        for value in (arg, arg.partition("=")[2] if arg.startswith("--") else ""):
            url = _REMOTE_URL.match(value)
            spec = None if url else _REMOTE_SPEC.match(value)
            match = url or spec
            if match is None or not _this_machine(match.group(1).strip("[]")):
                continue
            remote = match.group(2) or ""
            if url and remote.startswith("/~"):
                remote = remote[1:]  # `ssh://host/~/x`
            if remote.startswith("~") or _is_rooted(remote):
                paths.append(remote)
            else:
                paths.append(f"~/{remote}")
    return paths


def _command_variable(word: str) -> str | None:
    """The value of `GIT_SSH_COMMAND=...`, `PAGER=...`, `$env:EDITOR=...`, else ``None``."""
    assignment = _ASSIGNMENT_WORD.match(word) or _POWERSHELL_ENV_ASSIGNMENT.match(word)
    if assignment is None or _COMMAND_VARIABLE.fullmatch(assignment.group(1)) is None:
        return None
    return assignment.group(3)


def _inspect_command(words: list[str], *, fed: bool = False, grammar: str = "bash") -> _CommandInfo:
    """``fed``: the command reads a pipe or a here-string written in the same command line;
    ``grammar``: bash, powershell or cmd, which decide what a listing's options mean."""
    info = _CommandInfo(words=list(words), patterns=_pattern_indices(words))
    position = _command_index(words)
    for index, raw in enumerate(words):
        word = raw.lstrip("(").rstrip(")")
        program = _program(word) if word else ""
        if "[" in word or word.startswith("(("):
            info.reevaluates = True  # arithmetic, a subscript
        assignment = _ASSIGNMENT_WORD.match(word)
        if assignment and assignment.group(3).startswith("("):
            info.value = True  # an array assignment
        command = _command_variable(raw)
        spaced = _POWERSHELL_ENV_ASSIGNMENT.match(raw) if raw[:1] == "$" else None
        following = words[index + 1 : index + 3]
        if (
            spaced
            and not spaced.group(2)
            and following[:1] == ["="]
            and len(following) > 1
            and _COMMAND_VARIABLE.fullmatch(spaced.group(1))
        ):
            command = following[1]  # `$env:GIT_SSH_COMMAND = '...'`
        if command is not None:
            info.scripts.append((command, "bash", _RUNS))
            info.reevaluates = True  # a prompt or a pager command is expanded when it is used
        python = _PYTHON.fullmatch(program) is not None
        directory = _directory_option(words, index)
        if directory is not None:
            # PowerShell binds `-WorkingDirectory` wherever it stands: it covers every word.
            start = -1 if words[index].casefold().startswith("-wo") else index
            info.directories.append((start, directory))
        if program in _TRANSPORTS:
            _spend(len(words) - index)
            info.extra.extend(_local_transport_paths(words[index + 1 :]))
        if program in _SEARCHERS:
            _spend(len(words) - index)
            info.searches.extend(_search_roots(words, index, program, grammar))
            info.code |= _search_runner(words, index, program)  # `fd x ~ -x python`
        if program not in _INSPECTED and word not in _FIND_ACTIONS and word != "." and not python:
            continue
        # What follows the word, charged to the budget: a command that repeats a program a
        # thousand times must not read its own words a thousand times for free.
        _spend(len(words) - index)
        rest = words[index + 1 :]
        if program in _SHELLS:
            info.code = info.reevaluates = True
            info.stdin = info.stdin or "bash"
            info.scripts.extend(_shell_scripts(rest))
        elif program in _POWERSHELLS:
            info.code = info.reevaluates = True
            info.stdin = info.stdin or "powershell"
            # PowerShell reads a typographic dash as `-`, on its own command line too.
            _powershell_scripts([arg.translate(_POWERSHELL_TRANSLATION) for arg in rest], info)
        elif program == "cmd":
            info.code = True
            info.scripts.append((" ".join(rest).translate(_DASHES), "cmd", _MUST))
        elif program in ("invoke-expression", "iex", "invoke-command", "icm"):
            info.code = True
            info.scripts.append((" ".join(rest), "powershell", _RUNS))
        elif program in ("eval", "trap"):
            info.code = info.reevaluates = True  # read as part of this command, see `_splice`
        elif program == "source" or (word == "." and index == position):
            info.code = info.reevaluates = True
            info.stdin = info.stdin or "bash"
            info.scripts.extend((arg, "bash", _MAYBE) for arg in rest)
        elif program == "alias":
            # An alias's text is a command the shell runs where the alias is used.
            info.scripts.extend((arg.split("=", 1)[1], "bash", _RUNS) for arg in rest if "=" in arg)
        elif program == "git":
            # `git -c core.pager=...`, `alias.x=!...`: git hands such a value to a shell.
            info.scripts.extend(
                (value.split("=", 1)[1], "bash", _RUNS)
                for flag, value in itertools.pairwise(rest)
                if flag == "-c" and "=" in value
            )
        elif program == "ssh":
            _ssh_scripts(rest, info)
        elif program in _SCRIPT_RUNNERS:
            info.scripts.extend((arg, "bash", _RUNS) for arg in rest)
            info.scripts.append((" ".join(rest), "bash", _RUNS))
        elif program in _LOGIN_SHELLS and index == position:
            info.scripts.extend(_shell_scripts(rest))
            flags = "".join(arg[1:] for arg in rest if arg.startswith("-") and arg != "--")
            interactive = program in ("su", "runuser") and "c" not in flags
            if interactive or (program in ("sudo", "doas") and set(flags) & set("si")):
                info.code = True  # a shell reading its input
                info.stdin = info.stdin or "bash"
        elif program == "env":
            split = _env_split_string(rest)
            if split is not None:
                info.scripts.append((split, "bash", _MUST))
        elif program in ("read", "mapfile", "readarray"):
            # A value read from what an earlier part of this command line prints: the guard
            # sees the text that printed it, not the value. A file read is a file read back.
            info.fed |= fed
            # An array read (`read -a`, `mapfile`, `readarray`): 0.6.1 refused one beside a
            # way to the cache, and so does this guard.
            info.array_read |= program != "read" or any(
                arg.startswith("-") and "a" in arg[1:] for arg in rest if arg != "--"
            )
            callback = _callback(rest) if program != "read" else None
            if callback is not None:
                info.code = True  # `mapfile -C "$c"` runs the text of `c` for each line
                info.scripts.append((callback, "bash", _RUNS))
        elif program in ("declare", "typeset", "local", "readonly", "export"):
            flags = "".join(arg[1:] for arg in rest if arg.startswith("-"))
            info.value |= bool(set(flags) & set("aAn"))  # an array or a name reference
            info.reevaluates |= "i" in flags  # an integer: assignments are arithmetic
        elif program == "xargs":
            replaced, reader = _xargs(words, index)
            info.words[index + 1 :] = replaced
            info.code |= not reader
        elif program == "parallel":
            info.code = True
            info.words[index + 1 :] = [_PARALLEL_PLACEHOLDER.sub(_UNKNOWN, arg) for arg in rest]
        elif word in _FIND_ACTIONS:
            info.code |= not _runs_a_reader(_action_command(words, index))
        elif program in ("call", "new-psdrive", "ndr"):
            info.value = True  # cmd expands the line again; a drive the guard does not map
        if program == "find":
            info.extra.extend(_find_descendants(words, index))
        link_item = program in ("new-item", "ni") and any(
            arg.casefold() in _LINK_ITEM_TYPES for arg in rest
        )
        # `pip install .` and `make install` name a subcommand, not the program `install`.
        linker = program in _LINKERS and (program != "install" or index == _run_index(words))
        if linker or link_item:
            info.sources |= _link_sources(words, index, program)
        if python:
            info.sources |= _module_sources(words, index)
        if program in _CONTAINER_TOOLS:
            info.mounts.extend(_mount_hosts(rest))
            info.directories.extend(
                (index, value)
                for flag, value in itertools.pairwise(rest)
                if flag in ("-w", "--workdir")
            )  # the container's working directory, below its mounts
            info.directories.extend(
                (index, arg.partition("=")[2]) for arg in rest if arg.startswith("--workdir=")
            )
        if program in _REEVALUATORS:
            info.reevaluates = True  # `eval`, `let`, `test -v`
    return info


# --- search patterns ------------------------------------------------------------------------
#
# A word grep searches for is not a path: `grep -c hungry-crab` names nothing. Only the options
# that certainly take a value are skipped, so a misread option can only make a pattern count as
# a path, never the reverse.

_GREP_VALUES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    name: (
        frozenset("efmABCdD"),
        frozenset(
            {"regexp", "file", "max-count", "after-context", "before-context", "context"}
            | {"directories", "devices", "include", "exclude", "exclude-dir", "label"}
        ),
    )
    for name in ("grep", "egrep", "fgrep", "zgrep")
}
_GREP_VALUES["rg"] = (
    frozenset("efgtTmMABCjdEr"),
    frozenset(
        {"regexp", "file", "glob", "iglob", "type", "type-not", "type-add", "max-count"}
        | {"max-columns", "after-context", "before-context", "context", "threads"}
        | {"max-depth", "encoding", "replace", "pre-glob", "ignore-file", "sort", "sortr"}
        | {"max-filesize", "path-separator", "colors", "engine"}
    ),
)
_GIT_GREP_VALUES = (frozenset("efABCm"), frozenset({"max-depth", "threads", "file", "context"}))
_SELECT_STRING_VALUES = frozenset(
    {"-path", "-literalpath", "-lp", "-encoding", "-context", "-exclude", "-include"}
    | {"-inputobject", "-culture"}
)


def _grep_patterns(
    words: list[str], index: int, values: tuple[frozenset[str], frozenset[str]]
) -> set[int]:
    short, long = values
    explicit: set[int] = set()
    positional: int | None = None
    position = index + 1
    options = True
    while position < len(words):
        word = words[position]
        if options and word == "--":
            options = False
        elif options and word.startswith("--") and len(word) > 2:
            name, equals, _ = word[2:].partition("=")
            if name == "regexp":
                explicit.add(position if equals else position + 1)
            if name in long and not equals:
                position += 1
        elif options and word.startswith("-") and len(word) > 1:
            for offset in range(1, len(word)):
                if word[offset] in short:
                    attached = offset + 1 < len(word)
                    if word[offset] == "e":
                        explicit.add(position if attached else position + 1)
                    if not attached:
                        position += 1
                    break
        elif positional is None:
            positional = position
        position += 1
    if explicit:
        return explicit
    return {positional} if positional is not None else set()


def _select_string_patterns(words: list[str], index: int) -> set[int]:
    explicit: set[int] = set()
    positional: int | None = None
    position = index + 1
    while position < len(words):
        word = words[position]
        if word.startswith("-") and len(word) > 1:
            name, colon, _ = word.casefold().partition(":")
            if _is_pattern_parameter(name):
                explicit.add(position if colon else position + 1)
                position += 0 if colon else 1
            elif name in _SELECT_STRING_VALUES and not colon:
                position += 1
        elif positional is None:
            positional = position
        position += 1
    if explicit:
        return explicit
    return {positional} if positional is not None else set()


def _findstr_patterns(words: list[str], index: int) -> set[int]:
    explicit = {at for at, word in enumerate(words) if at > index and word.casefold()[:3] == "/c:"}
    if explicit:
        return explicit
    for at in range(index + 1, len(words)):
        if not words[at].startswith("/"):
            return {at}
    return set()


def _git_subcommand(words: list[str], index: int) -> int | None:
    position = index + 1
    while position < len(words):
        word = words[position]
        if word in ("-C", "-c"):
            position += 2
        elif word.startswith("-"):
            position += 1
        else:
            return position
    return None


def _pattern_indices(words: list[str]) -> set[int]:
    """The search patterns of the program this command runs; a word that merely reads `grep`
    (`set -- grep -e ...`) makes no pattern of what follows it."""
    index = _run_index(words)
    if index is None:
        return set()
    program = _program(words[index].lstrip("(")) if words[index].lstrip("(") else ""
    if program in _GREP_VALUES:
        return _grep_patterns(words, index, _GREP_VALUES[program])
    if program == "git":
        sub = _git_subcommand(words, index)
        if sub is not None and words[sub] == "grep":
            return _grep_patterns(words, sub, _GIT_GREP_VALUES)
    if program in ("select-string", "sls"):
        return _select_string_patterns(words, index)
    if program == "findstr":
        return _findstr_patterns(words, index)
    return set()


# --- the hint rule --------------------------------------------------------------------------
#
# The scope gate models variables, parameter defaults, `~`, globs, braces, assignments, `cd`,
# nested shells and substitutions. It does not model what a parameter operator, an array, a
# positional parameter or a PowerShell expression computes from the command's own text (a
# *value* construct), a value read from what the command prints (a *fed* value), nor text a
# shell, `eval` or `xargs` turns into commands unseen (a *code* construct). A command that
# carries a hint of the cache and uses one of those is refused, as `refuses_unmodelled` says.
#
# A strong hint spells a way to the cache: a glob or 8.3 alias that can match its name, its
# parent spelled from home (`~/.cache`, `~/.c*`, `../../.cache`) alone or followed by a name
# that can be the cache's or by text the guard does not know, or its name in a word shaped like
# a path (`x/hungry-crabX`). A sibling is the parent spelled from home followed by another name
# (`~/.cache/uv`); a weak hint is the bare name (`hungry-crab` as a package, a comparison
# operand, echo's text). Both count beside a value or a code construct, which can compute a way
# to the cache from them; the bare name also counts beside a fed value when the command names
# the parent too. A substitution that holds any hint is refused: its program computes its
# output from that text. A word grep searches for names no path, and a URL counts only for a
# way to the cache in its own path.

_UNMODELLED_REASON = (
    "command points at the prey cache through a construct the guard does not model "
    "(AGENTS.md rule 3)"
)
_BASH_UNMODELLED = re.compile(
    r"\$\{(?:!|[0-9@*]|[A-Za-z_][A-Za-z0-9_]*(?:\[|[%#/^,@]|:(?![-=+?])))"  # `${x%y}`, `${a[0]}`
    r"|\$[0-9@*]"  # positional parameters
    r"|\$['\"]"  # `$'...'`, `$"..."`
    r"|\b(?:CDPATH|IFS)\b"
)
_SPECIAL_PARAMETER = re.compile(r"\$[?$!#-]")
# An unquoted brace expansion: `{a,b}`, `{1..3}`, `x{,}`; not `${x:-a,b}`.
_BRACE_EXPANSION = re.compile(r"(?<![$\\])\{[^{}\s]*(?:,|\.\.)[^{}\s]*\}")
_DOUBLE_QUOTED = re.compile(r'"[^"]*"')
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
# Of those, the ones that compute text from the command's own text: method calls, a variable's
# properties (`$p.Parent`), `+`, the escape character, static members, the string operators and
# the path cmdlets. A group computes its output from its own text, which is judged as a command
# of its own: a hint there refuses the command as a hint in a bash substitution does.
_POWERSHELL_TRANSFORMS = re.compile(
    r"\.[A-Za-z_]\w*\(|\$[\w:]+\.[A-Za-z_]|[+`]|::"
    r"|(?<![\w-])-[ci]?(?:f|join|replace|split)(?![\w-])"
    r"|\b(?:join-path|split-path|resolve-path|convert-path|invoke-expression|iex"
    r"|invoke-command|icm)\b",
    re.IGNORECASE,
)


def _homes(cwd: Path) -> list[Path]:
    """The home directory as Python sees it and as `$HOME` names it, when they differ."""
    budget = _budget.get()
    if budget is not None and budget.homes is not None:
        return budget.homes
    homes = [canonical_path(Path.home(), cwd=cwd)]
    env_home = os.environ.get("HOME")
    if env_home:
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            homes.append(canonical_path(Path(_msys_drive(env_home)), cwd=cwd))
    homes = list(dict.fromkeys(homes))
    if budget is not None:
        budget.homes = homes
    return homes


def _hint_patterns(root: Path, homes: list[Path]) -> list[re.Pattern[str]]:
    """What in a share path's text points at the cache: its name, and its parent under home."""
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


def _names_root(text: str, scope: _Scope) -> bool:
    """The cache's own name, or `hungry-` for the default root, anywhere in ``text``."""
    normalized = text.replace("\\", "/").casefold()
    name = scope.root.name.casefold()
    return bool(name) and (
        name in normalized or (name == "hungry-crab" and "hungry-" in normalized)
    )


# A weak hint names the cache bare; a sibling spells the cache's parent from home followed by a
# name that cannot be the cache's (`~/.cache/uv`); a strong hint spells a way to the cache.
_WEAK, _SIBLING, _STRONG = 1, 2, 3
_BLANKS = re.compile(r"[ \t\n]")


def _hint_level(raw: str, candidate: str, scope: _Scope) -> tuple[int, bool]:
    """How strongly one expanded word hints at the cache, and whether it names its parent."""
    _spend(len(candidate) // 32 + 2)
    url = _URL.match(candidate)
    text = candidate[url.end() :] if url else candidate
    parent = scope.root.parent.name
    mentions_parent = False
    for component in _components(text):
        if component in ("", ".", ".."):
            continue
        matcher = _component(component)
        if matcher is None:
            continue
        dynamic = _dynamic(component) or matcher.alias
        if matcher.literal and dynamic and matcher.matches(scope.root.name):
            return _STRONG, True
        mentions_parent |= bool(parent) and matcher.literal and matcher.matches(parent)
    toward = _toward_the_root(text, scope)
    if toward == _STRONG:
        return _STRONG, True
    if url is None and _names_root(candidate, scope):
        shaped = _BLANKS.search(raw) is None and _SEPARATORS.search(candidate) is not None
        return (_STRONG if shaped else _WEAK), mentions_parent or bool(toward)
    return toward, mentions_parent or bool(toward)


def _toward_the_root(candidate: str, scope: _Scope) -> int:
    """The cache's parent spelled from home, directly or relative to where the command runs:
    alone or followed by what can lead to the cache, `_STRONG`; followed by a name that cannot,
    `_SIBLING`; else 0."""
    root = scope.root
    if scope.anchors is None:
        scope.anchors = [h for h in scope.homes if h != root.parent and _is_within(root, h)]
    homes = scope.anchors
    if not homes:
        return 0
    found = 0
    for spelling in _msys_spellings(candidate):
        text = spelling.replace("\\", "/")
        if _is_rooted(text):
            absolutes = [text]
        else:
            # Without `..` the way to the cache runs through the directory itself. A word that
            # starts with what the guard does not know (`$f`) spells nothing of it: it counts
            # only where the command runs beside the cache or in it, not anywhere above.
            climbs = ".." in text.split("/")
            lettered = _leads_with_text(text)
            absolutes = [
                f"{cwd.as_posix().rstrip('/')}/{text}"
                for cwd in scope.cwds
                if (climbs or scope.related(cwd)) and (lettered or _is_within(cwd, root.parent))
            ]
        for spelled in absolutes:
            # `C:/Users/me/../me/.cache` names the parent too: compare normalised spellings.
            absolute = os.path.normpath(_win32_trim(spelled) if os.name == "nt" else spelled)
            absolute = absolute.replace("\\", "/")
            folded = absolute.casefold()
            for home in homes:
                anchor = home.as_posix().casefold().rstrip("/")
                if folded.startswith(anchor + "/"):
                    level = _walks_toward(
                        absolute[len(anchor) + 1 :].split("/"), root.parts[len(home.parts) :]
                    )
                    if level == _STRONG:
                        return level
                    found = max(found, level)
    return found


def _leads_with_text(text: str) -> bool:
    """Whether a relative path's first component spells something: not only expansions."""
    for component in text.split("/"):
        if component not in ("", "."):
            return any(char not in _MARKERS for char in component)
    return False


def _walks_toward(components: list[str], target: tuple[str, ...]) -> int:
    stack: list[str] = []
    for component in components:
        if component in ("", "."):
            continue
        if component == "..":
            if not stack:
                return 0
            stack.pop()
        else:
            stack.append(component)
    for position, component in enumerate(stack):
        matcher = _component(component)
        if position >= len(target) - 1:
            # The parent is named. A name after it that can be the cache's, or text the guard
            # does not know (it can hold `/../`), leads on to the cache. Any other name is a
            # sibling (`~/.cache/uv`), which a parameter operator or a program such as `dirname`
            # can still turn into the parent.
            onward = matcher is None or matcher.matches(target[-1])
            if onward or any(_UNKNOWN in part for part in stack[position:]):
                return _STRONG
            return _SIBLING
        if matcher is None:
            return _STRONG
        if not matcher.matches(target[position]):
            return 0
    return _STRONG if len(stack) >= len(target) - 1 else 0


def _powershell_split_hint(words: list[str], scope: _Scope) -> bool:
    """`Join-Path $HOME .cache ...`, `$HOME + '\\.cache\\hun' + 'gry-crab'`: PowerShell builds
    the path from separate strings, so the parent's name as a component anywhere, beside a home
    spelling anywhere, is a hint — only toward the cache: `.cache` alone, or followed by the
    start of the cache's name (`'\\.cache\\hun'`), not by another child (`'.cache\\pip'`)."""
    normalized = " ".join(words).replace("\\", "/").casefold().replace("`", "")
    if re.search(_HOME_SPELLING, normalized) is None:
        return False
    root = scope.root
    name = root.name.casefold()
    for home in scope.homes:
        if home not in root.parent.parents:
            continue
        first = re.escape(root.parent.relative_to(home).parts[0].casefold())
        pattern = rf"(?:^|[/'\"\s(,]){first}(/[^/'\"\s),]*)?(?=$|[/'\"\s),])"
        for match in re.finditer(pattern, normalized):
            child = match.group(1)
            if child is None or name.startswith(child[1:]):
                return True
    return False


# --- judging a command ----------------------------------------------------------------------

_MAX_LEVELS = 8  # a substitution in a script in a substitution ...
_MAX_SPLICES = 16


@dataclass
class _Verdict:
    reaches: bool = False  # a word can be a path in the cache
    strong: bool = False  # a word spells a way to the cache
    sibling: bool = False  # a word spells the cache's parent and another name in it
    weak: bool = False  # a word names the cache bare
    parent: bool = False  # a word names the directory that holds the cache
    code: bool = False  # text becomes commands the guard never sees
    value: bool = False  # text the command computes from its own text, unseen
    computed: bool = False  # a PowerShell group or script block's output
    fed: bool = False  # a value read from what an earlier part of the command prints
    runs: bool = False  # a program that does more than list, print or filter text
    group_strong: bool = False  # a substitution whose own text spells a way to the cache
    group_weak: bool = False  # a substitution whose own text names it bare
    near_group: bool = False  # a substitution that runs in the cache or beside it
    near: bool = False  # the command can run in the cache, beside it, or anywhere
    linked: bool = False  # the cache or the directory that holds it is linked or copied
    opaque: bool = False  # a shell is handed a script the guard cannot decode
    searched: bool = False  # a search walks the cache or a directory above it
    piped_run: bool = False  # a program that does more than list reads a pipe
    grouped: bool = False  # a substitution, or a PowerShell group, gives a value
    special: bool = False  # bash's `$(...)`, `` `...` ``, `$?`, `$$`, `$!`, `$#`, `$-`
    file_fed: bool = False  # a value read from a file the command redirects in
    commands: int = 0
    spelled: list[str] = field(default_factory=list)  # PowerShell words, see the split hint

    def absorb(self, other: _Verdict) -> None:
        for name in (
            "reaches",
            "strong",
            "sibling",
            "weak",
            "parent",
            "code",
            "value",
            "computed",
            "fed",
            "runs",
            "group_strong",
            "group_weak",
            "near_group",
            "near",
            "linked",
            "opaque",
            "searched",
            "piped_run",
            "grouped",
            "special",
            "file_fed",
        ):
            setattr(self, name, getattr(self, name) or getattr(other, name))
        self.commands += other.commands
        self.spelled.extend(other.spelled[: max(0, 4096 - len(self.spelled))])

    def runs_what_a_search_finds(self) -> bool:
        """A search that walks through the cache (`find ~`, `fd x ~`, `rg --files ~`,
        `Get-ChildItem ~ -Recurse`, `locate`) feeding something that runs: a non-reader
        `xargs`, `find -exec` or `fd -x`, a program reading the pipe, or a command that
        runs a program on a value read from input or from a substitution."""
        if not self.searched:
            return False
        read = self.fed or self.file_fed or self.grouped
        return self.code or self.piped_run or (self.runs and read)

    def refuses_unmodelled(self) -> bool:
        # A substitution computes its output from its own text: a way to the cache, a sibling or
        # the bare name in it can come out as a path.
        if self.group_strong or self.group_weak:
            return True
        # As 0.6.1 did: a way to the cache spelled beside a substitution or a special
        # parameter is not judged further.
        if self.strong and self.special:
            return True
        # Text the command computes from its own text (a parameter operator, a method call, an
        # array, code a shell reads) can turn the bare name or a sibling into a way to the cache.
        if (self.weak or self.sibling) and (self.code or self.value):
            return True
        # What a program prints about a sibling can be the parent (`dirname`, `find
        # -printf %h`); read back, it is harmless only while the command just lists it.
        if self.sibling and self.fed and self.runs:
            return True
        # A value read from input, or a substitution run where its output can be the cache's
        # name, finds the cache only where a word spells the way to it, or names the cache
        # next to its parent or where the command runs beside it (`cd "$(tr , / < f)"` with
        # the name in `f`); a sibling's listing cannot hold the cache.
        pointed = self.strong or (self.weak and (self.parent or self.near))
        others = self.computed or self.fed or self.near_group
        return pointed and (self.code or self.value or others)


# Programs that only list, print or filter text: a value read from their output reaches
# nothing while the command runs nothing else.
_LISTERS = frozenset(
    {"ls", "dir", "du", "df", "stat", "echo", "printf", "read", "mapfile", "readarray"}
    | {"sort", "uniq", "head", "tail", "wc", "cat", "grep", "egrep", "fgrep", "cut", "tr"}
    | {"column", "basename", "true", "false", "test", "[", "[[", ":", "nl", "tac", "rev"}
    | {"dirname", "realpath", "readlink"}
    | {"done", "fi", "esac", "}", ")", "then", "else", "do", "in"}
    | {"find", "fd", "fdfind", "rg", "ag", "ack", "tree", "locate", "plocate", "mlocate"}
)
_POWERSHELL_LISTERS = frozenset(
    {"get-childitem", "gci", "ls", "dir", "get-item", "gi", "get-content", "gc", "cat"}
    | {"type", "select-object", "select", "select-string", "sls", "measure-object"}
    | {"measure", "sort-object", "sort", "format-table", "ft", "format-list", "fl"}
    | {"write-output", "echo", "write", "write-host", "out-string", "test-path"}
    | {"foreach-object", "%", "where-object", "where", "?", "group-object"}
)


# Bash builtins that never read their input: a pipe into one feeds nothing.
_IGNORES_INPUT = frozenset(
    {"cd", "pushd", "popd", "export", "unset", "set", "shift", "exit", "return", "wait"}
)


def _ignores_input(words: list[str]) -> bool:
    index = _run_index(words)
    return index is not None and index < len(words) and words[index] in _IGNORES_INPUT


def _lists_or_prints(words: list[str], *, powershell: bool = False) -> bool:
    """Whether one simple command runs nothing but a program that lists or prints."""
    if powershell:
        head = words[0].casefold()
        return head.startswith("$") or head in _POWERSHELL_LISTERS
    index = _run_index(words)
    if index is None or index >= len(words):
        return True  # assignments alone
    word = words[index].lstrip("(")
    if _program(word) == "rg" and any(_RG_PRE.match(arg) for arg in words[index + 1 :]):
        return False  # `rg --pre PROGRAM` runs PROGRAM on every file
    return not word or word in _LISTERS or _program(word) in _LISTERS


def _near(scope: _Scope) -> bool:
    """Whether a program's output could be the cache's name: the command can be in the cache,
    in the directory that holds it, or somewhere the guard does not know."""
    parent = os.path.normcase(str(scope.root.parent))
    return scope.unknown_cwd or any(
        _is_within(cwd, scope.root) or os.path.normcase(str(cwd)) == parent for cwd in scope.cwds
    )


# A URL names a remote resource. What a program prints after fetching one is a path that program
# computes, so the cache's name inside a URL is no hint; `file:` URLs name local files.
_URL = re.compile(r"(?!file:)[A-Za-z][A-Za-z0-9+.-]+://", re.IGNORECASE)


def _holds_the_cache(candidate: str, scope: _Scope) -> bool:
    """Whether a word a linker or a copier takes from can name the cache, a path in it, or the
    directory that holds it, so that the copy holds the cache. Home and the directories above
    the cache's parent hold much else, and copying them is ordinary work (`cp -r . ../backup`
    at home); a copy of home is found where it is used by the cache's spelling in it."""
    if candidate.startswith(_UNKNOWN):
        return False
    holder = scope.root.parent
    if any(marker in candidate for marker in _MARKERS) or any(
        _SHORT_NAME.fullmatch(component) for component in _components(candidate)
    ):
        return any(_is_within(found, holder) for found in _pattern_directories(candidate, scope))
    for spelling in _msys_spellings(candidate):
        for base in _bases(spelling, scope):
            try:
                path = _word_path(spelling, cwd=base)
            except (OSError, RuntimeError, ValueError):
                continue
            held = os.path.normcase(str(path)) == os.path.normcase(str(holder))
            if held or _is_within(path, scope.root):
                return True
    return False


def _mounts_the_cache(info: _CommandInfo, scope: _Scope) -> bool:
    """A container bind mount of the cache, its parent or anything in them: the container
    gets the cache to run in, and `:ro` keeps nothing from running."""
    return any(
        _holds_the_cache(candidate, scope)
        for host in info.mounts
        for candidate in _expand_word(host, scope)
    )


def _names_a_cache_copy(text: str, scope: _Scope) -> bool:
    """A path through a directory named like the cache's parent into one named like the cache,
    below another directory: `/mnt/backup/.cache/./hungry-crab/...`, a copy or a mount of the
    home directory. It is read as the cache, as the plain spelling `x/.cache/hungry-crab`
    always was; `.cache/hungry-crab` alone, relative, is resolved where the command runs."""
    parent, name = scope.root.parent.name, scope.root.name
    if not parent or not name or _URL.match(text):
        return False
    literal = not any(marker in text for marker in _MARKERS) and "~" not in text
    if literal and name.casefold() not in text.casefold():
        return False  # no wildcard or alias, and the cache's name is not there
    stack: list[str] = []
    for component in _components(text):
        if component in ("", "."):
            continue
        if component == "..":
            if stack:
                stack.pop()
            continue
        stack.append(component)
    _spend(len(stack) // 8 + 1)
    for position in range(0 if _is_rooted(text) else 1, len(stack) - 1):
        first, second = _component(stack[position]), _component(stack[position + 1])
        if (
            first is not None
            and second is not None
            and first.literal
            and second.literal
            and first.matches(parent)
            and second.matches(name)
        ):
            return True
    return False


def _relative_text(text: str) -> bool:
    """A word that names a path from the directory a program works in."""
    return (
        bool(text)
        and not text.startswith(("-", "~", _LITERAL_TILDE))
        and not _is_rooted(text)
        and _URL.match(text) is None
    )


def _anchors(piece: str, scope: _Scope) -> bool:
    """Whether one expanded word names the cache, the directory that holds it or one above
    it, such as home: a program handed it may work there (`make -C ~`, `uv run --directory
    ~`, `docker compose --project-directory ~/.cache`, `Start-Process -WorkingDirectory`)."""
    if piece.startswith(_UNKNOWN) or any(marker in piece for marker in _MARKERS):
        return False
    for base in _bases(piece, scope):
        try:
            path = _word_path(piece, cwd=base)
        except (OSError, RuntimeError, ValueError):
            continue
        if scope.related(path):
            return True
    return False


_MAX_ANCHORS = 8


def _relocated(
    found: list[tuple[int, str]], info: _CommandInfo, named: list[str], scope: _Scope
) -> list[tuple[int, str]]:
    """The relative words of one command read from the directories it may work in: those
    its directory options set (each relative to the one before, as `git -C a -C b` does),
    and any word that names the cache, its parent or a directory above it. `make -C ~ -f
    .cache/hungry-crab/.../Makefile` reads `~/.cache/hungry-crab/.../Makefile`. Each word
    keeps the index of the one it comes from, so a pattern stays a pattern."""
    if len(info.directories) > _MAX_ANCHORS:
        raise _BudgetExceededError  # more directory changes than one command needs
    anchors: list[tuple[int, str]] = []
    chained: str | None = None
    for index, directory in info.directories:
        if chained is not None and _relative_text(directory):
            directory = f"{chained}/{directory}"
        chained = directory
        anchors.append((index, directory))
    relocated = [(index, directory) for index, directory in anchors]
    relocated += [
        (index, original)
        for index, text in found
        if (original := _unalias(text, scope)) is not None
    ]
    anchors.extend((-1, text) for text in named)
    if not anchors:
        return []
    for index, text in found:
        if index in info.patterns or not _relative_text(text):
            continue
        for start, directory in anchors:
            if index > start and directory != text:
                _spend()
                relocated.append((index, f"{directory.rstrip('/')}/{text}"))
    return relocated


def _judge_words(
    words: list[tuple[int, str]],
    info: _CommandInfo,
    scope: _Scope,
    verdict: _Verdict,
    scripts: list[_Script],
    grammar: str,
    *,
    anchors: list[str] | None = None,
    hints: bool = True,
) -> None:
    """Judge one command's words. ``anchors`` collects the words that name the cache, its
    parent or a directory above it; ``hints=False`` judges words a directory option or an
    anchor relocated, for where they lead only."""
    budget = _budget.get()
    for index, text in words:
        eligible = hints and index not in info.patterns
        source = index in info.sources
        blank = _BLANKS.search(text) is not None
        if verdict.reaches and verdict.strong and verdict.parent and not (source or blank):
            continue
        level = 0
        weak = sibling = named_parent = False
        if eligible:
            level, named_parent = _hint_level(text, text, scope)
            weak, sibling = level == _WEAK, level == _SIBLING
        lexical = budget.lexical if budget is not None else 0
        pieces: list[str] = []
        for candidate in _expand_word(text, scope):
            for piece in (candidate, *_list_elements(candidate)):
                pieces.append(piece)
                if not verdict.reaches and (
                    _candidate_reaches(piece, scope) or _names_a_cache_copy(piece, scope)
                ):
                    verdict.reaches = True
                if (
                    anchors is not None
                    and len(anchors) < _MAX_ANCHORS
                    and index not in info.patterns
                    and text not in anchors
                    and _anchors(piece, scope)
                ):
                    anchors.append(text)
                if source and not verdict.linked and _holds_the_cache(piece, scope):
                    verdict.linked = True
                settled = verdict.strong and verdict.parent and _SUBSTITUTION not in text
                if eligible and level < _STRONG and (blank or not settled):
                    found, parent = _hint_level(text, piece, scope)
                    level = max(level, found)
                    weak |= found == _WEAK
                    sibling |= found == _SIBLING
                    named_parent |= parent
        # A path compared by its spelling alone (past the resolution budget, or too deep to
        # resolve) is not followed through links and junctions (`C:\Documents and Settings`):
        # one that hints at the cache counts as reaching it.
        unresolved = budget is not None and budget.lexical > lexical
        if unresolved and eligible and not verdict.reaches:
            hinted = level or named_parent
            if hinted or any(any(_hint_level(text, piece, scope)) for piece in pieces):
                verdict.reaches = True
        if level >= _SIBLING and _SUBSTITUTION in text:
            # `~/.cache/uv$(cat f)/...`: a program's output completes a path this word
            # spells toward the cache, and it can hold `/../`.
            verdict.value = True
        verdict.parent |= named_parent
        verdict.strong |= level >= _STRONG
        verdict.sibling |= sibling
        verdict.weak |= weak
        if blank and (level or named_parent):
            # A hinted text with a blank may be a command line some program runs: `tmux new -d
            # '...'`, `hyperfine '...'`, a value another variable names, text piped to a shell.
            scripts.append((text, grammar, _MAYBE))


def _spliced_payloads(words: list[str]) -> list[str]:
    """Text this shell itself reads again: `eval`'s arguments, a `trap` action."""
    for index, raw in enumerate(words):
        program = _program(raw.lstrip("(")) if raw.lstrip("(") else ""
        if program == "eval":
            return [" ".join(words[index + 1 :])]
        if program == "trap" and index + 1 < len(words):
            return [words[index + 1]]
    return []


def _splice(lexed: _Lexed) -> list[_Lexed]:
    lexes = [lexed]
    position = 0
    while position < len(lexes):
        for words in lexes[position].simple_commands:
            for payload in _spliced_payloads(words):
                if len(lexes) > _MAX_SPLICES:
                    raise _BudgetExceededError
                inner = _lex_bash(payload)
                if inner is None:
                    raise _UnparsableError
                lexes.append(inner)
        position += 1
    return lexes


def _spliced_values(lexes: list[_Lexed], scope: _Scope) -> list[str]:
    """What `eval "$c"` and `trap "$c" EXIT` run when `c` holds a value the command assigned:
    that text, read as code again."""
    texts: list[str] = []
    for lex in lexes:
        for words in lex.simple_commands:
            for payload in _spliced_payloads(words):
                texts.extend(_code_variants(payload, scope))
    return texts


def _judge_bash(lexed: _Lexed, parent: _Scope, depth: int) -> _Verdict:
    if depth > _MAX_LEVELS:
        raise _BudgetExceededError
    _spend()
    lexes = _splice(lexed)
    scope = _build_scope([words for lex in lexes for words in lex.simple_commands], parent)
    scope.aliases += _aliases([words for lex in lexes for words in lex.simple_commands], scope)
    verdict = _Verdict(value=scope.mutated, near=_near(scope), reaches=_in_a_cache_copy(scope))
    actives = ["".join(lex.active) for lex in lexes]
    if any(_BASH_UNMODELLED.search(active) for active in actives):
        verdict.value = True
    verdict.special = (
        any(lex.groups for lex in lexes)
        or any(_SPECIAL_PARAMETER.search(active) for active in actives)
        or any(_BRACE_EXPANSION.search(_DOUBLE_QUOTED.sub("", active)) for active in actives)
    )
    # `while read f; do ...; done < list` or `mapfile -t a < list`: what an earlier part of
    # the command wrote to `list` (a search's output) is read back.
    verdict.file_fed = any(
        "<" in lex.tokens
        and any(
            _command_words(words)[:1] in (["read"], ["mapfile"], ["readarray"])
            for words in lex.simple_commands
        )
        for lex in lexes
    )
    # Arithmetic, `${x@P}`, `eval` and prompts evaluate the text of a value again, so a quoted
    # `$(...)` in a word can still run.
    reevaluates = any(lex.arithmetic for lex in lexes) or any("@P" in a for a in actives)
    scripts: list[_Script] = [(text, "bash", _RUNS) for text in _spliced_values(lexes, scope)]
    for lex in lexes:
        readers: dict[int, str] = {}
        for position, words in enumerate(lex.commands):
            if not words:
                continue
            verdict.commands += 1
            info = _inspect_command(words, fed=lex.fed[position])
            verdict.code |= info.code
            verdict.value |= info.value
            verdict.fed |= info.fed
            verdict.special |= info.array_read
            lists = _lists_or_prints(words)
            verdict.runs |= not lists
            verdict.piped_run |= lex.fed[position] and not lists and not _ignores_input(words)
            if info.searches and not verdict.searched:
                verdict.searched = _searches_near(_search_starts(info), scope)
            verdict.opaque |= info.opaque
            reevaluates |= info.reevaluates
            scripts.extend(info.scripts)
            if info.stdin is not None:
                readers[position] = info.stdin
            found = [*_path_words(info.words), *((-1, extra) for extra in info.extra)]
            named: list[str] = []
            _judge_words(found, info, scope, verdict, scripts, "bash", anchors=named)
            moved = _relocated(found, info, named, scope)
            _judge_words(moved, info, scope, verdict, scripts, "bash", hints=False)
            verdict.linked |= _mounts_the_cache(info, scope)
            if verdict.refuses_unmodelled():
                return verdict
        # A here-document a shell reads is a script; one piped into a shell may be.
        for heredoc in lex.heredocs:
            if heredoc.command in readers:
                scripts.append((heredoc.body, readers[heredoc.command], _MUST))
            elif readers:
                scripts.append((heredoc.body, next(iter(readers.values())), _RUNS))
    if reevaluates:
        scripts.extend(
            (word, "bash", _RUNS)
            for lex in lexes
            for words in lex.simple_commands
            for word in words
            if "$(" in word or "`" in word
        )
    near = _near(scope)
    seen: set[str] = set()
    for lex in lexes:
        for text, group in lex.groups:
            if text in seen:
                continue
            seen.add(text)
            inner = _judge_bash(group, scope, depth + 1)
            verdict.absorb(inner)
            verdict.grouped = True
            verdict.group_strong |= inner.strong or inner.sibling
            verdict.group_weak |= inner.weak
            verdict.near_group |= near
            if verdict.refuses_unmodelled():
                return verdict
    for text, grammar, reading in _run_scripts(scripts, verdict, scope, parent, "bash"):
        nested = _judge_script(text, grammar, reading, scope, depth + 1)
        if nested is not None:
            verdict.absorb(nested)
            if verdict.refuses_unmodelled():
                return verdict
    return verdict


def _run_scripts(
    scripts: list[_Script], verdict: _Verdict, scope: _Scope, parent: _Scope, grammar: str
) -> list[_Script]:
    """The scripts to judge, once each. In a command that runs text as code, the code can
    come from anywhere in it (`c='...'; echo "$c" | bash`): every value it assigns is read
    as a script that runs, and so is every text that only may be one."""
    if verdict.code:
        scripts = [(text, kind, max(reading, _RUNS)) for text, kind, reading in scripts]
        for name, held in scope.variables.items():
            if held and name != "_" and parent.variables.get(name) is not held:
                scripts.extend((value, grammar, _RUNS) for value in held)
    return list(dict.fromkeys(scripts))


def _judge_script(
    text: str, grammar: str, reading: int, scope: _Scope, depth: int
) -> _Verdict | None:
    """A script handed to another shell, judged as a command of its own, together with the
    scripts its variables hold (`bash -c "$c"` runs the text of `c`)."""
    verdict = _judge_one_script(text, grammar, reading, scope, depth)
    variants = _code_variants(text, scope, powershell=grammar != "bash")
    if grammar == "bash" and "\r" in text:
        variants.append(text.replace("\r", ""))  # as Git Bash reads it, see `guard_reason`
    for variant in variants:
        nested = _judge_one_script(variant, grammar, min(reading, _RUNS), scope, depth + 1)
        if nested is not None:
            if verdict is None:
                verdict = nested
            else:
                verdict.absorb(nested)
    return verdict


def _judge_one_script(
    text: str, grammar: str, reading: int, scope: _Scope, depth: int
) -> _Verdict | None:
    if grammar == "bash":
        lexed = _lex_bash(text)
        if lexed is None:
            if reading == _MUST:
                raise _UnparsableError
            return _unreadable_script(text, scope) if reading == _RUNS else None
        return _judge_bash(lexed, scope, depth)
    cmd = grammar == "cmd"
    if cmd:
        text = re.sub(r"[\"'^]", "", text)  # cmd keeps quotes for the program; `^` escapes
    try:
        return _judge_powershell(text, scope, depth, cmd=cmd)
    except _UnparsableError:
        if reading == _MUST:
            raise
        return _unreadable_script(text, scope) if reading == _RUNS else None


_SCRIPT_PIECES = re.compile(r"[\s'\"`;&|()<>{}]+")


def _unreadable_script(text: str, scope: _Scope) -> _Verdict | None:
    """Text a program may run as a script that no shell grammar here can read (an open quote, a
    trailing backslash, bash 5.3's `${ cmd; }`): judged by its hints alone. A way to the cache
    spelled in it refuses the command, the bare name beside the parent's name does too."""
    verdict = _Verdict()
    for piece in _SCRIPT_PIECES.split(text):
        if not piece:
            continue
        for candidate in _expand_word(piece, scope):
            level, parent = _hint_level(piece, candidate, scope)
            verdict.strong |= level >= _SIBLING
            verdict.weak |= level == _WEAK
            verdict.parent |= parent
    if not (verdict.strong or verdict.weak):
        return None
    verdict.code = True  # the script runs, and the guard cannot read what it does
    return verdict


# A parameter a script's text names: `$c`, `${c}`; for PowerShell also `$env:c`.
_SCRIPT_PARAMETER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_POWERSHELL_SCRIPT_PARAMETER = re.compile(
    r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}|\$(?:env:)?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)


def _code_variants(text: str, scope: _Scope, *, powershell: bool = False) -> list[str]:
    """The scripts ``text`` becomes when the variables it names hold what the command assigned
    them, as written: `c='python ~/.cache/${x/_/-}/...'; eval "$c"` runs that text, so its own
    expansions are read again as code. A variable the guard does not know is left as it is."""
    if "$" not in text:
        return []
    pattern = _POWERSHELL_SCRIPT_PARAMETER if powershell else _SCRIPT_PARAMETER
    variants = [""]
    position = 0
    replaced = False
    for match in pattern.finditer(text):
        _spend()
        name = match.group(1) or match.group(2)
        values = scope.variables.get(name)
        if powershell and not values:
            values = scope.variables.get(name.casefold())  # PowerShell names ignore case
        if not values:
            continue
        literal = text[position : match.start()]
        variants = [variant + literal + value for variant in variants for value in values]
        if len(variants) > _MAX_CANDIDATES:
            return []  # more texts than a decision reads: the code construct alone decides
        position = match.end()
        replaced = True
    if not replaced:
        return []
    return [variant + text[position:] for variant in variants]


def _spells_the_cache(text: str, scope: _Scope) -> bool:
    """Whether text spells the cache: its path, `.cache/hungry-crab` after a separator, or
    `$CRAB_CACHE_DIR`."""
    return bool(
        _ENV_CACHE_REF.search(text)
        or _DEFAULT_CACHE_REF.search(text)
        or names_root(text, scope.root)
    )


def _judge(command: str, *, root: Path, cwd: Path, shell: Shell) -> _Verdict:
    homes = _homes(cwd)
    scope = _Scope(
        root=root,
        cwd=cwd,
        home=homes[0],
        cwds=[cwd],
        powershell=shell == "powershell",
        homes=homes,
        hints=_hint_patterns(root, homes),
    )
    if shell == "powershell":
        verdict = _judge_powershell(command, scope, 0)
    else:
        lexed = _lex_bash(command)
        if lexed is None:
            raise _UnparsableError
        verdict = _judge_bash(lexed, scope, 0)
    # The cache spelled anywhere in the text: in a word, a here-document, a message.
    if _spells_the_cache(command, scope):
        verdict.reaches = True
    if verdict.spelled and _powershell_split_hint(verdict.spelled, scope):
        verdict.strong = True
    return verdict


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
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2015": "-"})
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
_POWERSHELL_ASSIGNMENT = re.compile(r"^\$(?:env:)?([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)$", re.DOTALL)
_CMD_REEXPANSION = re.compile(r"\bcall\b|![A-Za-z_][A-Za-z0-9_]*!", re.IGNORECASE)
_POWERSHELL_FOREACH = re.compile(r"^\s*\$([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.*)$", re.I | re.S)
# Anything that makes PowerShell evaluate rather than read: variables, subexpressions, arrays,
# script blocks and the escape character.
_POWERSHELL_EVALUATION = re.compile(r"[$@(){}`]")
_GLOB_CHARS = frozenset("*?[")
# PowerShell separates words on these as well as on space and tab.
_POWERSHELL_BLANKS = frozenset(" \t\v\f\u00a0\u0085")


def _powershell_blank(char: str) -> bool:
    return char in _POWERSHELL_BLANKS or (
        char > "\x7f" and unicodedata.category(char) in ("Zs", "Zl", "Zp")
    )


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


def _powershell_double_end(text: str, start: int) -> int:
    """Index of the `"` closing the double-quoted string at ``start``, or -1."""
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "`":
            index += 2
        elif char == '"':
            if text.startswith('"', index + 1):
                index += 2
                continue
            return index
        elif char == "$" and text.startswith("(", index + 1):
            index = _closing_paren(text, index + 1) + 1
        else:
            index += 1
    return -1


def _powershell_normalize(command: str) -> str | None:
    """The command with comments removed, here-strings as plain strings and every separator
    PowerShell reads between words as a space; ``None`` when a string or comment is left open.
    """
    _spend(len(command) // 8 + 1)
    out: list[str] = []
    index = 0
    length = len(command)
    start = True  # at the start of a token, where `#` opens a comment
    while index < length:
        char = command[index]
        if char in "\r\n":
            out.append(char)
            index += 1
            start = True
        elif _powershell_blank(char):
            out.append(" ")
            index += 1
            start = True
        elif start and char == "#":
            end = command.find("\n", index)
            index = length if end < 0 else end
        elif start and command.startswith("<#", index):
            end = command.find("#>", index + 2)
            if end < 0:
                return None
            out.append(" ")
            index = end + 2
        elif command.startswith(("@'", '@"'), index) and _here_string_opens(command, index + 2):
            quote = command[index + 1]
            body_start = command.find("\n", index) + 1
            closing = command.find("\n" + quote + "@", body_start - 1)
            if closing < 0:
                return None
            body = command[body_start:closing].rstrip("\r") if closing >= body_start else ""
            if quote == "'":
                out.append("'" + body.replace("'", "''") + "'")
            else:
                out.append('"' + body.replace('"', '`"') + '"')
            index = closing + 3
            start = False
        elif char == "'":
            end = _powershell_quote_end(command, index)
            if end < 0:
                return None
            out.append(command[index : end + 1])
            index = end + 1
            start = False
        elif char == '"':
            end = _powershell_double_end(command, index)
            if end < 0:
                return None
            out.append(command[index : end + 1])
            index = end + 1
            start = False
        elif char == "`":
            out.append(command[index : index + 2])
            index += 2
            start = False
        else:
            out.append(char)
            start = char in ";|&(){},="
            index += 1
    return "".join(out)


def _here_string_opens(command: str, position: int) -> bool:
    end = command.find("\n", position)
    return end >= 0 and command[position:end].strip(" \t\r") == ""


def _powershell_active(command: str) -> str:
    """The text PowerShell evaluates: single-quoted strings are literal."""
    out: list[str] = []
    index = 0
    while index < len(command):
        char = command[index]
        if char == "'":
            end = _powershell_quote_end(command, index)
            end = len(command) - 1 if end < 0 else end
            out.append("''")
            index = end + 1
        elif char == "`":
            out.append(command[index : index + 2])
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _powershell_groups(command: str) -> tuple[str, list[str], set[int]]:
    """Replace `$(...)`, `@(...)`, `(...)` and `{...}` with a placeholder; return their text,
    and which of them are script blocks (`{...}`) rather than values."""
    out: list[str] = []
    groups: list[str] = []
    blocks: set[int] = set()
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
            if char == "{":
                blocks.add(len(groups))
            groups.append(command[opening + 1 : end])
            out.append(_SUBSTITUTION)
            index = end + 1
        else:
            out.append(char)
            index += 1
    return "".join(out), groups, blocks


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
        if _powershell_blank(char) or char in "\r\n" or char in _OPERATOR_CHARS:
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
        if char == "`" and command[index + 1 : index + 2] in ("\n", "\r"):
            index += 2  # a line continuation
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


def _powershell_target(args: list[str]) -> str | None:
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
    return None


# An assignment's target: `$c`, `${c}`, `$global:c`, `[string]$c`, its operator attached or not.
_POWERSHELL_TARGET = re.compile(
    r"^(?:\[[^\]]*\])*\$(?:\{(?:(?:global|script|local|private|env):)?([A-Za-z_][A-Za-z0-9_]*)\}"
    r"|(?:(?:global|script|local|private|env):)?([A-Za-z_][A-Za-z0-9_]*))"
    r"(?:([-+*/%]?=)(.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_POWERSHELL_OPERATOR = re.compile(r"[-+*/%]?=")
_POWERSHELL_SETTERS = frozenset({"set-variable", "sv", "new-variable", "nv"})
_SET_VARIABLE_VALUES = ("-scope", "-option", "-description", "-visibility")


def _assign_powershell(scope: _Scope, words: list[str], index: int) -> None:
    """`$c = x`, `$c=x`, `$c ="x"`, `${c} += x`, `$global:c = x`, `[string]$c = x`."""
    target = _POWERSHELL_TARGET.match(words[index])
    if target is None:
        return
    name = (target.group(1) or target.group(2)).casefold()
    operator, value = target.group(3), target.group(4)
    if operator is None:
        following = words[index + 1 : index + 3]
        attached = _POWERSHELL_OPERATOR.match(following[0]) if following else None
        if attached is None:
            return
        operator = attached.group()
        value = following[0][attached.end() :] or (following[1] if len(following) > 1 else "")
    if operator in ("=", "+="):
        _assign(scope, name, value or "", append=operator == "+=")
    else:
        scope.variables[name] = None  # arithmetic on the value


def _set_variable(scope: _Scope, args: list[str]) -> None:
    """`Set-Variable c x`, `Set-Variable -Name c -Value x`, `New-Variable -n c -v x`."""
    name: str | None = None
    value: str | None = None
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-") or len(arg) < 2:
            positional.append(arg)
            index += 1
            continue
        key, colon, attached = arg.partition(":")
        key = key.casefold()
        taken = attached if colon else (args[index + 1] if index + 1 < len(args) else "")
        if "-name".startswith(key):
            name = taken
        elif "-value".startswith(key):
            value = taken
        elif not any(option.startswith(key) for option in _SET_VARIABLE_VALUES):
            index += 1  # a switch
            continue
        index += 1 if colon else 2
    if name is None and positional:
        name = positional.pop(0)
    if value is None and positional:
        value = positional.pop(0)
    if name:
        if value is None:
            scope.variables[name.casefold()] = None
        else:
            _assign(scope, name.casefold(), value)


# Common parameters that store a cmdlet's output, errors or messages in a variable.
_VARIABLE_PARAMETERS = (
    "-outvariable",
    "-pipelinevariable",
    "-errorvariable",
    "-warningvariable",
    "-informationvariable",
)
_VARIABLE_PARAMETER_ALIASES = frozenset({"-ov", "-pv", "-ev", "-wv", "-iv"})
_VARIABLE_DRIVE = re.compile(r"^(?:-[A-Za-z]+:)?variable:[\\/]*([A-Za-z_][A-Za-z0-9_]*)?", re.I)


def _store_unknown(scope: _Scope, words: list[str]) -> set[str]:
    """Variables a cmdlet fills with what the guard does not compute: `Set-Item Variable:c x`,
    `New-Item Variable:c`, `-OutVariable c` and the other common parameters, `Tee-Object
    -Variable c`. Their values are unknown; the names are returned (`*` for one the guard
    cannot read)."""
    stored: set[str] = set()
    tee = words[0].casefold() in ("tee-object", "tee")
    for index, word in enumerate(words):
        folded = word.casefold()
        drive = _VARIABLE_DRIVE.match(folded)
        if drive:
            stored.add(drive.group(1) or "*")
            continue
        if not folded.startswith("-") or len(folded) < 3:
            continue
        key, colon, attached = folded.partition(":")
        named = key in _VARIABLE_PARAMETER_ALIASES or (
            len(key) >= 4 and any(name.startswith(key) for name in _VARIABLE_PARAMETERS)
        )
        if not named and not (tee and "-variable".startswith(key)):
            continue
        target = attached if colon else (words[index + 1] if index + 1 < len(words) else "")
        name = target.lstrip("+").casefold()
        stored.add(name if _NAME.fullmatch(name) else "*")
    for name in stored - {"*"}:
        scope.variables[name] = None
    return stored


def _reads_back(stored: set[str], tokens: list[str], groups: list[str]) -> bool:
    """Whether the command reads a variable a cmdlet stored: `$c`, `${c}`, `$script:c`."""
    if "*" in stored:
        return True
    text = " ".join([*tokens, *groups])
    return any(
        re.search(r"\$\{?(?:[a-z]+:)?" + re.escape(name) + r"(?![A-Za-z0-9_])", text, re.I)
        for name in stored
    )


def _assign_cmd(scope: _Scope, args: list[str]) -> None:
    """cmd's `set name=value`; `set /p` and `set /a` give a value the guard does not know."""
    if args and args[0].startswith("/"):
        for arg in args[1:]:
            name = arg.split("=", 1)[0].strip()
            if name:
                scope.variables[name.casefold()] = None
        return
    name, equals, value = " ".join(args).partition("=")
    if equals and name.strip():
        _assign(scope, name.strip().casefold(), value)


def _build_powershell_scope(
    tokens: list[str], groups: list[str], parent: _Scope, *, cmd: bool = False
) -> _Scope:
    scope = parent.child(powershell=True, cmd=cmd)
    commands = _simple_commands(tokens)
    stored: set[str] = set()
    for words in commands:
        _spend(len(words))
        head = words[0].casefold()
        if cmd and head == "set":
            _assign_cmd(scope, words[1:])
            continue
        if head in _POWERSHELL_SETTERS:
            _set_variable(scope, words[1:])
        for index in range(len(words)):
            _assign_powershell(scope, words, index)
        stored |= _store_unknown(scope, words)
    if stored and _reads_back(stored, tokens, groups):
        scope.mutated = True
    for group in groups:
        loop = _POWERSHELL_FOREACH.match(group)
        if loop:
            for value in re.split(r"[\s,]+", loop.group(2).strip()):
                if value:
                    _assign(scope, loop.group(1).casefold(), value)
    # The location is the runspace's, so a `Set-Location` in a script block or a function body
    # moves the command too.
    nested: list[tuple[list[str], bool]] = []
    for group in groups:
        group_tokens = _powershell_tokens(group)
        if group_tokens is not None:
            nested.extend(_piped_commands(group_tokens))
    for words, piped in [*_piped_commands(tokens), *nested]:
        for index, word in enumerate(words):
            if word.casefold() in _POWERSHELL_CD_COMMANDS:
                target = _powershell_target(words[index + 1 :])
                if target is None and index == 0 and piped:
                    # `Get-ChildItem ~\.cache | Where-Object ... | Set-Location`: the
                    # location comes down the pipeline.
                    scope.unknown_cwd = True
                    continue
                if target is None and index == 0:
                    target = "~"
                if target is not None:
                    _change_directory(scope, [target])
    return scope


_POWERSHELL_COMMAND_VALUE = re.compile(
    r"\$[\w:]+\s*[-+*/]?=\s*(?![$'\"(@\d\[-])[A-Za-z_]"
    r"|\bforeach\s*\(\s*\$\w+\s+in\s+(?![$'\"(@\d\[-])[A-Za-z_]",
    re.IGNORECASE,
)
_POWERSHELL_ENUMERATION = re.compile(
    r"\b(?:Enumerate|Get)(?:Files|Directories|FileSystemEntries|FileSystemInfos)\s*\(",
    re.IGNORECASE,
)


def _powershell_path_words(tokens: list[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for index, token in enumerate(tokens):
        if _is_punctuation(token) or token in {"=", "+="}:
            continue
        if token.startswith("-") and "," in token and ":" not in token:
            value = token  # an array whose first element starts with `-`: `'-C','dir'`
        elif token.startswith("-"):
            if ":" not in token:
                continue
            value = token.split(":", 1)[1]  # `-Path:C:\x`
        else:
            assignment = _POWERSHELL_ASSIGNMENT.match(token)
            value = assignment.group(3) if assignment else token
        for piece in value.split(","):  # `a,b` is an array
            if piece:
                found.append((index, piece))
                found.extend((index, element) for element in _list_elements(piece))
    return found


def _judge_powershell(command: str, parent: _Scope, depth: int, *, cmd: bool = False) -> _Verdict:
    if depth > _MAX_LEVELS:
        raise _BudgetExceededError
    _spend()
    normalized = _powershell_normalize(command)
    if normalized is None:
        raise _UnparsableError
    text, groups, blocks = _powershell_groups(normalized)
    tokens = _powershell_tokens(text)
    if tokens is None:
        raise _UnparsableError
    if os.name != "nt":
        tokens = [token.replace("\\", "/") for token in tokens]  # pwsh reads both separators
    scope = _build_powershell_scope(tokens, groups, parent, cmd=cmd)
    scope.aliases += _aliases(_simple_commands(tokens), scope)
    verdict = _Verdict(value=scope.mutated, near=_near(scope), reaches=_in_a_cache_copy(scope))
    active = _powershell_active(normalized)
    if _POWERSHELL_UNMODELLED.search(active):
        verdict.computed = True
    # `$f = Get-ChildItem ...`, `foreach ($f in Get-Content l)`: a value a command printed.
    verdict.fed |= _POWERSHELL_COMMAND_VALUE.search(active) is not None
    # `[IO.Directory]::EnumerateFiles($HOME, '*', 'AllDirectories')` searches from code.
    verdict.searched |= _POWERSHELL_ENUMERATION.search(active) is not None
    if _POWERSHELL_TRANSFORMS.search(active):
        verdict.value = True
    if cmd and _CMD_REEXPANSION.search(normalized):
        verdict.value = True  # `call` and `!x!` expand the line again
    scripts: list[_Script] = []
    grammar = "cmd" if cmd else "powershell"
    for words, piped in _piped_commands(tokens):
        verdict.commands += 1
        info = _inspect_command(words, grammar=grammar)
        verdict.code |= info.code
        verdict.value |= info.value
        verdict.fed |= info.fed or any(
            re.search(r"\$(?:_|psitem)\b", word, re.IGNORECASE) for word in words
        )  # what the pipeline hands a script block
        lists = _lists_or_prints(words, powershell=True)
        verdict.runs |= not lists
        verdict.piped_run |= piped and not lists
        verdict.fed |= piped and not lists  # `... | Set-Location`, `... | Invoke-Item`
        if info.searches and not verdict.searched:
            verdict.searched = _searches_near(_search_starts(info), scope)
        verdict.opaque |= info.opaque
        scripts.extend(info.scripts)
        found = [*_powershell_path_words(info.words), *((-1, extra) for extra in info.extra)]
        grammar = "cmd" if cmd else "powershell"
        named: list[str] = []
        _judge_words(found, info, scope, verdict, scripts, grammar, anchors=named)
        moved = _relocated(found, info, named, scope)
        _judge_words(moved, info, scope, verdict, scripts, grammar, hints=False)
        verdict.linked |= _mounts_the_cache(info, scope)
        verdict.spelled.extend(word for at, word in enumerate(words) if at not in info.patterns)
        if verdict.refuses_unmodelled():
            return verdict
    for group, block in dict.fromkeys(
        (group, position in blocks) for position, group in enumerate(groups)
    ):
        inner = _judge_powershell(group, scope, depth + 1, cmd=cmd)
        verdict.absorb(inner)
        if not block:
            verdict.grouped = True
            # `(Get-Item ~\.cache\uv).Parent`: a value computed from the group's text. The
            # bare name in one (`($_ -match 'hungry-crab')`) counts as it does elsewhere: the
            # operators that compute text from it are seen in the whole command's text.
            verdict.group_strong |= inner.strong or inner.sibling
        if verdict.refuses_unmodelled():
            return verdict
    own = "cmd" if cmd else "powershell"
    for script, grammar, reading in _run_scripts(scripts, verdict, scope, parent, own):
        nested = _judge_script(script, grammar, reading, scope, depth + 1)
        if nested is not None:
            verdict.absorb(nested)
    return verdict


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


def _powershell_allowlist_reason(command: str) -> str | None:
    if _LINE_BREAK.search(command):
        return "cache-touching multi-line command is not established read-only (AGENTS.md rule 3)"
    normalized = _powershell_normalize(command)
    tokens = None if normalized is None else _powershell_tokens(normalized)
    if normalized is None or tokens is None:
        return "cache-touching command could not be parsed safely (AGENTS.md rule 3)"
    if not _powershell_read_only(normalized, tokens):
        return (
            "cache-touching PowerShell command is not a read cmdlet with literal arguments "
            "(AGENTS.md rule 3)"
        )
    return None


# --- deciding from the text alone -----------------------------------------------------------
#
# A command the guard cannot parse, or cannot finish judging within its budget, is decided from
# its text, and the text has to be plain to pass: no expansion, substitution, glob, tilde,
# brace or escape anywhere, no hint of the cache (its name, a glob or 8.3 alias of it or of a
# directory on the way to it from home), and not run from inside the cache. A guard that could
# be pushed into this path on purpose must not be weaker there than where it judges in full.

_TEXT_ONLY_REASON = (
    "command could not be parsed or judged in time, and its text could point at the prey "
    "cache (AGENTS.md rule 3)"
)
_TEXT_EXPANSION = re.compile(
    r"\$\{[^{}]*\}|\$\([^()]*\)|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|\{[^{}\s]*\}"
)
_TEXT_SPLIT = re.compile(r"[\s/\\;&|<>(),=:@]+")
_TEXT_QUOTES = re.compile(r"['\"`]")


def _text_glob(piece: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(piece):
        char = piece[index]
        if char == "*":
            out.append(_STAR)
        elif char == "?":
            out.append(_ONE)
        elif char == "[" and (close := piece.find("]", index + 2)) > 0:
            out.append(_ONE)
            index = close
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _text_points_near(command: str, *, root: Path, homes: list[Path]) -> bool:
    unquoted = _TEXT_QUOTES.sub("", command)
    name = root.name.casefold()
    for variant in (unquoted.replace("\\", "/"), unquoted.replace("\\", "")):
        folded = variant.casefold()
        if name and (name in folded or (name == "hungry-crab" and "hungry-" in folded)):
            return True
    names = {root.name}
    for home in homes:
        if home in root.parents:
            names.update(root.parts[len(home.parts) : -1])
    for piece in _TEXT_SPLIT.split(_TEXT_EXPANSION.sub("*", unquoted)):
        if not piece or len(piece) > 255:
            continue
        matcher = _component(_text_glob(piece))
        if matcher is not None and matcher.literal and any(matcher.matches(n) for n in names):
            return True
    return False


# Anything that lets the shell build a word from more than its letters: an expansion, a
# substitution, a glob, a tilde, a brace, an escape, a cmd variable.
_TEXT_BUILDS = re.compile(r"[$`*?\[{}~\\%!]")


def _text_only_reason(command: str, *, root: Path, cwd: Path) -> str | None:
    """A command the guard could not read or finish judging passes only when its text is plain:
    nothing the shell could build a path from, and no hint of the cache."""
    if _is_within(cwd, root) or _TEXT_BUILDS.search(command):
        return _TEXT_ONLY_REASON
    if _ENV_CACHE_REF.search(command) or _DEFAULT_CACHE_REF.search(command):
        return _TEXT_ONLY_REASON
    if names_root(command, root) or _text_points_near(command, root=root, homes=_homes(cwd)):
        return _TEXT_ONLY_REASON
    return None


# --- the audited read-only shapes -----------------------------------------------------------


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


_MAX_GIT_DIRECTORIES = 4
# Configuration keys whose value git runs, or which pull in configuration from elsewhere.
# `*` stands for one subsection or key name.
_RUNNING_KEYS = tuple(
    re.compile(pattern)
    for pattern in (
        r"core\.(?:fsmonitor|hookspath|pager|sshcommand|editor|askpass|gitproxy)",
        r"core\.alternaterefscommand",
        r"diff\.external",
        r"diff\..+\.(?:textconv|command)",
        r"merge\..+\.driver",
        r"filter\..+",
        r"alias\..+",
        r"include\..+",
        r"includeif\..+",
        r"credential\.(?:.+\.)?helper",
        r"gpg\.(?:.+\.)?program",
        r"sequence\.editor",
        r"pager\..+",
        r"uploadpack\.packobjectshook",
        r"remote\..+\.(?:uploadpack|receivepack|vcs)",
    )
)
_MAX_CONFIG_BYTES = 64 * 1024
_CONFIG_SECTION = re.compile(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]')


def _config_runs_a_program(git_dir: Path) -> bool:
    """Whether a clone's repository configuration names a program git may run on a read
    (`core.fsmonitor`, `core.pager`, a textconv driver, an alias, an include ...). Written by
    the main agent's file tools or an earlier command, it would turn an allowed
    `git -C <clone> status` into a way to run prey. The files are read, never run; one
    that cannot be read, or is too large to read whole, refuses, and so does a `commondir`
    that points git at a configuration elsewhere."""
    if (git_dir / "commondir").exists():
        return True
    for name in ("config", "config.worktree"):
        try:
            with (git_dir / name).open("rb") as handle:
                data = handle.read(_MAX_CONFIG_BYTES + 1)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if len(data) > _MAX_CONFIG_BYTES:
            return True
        if _config_names_a_runner(data.decode("utf-8", errors="replace")):
            return True
    return False


_CONFIG_BLANKS = " \t\f\v\r"
_CONFIG_KEY = re.compile(r"[A-Za-z][A-Za-z0-9-]*")


def _config_names_a_runner(text: str) -> bool:
    """Read git configuration text the way git does where it matters here: a byte-order mark
    is skipped, lines end at `\\n` alone (`\\r\\n` too), sections are `[name]`, `[name "sub"]`
    or the old `[name.sub]`, names ignore case, a key may stand alone (a true boolean), a
    value continues on the next line after an unescaped backslash outside a comment, and a
    line git would reject refuses."""
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").split("\n")
    section = ""
    index = 0
    while index < len(lines):
        line = lines[index].strip(_CONFIG_BLANKS)
        index += 1
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            header = _CONFIG_SECTION.match(line)
            if header is None:
                return True  # a header git may read differently
            name, sub = header.group(1).casefold(), header.group(2)
            section = f"{name}.{sub}" if sub is not None else name
            line = line[header.end() :].strip(_CONFIG_BLANKS)
            if not line or line[0] in "#;":
                continue  # `[core] pager = x` holds a key on the header's line
        key = _CONFIG_KEY.match(line)
        if key is None:
            return True  # a line git would reject
        full = f"{section}.{key.group().casefold()}".casefold()
        if any(pattern.fullmatch(full) for pattern in _RUNNING_KEYS):
            return True
        value, quoted = line[key.end() :], False
        while True:
            continues, quoted = _config_value_continues(value, quoted)
            if not continues or index >= len(lines):
                break
            value = lines[index]
            index += 1
    return False


def _config_value_continues(value: str, quoted: bool) -> tuple[bool, bool]:
    """Whether a value goes on past its line (a backslash last, outside a comment), and
    whether a quote is still open where the line ends."""
    at = 0
    while at < len(value):
        char = value[at]
        if char == "\\":
            if at + 1 == len(value):
                return True, quoted
            at += 2
            continue
        if char == '"':
            quoted = not quoted
        elif char in "#;" and not quoted:
            return False, quoted  # a comment: what follows, a backslash too, is no value
        at += 1
    return False, quoted


def _git_is_read_only(tokens: list[str], *, root: Path, cwd: Path) -> bool:
    # git opens the repository it discovers from its working directory: the event cwd, moved by
    # every `-C` in turn. That repository's config decides which programs `status`, `diff`,
    # `log -p` or `blame` start (`core.fsmonitor`, `diff.external`, textconv drivers), so it
    # has to be a clone root. `--git-dir`, `--work-tree`, `--namespace` and every other option
    # before the subcommand are refused rather than interpreted.
    directory = cwd
    index = 1
    moves = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-C":
            # Each `-C` is resolved through the filesystem after the budget ends: a read needs
            # one or two, and more are refused rather than resolved.
            moves += 1
            if moves > _MAX_GIT_DIRECTORIES:
                return False
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
    if _config_runs_a_program(directory / ".git"):
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


# Filters a read may be piped into: they read their input and print, and run nothing.
# None of their options writes a file or starts a program; `sort` and `uniq` have both
# (`sort --compress-program`, `sort -o`, `uniq IN OUT`), and GNU sort takes abbreviations.
_PIPE_FILTERS = frozenset({"head", "tail", "grep", "egrep", "fgrep", "wc", "cut"})


def _read_pipeline(tokens: list[str], *, root: Path, cwd: Path) -> bool:
    """`git -C <clone> log --oneline | head -20`: an allowed read piped into filters."""
    stages: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            stages.append([])
        elif _is_punctuation(token):
            return False
        else:
            stages[-1].append(token)
    if len(stages) < 2 or not all(stages) or not _simple_read_only(stages[0], root=root, cwd=cwd):
        return False
    for stage in stages[1:]:
        head = stage[0]
        if _ASSIGNMENT.match(head) or _ASSEMBLY.search(head) or "/" in head or "\\" in head:
            return False
        if _program(head) not in _PIPE_FILTERS:
            return False
    return True


def _bash_allowlist_reason(command: str, *, root: Path, cwd: Path) -> str | None:
    if _LINE_BREAK.search(command):
        # A line break separates commands exactly as `;` does: `cat <cache>/README.md` on one
        # line and `python <cache>/setup.py` on the next must not be judged as one long `cat`.
        return "cache-touching multi-line command is not established read-only (AGENTS.md rule 3)"
    if _EXECUTION_SUBSTITUTION.search(command):
        return "cache-touching shell substitution is not established read-only (AGENTS.md rule 3)"
    tokens = _shell_tokens(command)
    if tokens is None:
        return "cache-touching command could not be parsed safely (AGENTS.md rule 3)"
    if any(_is_punctuation(token) for token in tokens):
        if _read_pipeline(tokens, root=root, cwd=cwd):
            return None
        return (
            "cache-touching shell composition or redirection is not established read-only "
            "(AGENTS.md rule 3)"
        )
    if not _simple_read_only(tokens, root=root, cwd=cwd):
        return "cache-touching command is not in the audited read-only allowlist (AGENTS.md rule 3)"
    return None


def guard_reason(
    command: str,
    *,
    root: Path | None = None,
    cwd: Path | None = None,
    shell: Shell = "bash",
) -> str | None:
    """Return why a shell command must be denied, or ``None`` when this guard allows it.

    Commands that do not reach the cache are outside this guard's scope. Once one can, the
    burden flips: the complete command shape must be one of the small read-only forms above.
    This deliberately prefers a false refusal over silently executing prey. ``shell`` is the
    grammar the command is written in: Bash, or PowerShell.
    """
    here = canonical_path(cwd or Path.cwd(), cwd=Path.cwd())  # no network for a share
    cache = canonical_path(root or cache_root(), cwd=here)
    if not command.strip():
        return None
    if shell == "powershell":
        command = command.translate(_POWERSHELL_TRANSLATION)
    deadline = time.perf_counter() + _DEADLINE
    readings = [command]
    if shell == "bash" and "\r" in command:
        # Git Bash, the Bash tool's shell on Windows, discards carriage returns before it reads
        # a command; bash elsewhere keeps them as word characters (`EOF\r` is no delimiter for
        # `<<EOF`, `hungr\ry-crab` no name of the cache). A command passes only when both
        # readings pass, within one deadline.
        readings.append(command.replace("\r", ""))
    for reading in readings:
        reason = _decide(reading, root=cache, cwd=here, shell=shell, deadline=deadline)
        if reason is not None:
            return reason
    return None


def _decide(command: str, *, root: Path, cwd: Path, shell: Shell, deadline: float) -> str | None:
    cache, here = root, cwd
    verdict = _within_budget(
        lambda: _judge(command, root=cache, cwd=here, shell=shell), deadline=deadline
    )
    if verdict is None:
        return _text_only_reason(command, root=cache, cwd=here)
    if verdict.opaque:
        return "command hands a shell an encoded script the guard cannot read (AGENTS.md rule 3)"
    if verdict.refuses_unmodelled():
        return _UNMODELLED_REASON
    if verdict.runs_what_a_search_finds():
        # `find ~ -name setup.py | xargs python`: no word names the cache, but the search
        # walks through it and the command runs what it finds.
        return (
            "command runs what a search of the prey cache or a directory above it finds "
            "(AGENTS.md rule 3)"
        )
    if verdict.linked:
        # `cp -r ~/.cache x`, then `python x/hungry-crab/...` in the next call: the copy is
        # nowhere the guard looks, so it is not made. A link is followed where it is used, but
        # one made by the command that uses it is not there yet when the guard looks.
        return (
            "command links, copies, archives or mounts the prey cache or the directory that "
            "holds it (AGENTS.md rule 3)"
        )
    if not verdict.reaches:
        return None
    if shell == "powershell":
        return _powershell_allowlist_reason(command)
    return _bash_allowlist_reason(command, root=cache, cwd=here)


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
    """Apply the decision to one PreToolUse event without trusting its shape: a shell
    command, or a file a tool writes."""
    if not isinstance(event, dict):
        return None
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    if isinstance(tool_name, str) and tool_name in _FILE_TOOLS:
        key = _FILE_TOOLS[tool_name]
        target = tool_input.get(key) if isinstance(tool_input, dict) else None
        if not isinstance(target, str):
            return f"{tool_name} tool call denied: the {key} could not be inspected."
        reason = file_guard_reason(target, root=root, cwd=_event_cwd(event))
        return None if reason is None else f"{tool_name} tool call denied: {reason}"
    known = tool_name is None or tool_name in _SHELL_TOOLS
    tool = tool_name if isinstance(tool_name, str) else "Bash"
    if not isinstance(tool_input, dict):
        return f"{tool} tool call denied: the tool input could not be inspected." if known else None
    command = tool_input.get("command")
    if not isinstance(command, str):
        if known:
            return f"{tool} tool call denied: the shell command could not be inspected."
        return None
    cwd = _event_cwd(event)
    workdir = tool_input.get("cwd")
    if isinstance(workdir, str) and workdir.strip():
        try:
            cwd = canonical_path(Path(workdir), cwd=cwd)
        except (OSError, RuntimeError, ValueError):
            return f"{tool} tool call denied: the working directory could not be resolved."
    shells: list[Shell] = ["powershell"] if tool_name == "PowerShell" else ["bash"]
    if tool_name in _TERMINAL_TOOLS:
        shells = ["bash", "powershell"]  # a login shell or PowerShell, whichever runs it
    for shell in shells:
        reason = guard_reason(command, root=root, cwd=cwd, shell=shell)
        if reason is not None:
            return reason
    return None


def file_guard_reason(
    target: str, *, root: Path | None = None, cwd: Path | None = None
) -> str | None:
    """Why a tool may not write the file ``target`` names, or ``None``: one in the prey cache,
    by any spelling the shell paths are compared in (`~`, Git Bash's `/c/`, 8.3 aliases,
    device prefixes, links and junctions)."""
    here = canonical_path(cwd or Path.cwd(), cwd=Path.cwd())
    cache = canonical_path(root or cache_root(), cwd=here)
    for spelling in dict.fromkeys([target, *_msys_spellings(target)]):
        try:
            path = canonical_path(Path(spelling), cwd=here)
        except (OSError, RuntimeError, ValueError):
            return "the file path could not be resolved (AGENTS.md rule 3)"
        if _is_within(path, cache):
            return "the file is in the prey cache (AGENTS.md rule 3)"
        if not is_drive_path(path) and os.name == "nt" and names_root(spelling, cache):
            return "the file is in the prey cache (AGENTS.md rule 3)"
    return None


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
