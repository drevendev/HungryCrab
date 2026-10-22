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

import json
import os
import re
import shlex
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .cache import cache_root
from .fs import is_reparse_point

_ENV_CACHE_REF = re.compile(
    r"(?:\$\{?CRAB_CACHE_DIR\}?|%CRAB_CACHE_DIR%|\$env:CRAB_CACHE_DIR)", re.IGNORECASE
)
# The cache root, and nothing that merely starts with its name: `hungry-crab-other` and
# `hungry-crab.old` are siblings, not the cache.
_PATH_BOUNDARY = r"(?![a-z0-9._-])"
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
# bytes where a later command runs them; `-O` hands `grep`'s matches to a program of the
# caller's choosing; `--filters` makes `cat-file` run the smudge filters a config names.
_GIT_DANGEROUS_ARGS = (
    "--ext-diff",
    "--textconv",
    "--filters",
    "--exec-path",
    "--config-env",
    "--output",
    "--open-files-in-pager",
    "-O",
)
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


def canonical_path(path: Path, *, cwd: Path) -> Path:
    """The one spelling both guards compare: absolute, resolved, device prefix stripped.

    On Windows ``resolve()`` asks for the final path name of the longest existing prefix, which
    expands 8.3 short names (``HUNGRY~1``) and follows junctions; the device prefix has to go
    first, or the result keeps it.
    """
    text = os.fspath(path)
    if os.name == "nt":
        text = strip_device_prefix(text)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _shell_tokens(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_OPERATOR_CHARS)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


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
_ASSEMBLY = re.compile(r"[$`*?\[{%\ue000-\ue005]|^~[^/\\]")


@dataclass
class _Scope:
    """What the guard knows about where one command's words can point."""

    root: Path
    cwd: Path
    home: Path
    near_parent: Path | None
    variables: dict[str, list[str] | None] = field(default_factory=dict)
    cwds: list[Path] = field(default_factory=list)
    unknown_cwd: bool = False


def _near_parent(root: Path, home: Path) -> Path | None:
    """The directory holding the cache, when naming it is a step towards the cache.

    ``~/.cache`` for the default root. Not the home directory or a drive root, which hold
    everything else as well, so ``cd ~`` stays an ordinary command.
    """
    parent = root.parent
    if parent in (root, home, Path(parent.anchor)):
        return None
    return parent


def _closing_paren(text: str, start: int) -> int:
    """Index of the `)` that closes the `(` at ``start``, skipping quotes; the end if none."""
    depth = 0
    quote = ""
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
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
        near_parent=_near_parent(root, home),
        variables=dict(variables),
        cwds=[cwd],
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
            try:
                path = canonical_path(Path(_msys_drive(candidate)), cwd=base)
            except (OSError, RuntimeError, ValueError):
                scope.unknown_cwd = True
                continue
            if path not in scope.cwds:
                scope.cwds.append(path)
    if len(scope.cwds) > _MAX_CANDIDATES:
        del scope.cwds[_MAX_CANDIDATES:]
        scope.unknown_cwd = True


def _environment(name: str, scope: _Scope) -> str | None:
    if name == "PWD":
        return str(scope.cwd)
    value = os.environ.get(name)
    if value is None and name.upper() in {"HOME", "USERPROFILE"}:
        return str(scope.home)
    return value


def _variable(name: str, scope: _Scope, inner: list[str], depth: int) -> list[str]:
    """The texts a variable can hold: assigned in the command, inherited, or unknown."""
    if name in scope.variables:
        raw_values = scope.variables[name]
        if raw_values is None or depth >= _MAX_DEPTH:
            return [_UNKNOWN]
        values: list[str] = []
        for raw in raw_values:
            values.extend(_expand_word(raw, scope, inner, depth=depth + 1))
        return values if len(values) <= _MAX_CANDIDATES else [_UNKNOWN]
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


def _tilde(prefix: str, scope: _Scope) -> str:
    if prefix == "~":
        return str(scope.home)
    if prefix == "~+":
        return str(scope.cwd)
    if prefix == "~-":
        return _UNKNOWN
    try:
        expanded = str(Path(prefix).expanduser())  # `~user`
    except RuntimeError:
        return _UNKNOWN
    return expanded if expanded != prefix else _UNKNOWN


def _expand_word(word: str, scope: _Scope, inner: list[str], *, depth: int = 0) -> list[str]:
    """Every text the shell can make of ``word``, with markers where it decides at run time."""
    if os.name == "nt":
        word = strip_device_prefix(word)  # its `?` is not a glob
    results = [""]
    index = 0
    if word.startswith("~"):
        separator = _SEPARATORS.search(word)
        index = separator.start() if separator else len(word)
        results = [_tilde(word[:index], scope)]
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
        elif char == _SUBSTITUTION or word.startswith("{}", index):
            pieces = [_UNKNOWN]
            step = 1 if char == _SUBSTITUTION else 2
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


def _component(text: str) -> tuple[re.Pattern[str] | None, bool]:
    """A matcher for one path component and whether it has a literal character to anchor on.

    ``None`` is a component that can span separators and so match whatever follows.
    """
    if _CROSS in text:
        return None, False
    pattern = "".join(
        ".*" if char in (_STAR, _ANY, _UNKNOWN) else "." if char == _ONE else re.escape(char)
        for char in text
    )
    if text[:1] in (_STAR, _ONE):
        pattern = r"(?!\.)" + pattern  # a glob does not match a leading dot
    literal = any(char not in _MARKERS for char in text)
    return re.compile(pattern, re.IGNORECASE | re.DOTALL), literal


def _walk(
    matchers: list[tuple[re.Pattern[str] | None, bool]],
    remaining: tuple[str, ...],
    *,
    allow_parent: bool,
) -> bool:
    """Whether components matched against the root's remaining parts can reach the root."""
    for position, (regex, _literal) in enumerate(matchers):
        if position >= len(remaining) or regex is None:
            return True
        if not regex.fullmatch(remaining[position]):
            return False
    return len(matchers) >= len(remaining) - (1 if allow_parent else 0)


def _unanchored(components: list[str], scope: _Scope) -> bool:
    """A word whose start is unknown: can a literal component line up with the root's path?"""
    segments: list[list[str]] = [[]]
    for component in components:
        if component == "..":
            segments.append([])
        elif component not in ("", "."):
            segments[-1].append(component)
    targets = scope.root.parts[1:]
    for segment in segments:
        matchers = [_component(component) for component in segment]
        for start, (regex, literal) in enumerate(matchers):
            if regex is None or not literal:
                continue
            for offset, part in enumerate(targets):
                remaining = targets[offset + 1 :]
                if regex.fullmatch(part) and _walk(
                    matchers[start + 1 :], remaining, allow_parent=False
                ):
                    return True
    return False


def _anchored(directory: Path, components: list[str], scope: _Scope) -> bool:
    """A word whose literal part ends at ``directory``: can the rest of it reach the root?"""
    rest = [component for component in components if component not in ("", ".")]
    if ".." in rest:
        last = len(rest) - 1 - rest[::-1].index("..")
        return _unanchored(rest[last + 1 :], scope)
    if _is_within(directory, scope.root):
        return True
    if not _is_within(scope.root, directory):
        return False
    remaining = scope.root.parts[len(directory.parts) :]
    matchers = [_component(component) for component in rest]
    return _walk(matchers, remaining, allow_parent=scope.near_parent is not None)


def _is_rooted(text: str) -> bool:
    return text[:1] in ("/", "\\") or Path(text).is_absolute()


def _literal_reaches(text: str, scope: _Scope) -> bool:
    if not text:
        return False
    for candidate in dict.fromkeys((text, _msys_drive(text))):
        rooted = _is_rooted(candidate)
        for base in [scope.cwd] if rooted else scope.cwds:
            try:
                path = canonical_path(Path(candidate), cwd=base)
            except (OSError, RuntimeError, ValueError):
                continue
            if _is_within(path, scope.root) or path == scope.near_parent:
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
        try:
            directory = canonical_path(Path(prefix or "."), cwd=base)
        except (OSError, RuntimeError, ValueError):
            continue
        if _anchored(directory, components[literal:], scope):
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


def glob_reaches(pattern: str, *, base: Path, root: Path) -> bool:
    """Whether a Glob ``pattern``, relative to ``base`` unless absolute, can match in ``root``.

    The literal part of the pattern is resolved like any path; the components after it are
    matched against the root's remaining components, so ``~/.cache/hungry-*/**`` and
    ``.cache/*crab/**`` from the home directory reach a default cache, ``src/**`` does not.
    """
    text = strip_device_prefix(pattern) if os.name == "nt" else pattern
    if text.startswith("~"):
        separator = _SEPARATORS.search(text)
        end = separator.start() if separator else len(text)
        text = str(Path(text[:end]).expanduser()) + text[end:]
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
    scope = _Scope(root=root, cwd=base, home=base, near_parent=None)
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
                    directory = canonical_path(Path(tokens[index + 1]), cwd=directory)
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
    return not any(
        arg == "-c" or any(arg.startswith(prefix) for prefix in _GIT_DANGEROUS_ARGS) for arg in args
    )


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
) -> str | None:
    """Return why a shell command must be denied, or ``None`` when this guard allows it.

    Commands that do not mention the cache are outside this guard's scope. Once the cache is
    mentioned, the burden flips: the complete command shape must be one of the small read-only
    forms above. This deliberately prefers a false refusal over silently executing prey.
    """
    here = (cwd or Path.cwd()).resolve(strict=False)
    cache = canonical_path(root or cache_root(), cwd=here)
    if not command.strip() or not _mentions_cache(command, root=cache, cwd=here):
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
    if tool_name is not None and tool_name != "Bash":
        return None

    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return "Bash tool call denied: the tool input could not be inspected."
    command = tool_input.get("command")
    if not isinstance(command, str):
        return "Bash tool call denied: the shell command could not be inspected."
    return guard_reason(command, root=root, cwd=_event_cwd(event))


def main(*, stdin: TextIO | None = None) -> int:
    """Read one PreToolUse event from stdin; exit 2 when the command is denied."""
    stream = stdin if stdin is not None else sys.stdin
    try:
        event = json.loads(stream.read())
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
