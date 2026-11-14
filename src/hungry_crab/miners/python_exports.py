"""Bounded declaration evidence for Python ``__all__``, without source execution.

Accept one unconditional module-level assignment of a bracketed literal list or
parenthesized tuple of strings, including adjacent literals. Nested def/class bindings and
attributes do not declare module exports. Other module-level uses, conditional
bindings and repeated declarations are unknown: omit names and issue a warning.
Class-body reads and definition-header expressions are conservatively unknown;
class-local reads are not resolved. Function bodies are not runtime-analyzed.
Absence retains the architecture miner's approximate definition fallback; an
explicit empty declaration suppresses it. This is not runtime export resolution.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import warnings
from collections.abc import Iterator

MAX_TOKENS = 50_000
MAX_NAMES = 1_000
MAX_LITERAL_CHARS = 40_000
MAX_SCOPES = 128

_STRING_PREFIX = re.compile(r"(?i)(?:r|u)?['\"]")
_UNKNOWN = "only one unconditional module-level literal list/tuple is supported"


def _literal_names(tokens: list[tokenize.TokenInfo]) -> list[str]:
    if len(tokens) < 2 or tokens[0].string not in {"[", "("}:
        raise ValueError(_UNKNOWN)
    closing = "]" if tokens[0].string == "[" else ")"
    if tokens[-1].string != closing:
        raise ValueError(_UNKNOWN)
    names: list[str] = []
    parts: list[str] = []
    comma = False
    for token in tokens[1:-1]:
        if token.type == tokenize.OP and token.string == ",":
            if not parts:
                raise ValueError(_UNKNOWN)
            names.append("".join(parts))
            if len(names) > MAX_NAMES:
                raise ValueError("literal element limit exceeded")
            parts = []
            comma = True
        elif token.type == tokenize.STRING and _STRING_PREFIX.match(token.string):
            if len(token.string) > MAX_LITERAL_CHARS:
                raise ValueError("string literal limit exceeded")
            # Only an individual STRING token reaches the literal decoder.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                warnings.simplefilter("ignore", SyntaxWarning)
                value = ast.literal_eval(token.string)
            if not isinstance(value, str):
                raise ValueError(_UNKNOWN)
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("decoded string is not valid UTF-8") from exc
            parts.append(value)
        else:
            raise ValueError(_UNKNOWN)
    if parts:
        names.append("".join(parts))
    if len(names) > MAX_NAMES:
        raise ValueError("literal element limit exceeded")
    if tokens[0].string == "(" and names and not comma:
        raise ValueError(_UNKNOWN)  # A parenthesized string is not a tuple.
    return names


def _statements(tokens: list[tokenize.TokenInfo]) -> Iterator[list[tokenize.TokenInfo]]:
    start, depth = 0, 0
    for index, token in enumerate(tokens):
        if token.type != tokenize.OP:
            continue
        if token.string in {"[", "(", "{"}:
            depth += 1
        elif token.string in {"]", ")", "}"}:
            depth -= 1
        elif token.string == ";" and depth == 0:
            yield tokens[start:index]
            start = index + 1
    yield tokens[start:]


def _definition(tokens: list[tokenize.TokenInfo]) -> tuple[str, int, int] | None:
    index = 1 if tokens and tokens[0].string == "async" else 0
    if len(tokens) > index + 1 and tokens[index].string in {"def", "class"}:
        depth = 0
        for end, token in enumerate(tokens):
            if token.type != tokenize.OP:
                continue
            if token.string in {"[", "(", "{"}:
                depth += 1
            elif token.string in {"]", ")", "}"}:
                depth -= 1
            elif token.string == ":" and depth == 0:
                kind = "function" if tokens[index].string == "def" else "class"
                return kind, index + 1, end
    return None


def _parameter_names(tokens: list[tokenize.TokenInfo]) -> set[int]:
    """Only parameter binding positions, excluding defaults/annotations/returns."""
    depth = 0
    opened = False
    parameter_start = False
    names: set[int] = set()
    for index, token in enumerate(tokens):
        if token.type == tokenize.OP:
            if token.string in {"[", "(", "{"}:
                if not opened and depth == 0 and token.string == "(":
                    opened = True
                    parameter_start = True
                depth += 1
            elif token.string in {"]", ")", "}"}:
                depth -= 1
                if opened and depth == 0:
                    break
            elif opened and depth == 1:
                if token.string == ",":
                    parameter_start = True
                elif token.string not in {"*", "**"} or not parameter_start:
                    parameter_start = False
        elif (
            opened
            and depth == 1
            and parameter_start
            and token.type == tokenize.NAME
            and token.string == "__all__"
            and index
            and tokens[index - 1].string in {"(", ",", "*", "**"}
            and index + 1 < len(tokens)
            and tokens[index + 1].string in {"=", ":", ",", ")"}
        ):
            names.add(index)
            parameter_start = False
        elif opened and depth == 1:
            parameter_start = False
    return names


def _references(
    tokens: list[tokenize.TokenInfo], visible: str, line_starts: list[int]
) -> list[int]:
    references: list[int] = []
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string != "__all__":
            continue
        start = line_starts[token.start[0] - 1] + token.start[1]
        if visible[start : start + 7] != "__all__":
            continue
        if index and tokens[index - 1].string == ".":
            continue
        references.append(index)
    return references


def _class_body_unknown(
    tokens: list[tokenize.TokenInfo], visible: str, line_starts: list[int]
) -> bool:
    for statement in _statements(tokens):
        references = _references(statement, visible, line_starts)
        if references and not (
            references == [0] and len(statement) > 1 and statement[1].string == "="
        ):
            return True
    return False


def python_exports(text: str, visible: str) -> tuple[list[str] | None, str | None]:
    """Return names/diagnostic; ``None`` names mean no module declaration.

    A diagnosed declaration returns empty names, never the definition fallback.
    ``visible`` is the original-offset lexical mask, excluding interpolation text.
    """
    if "__all__" not in visible:
        return None, None
    line_starts = [0, *(match.end() for match in re.finditer("\n", text))]
    scopes = ["module"]
    pending_scope: str | None = None
    line: list[tokenize.TokenInfo] = []
    found: list[str] | None = None
    try:
        for count, token in enumerate(tokenize.generate_tokens(io.StringIO(text).readline)):
            if count >= MAX_TOKENS:
                return [], "token limit exceeded"
            if token.type == tokenize.INDENT:
                if len(scopes) >= MAX_SCOPES:
                    return [], "scope limit exceeded"
                scopes.append(
                    "function" if scopes[-1] == "function" else pending_scope or scopes[-1]
                )
                pending_scope = None
            elif token.type == tokenize.DEDENT:
                scopes.pop()
                pending_scope = None
            elif token.type == tokenize.NEWLINE:
                definition = _definition(line)
                if scopes[-1] != "function":
                    if definition is not None:
                        kind, binding, colon = definition
                        if scopes[-1] == "module" and line[binding].string == "__all__":
                            return [], _UNKNOWN
                        ignored = _parameter_names(line[:colon]) if kind == "function" else set()
                        ignored.add(binding)
                        if any(
                            index not in ignored
                            for index in _references(line[:colon], visible, line_starts)
                        ):
                            return [], _UNKNOWN
                        if kind == "class" and _class_body_unknown(
                            line[colon + 1 :], visible, line_starts
                        ):
                            return [], _UNKNOWN
                    elif scopes[-1] == "class":
                        if _class_body_unknown(line, visible, line_starts):
                            return [], _UNKNOWN
                    else:
                        for statement in _statements(line):
                            for index in _references(statement, visible, line_starts):
                                if (
                                    index != 0
                                    or len(scopes) != 1
                                    or found is not None
                                    or len(statement) < 3
                                    or statement[1].string != "="
                                ):
                                    return [], _UNKNOWN
                                found = _literal_names(statement[2:])
                pending_scope = definition[0] if definition else None
                line = []
            elif token.type not in {tokenize.COMMENT, tokenize.NL, tokenize.ENDMARKER}:
                pending_scope = None
                line.append(token)
    except (ValueError, SyntaxError, tokenize.TokenError, RecursionError, MemoryError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, ValueError)
            and str(exc)
            in {
                _UNKNOWN,
                "literal element limit exceeded",
                "string literal limit exceeded",
                "decoded string is not valid UTF-8",
            }
            else "unsupported or malformed declaration"
        )
        return [], reason
    return found, None
