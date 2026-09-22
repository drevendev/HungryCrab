"""Fail-closed shell guard for commands that touch Hungry Crab's prey cache.

The guard does not try to parse all shell grammar. Once a command references the prey cache,
it allows only a small audited set of read-only command shapes. Unknown cache-touching shapes
are denied rather than assumed safe.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import TextIO

from .cache import cache_root

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
_GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset({"-C", "--git-dir", "--work-tree", "--namespace"})
_GIT_GLOBAL_SAFE_FLAGS = frozenset(
    {"--no-pager", "--literal-pathspecs", "--no-optional-locks", "--no-replace-objects"}
)
# `--output` writes what git prints to any path, which turns `log` into a way to land prey
# bytes where a later command runs them; `-O` hands `grep`'s matches to a program of the
# caller's choosing.
_GIT_DANGEROUS_ARGS = (
    "--ext-diff",
    "--textconv",
    "--exec-path",
    "--config-env",
    "--output",
    "--open-files-in-pager",
    "-O",
)
_SHELL_PUNCTUATION = frozenset(
    {";", "&", "&&", "|", "||", "<", ">", "<<", ">>", "<<<", "<>", ">&", "<&", "&>"}
)


def _canonical(path: Path, *, cwd: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _shell_tokens(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


def _token_mentions_root(token: str, *, root: Path, cwd: Path) -> bool:
    candidate = token
    if "=" in candidate and candidate.startswith("--"):
        candidate = candidate.split("=", 1)[1]
    if not candidate or candidate.startswith("-") or candidate in _SHELL_PUNCTUATION:
        return False
    try:
        return _is_within(_canonical(Path(candidate), cwd=cwd), root)
    except (OSError, RuntimeError, ValueError):
        return False


def _names_root(text: str, root: Path) -> bool:
    """Whether ``text`` spells the cache root, with a path boundary after it."""
    root_text = str(root).replace("\\", "/").casefold()
    if not root_text:
        return False
    normalized = text.replace("\\", "/").casefold()
    return re.search(re.escape(root_text) + _PATH_BOUNDARY, normalized) is not None


def _mentions_cache(command: str, *, root: Path, cwd: Path) -> bool:
    if _ENV_CACHE_REF.search(command) or _DEFAULT_CACHE_REF.search(command):
        return True
    if _names_root(command, root):
        return True

    tokens = _shell_tokens(command)
    return bool(tokens and any(_token_mentions_root(token, root=root, cwd=cwd) for token in tokens))


def _program(token: str) -> str:
    return Path(token).name.casefold().removesuffix(".exe")


def _program_token_points_into_cache(token: str, *, root: Path, cwd: Path) -> bool:
    """Return whether an explicit executable token can resolve inside the prey cache."""
    if _ENV_CACHE_REF.search(token) or _DEFAULT_CACHE_REF.search(token):
        return True
    if _names_root(token, root):
        return True

    # A bare command name such as ``cat`` is resolved by the trusted host PATH. Only explicit
    # path-shaped program tokens are resolved against cwd here; otherwise a cache cwd would make
    # every ordinary reader look like ``<cache>/cat`` even though the shell does not resolve it
    # that way.
    if "/" not in token and "\\" not in token:
        return False
    try:
        return _is_within(_canonical(Path(token), cwd=cwd), root)
    except (OSError, RuntimeError, ValueError):
        # This is already a cache-touching command. If an explicit executable path cannot be
        # classified, fail closed rather than applying the basename allowlist.
        return True


def _git_is_read_only(tokens: list[str]) -> bool:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                return False
            index += 2
            continue
        if token.startswith("-C") and token != "-C":
            index += 1
            continue
        if any(
            token.startswith(f"{option}=")
            for option in _GIT_GLOBAL_OPTIONS_WITH_VALUE
            if option != "-C"
        ):
            index += 1
            continue
        if token in _GIT_GLOBAL_SAFE_FLAGS:
            index += 1
            continue
        if token == "-c" or token.startswith(("--config-env", "--exec-path")):
            return False
        if token.startswith("-"):
            return False
        break

    if index >= len(tokens):
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
        return _git_is_read_only(tokens)
    if command not in _READ_ONLY_COMMANDS:
        return False
    return not (command == "rg" and any(arg == "--pre" or arg.startswith("--pre=") for arg in args))


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
    cache = _canonical(root or cache_root(), cwd=here)
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
    if any(token in _SHELL_PUNCTUATION for token in tokens):
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
        return _canonical(Path(raw), cwd=process_cwd)
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
