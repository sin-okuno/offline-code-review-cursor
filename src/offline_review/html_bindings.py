"""Lexical Angular template bindings.

Comments are skipped before recognition. A binding is a simple call or a single
identifier. Anything else is recorded without a name so the graph can keep it
unresolved. This is not a template type checker.
"""

from __future__ import annotations

from dataclasses import dataclass

_SKIP = frozenset(
    {"as", "async", "false", "for", "if", "let", "null", "of", "track", "true", "undefined"}
)


@dataclass(frozen=True, slots=True)
class HtmlBinding:
    kind: str
    name: str
    line: int
    evidence: str
    simple: bool


def scan_bindings(text: str) -> tuple[HtmlBinding, ...]:
    """Return bindings in source order. Comment text is not scanned."""

    found: list[HtmlBinding] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            index = length if end < 0 else end + 3
            continue
        if text.startswith("@if", index) and _boundary(text, index + 3):
            expr, index = _group(text, index + 3, "(", ")")
            name = _single_ident(expr)
            found.append(_binding("if_block", name, text, index, expr, name != ""))
            continue
        if text.startswith("@for", index) and _boundary(text, index + 4):
            expr, index = _group(text, index + 4, "(", ")")
            name = _of_target(expr)
            found.append(_binding("for_block", name, text, index, expr, name != ""))
            continue
        if text.startswith("{{", index):
            end = text.find("}}", index + 2)
            expr = text[index + 2 : length if end < 0 else end]
            name, simple = _expression_name(expr)
            kind = "pipe" if "| async" in expr else "interpolation"
            found.append(_binding(kind, name, text, index, expr, simple))
            index = length if end < 0 else end + 2
            continue
        if text.startswith("[(", index):
            name, expr, index = _attribute(text, index + 2, ")]")
            target, simple = _expression_name(expr)
            found.append(_binding("two_way", target or name, text, index, expr or name, simple))
            continue
        if text.startswith("[", index):
            name, expr, index = _attribute(text, index + 1, "]")
            target, simple = _expression_name(expr)
            found.append(_binding("property", target, text, index, expr or name, simple))
            continue
        if text.startswith("(", index):
            name, expr, index = _attribute(text, index + 1, ")")
            target, simple = _expression_name(expr)
            found.append(_binding("event", target, text, index, expr or name, simple and target != ""))
            continue
        if text.startswith("<", index) and index + 1 < length and _ident_start(text[index + 1]):
            tag, index = _tag(text, index + 1)
            if "-" in tag:
                found.append(_binding("selector", tag, text, index, tag, True))
            continue
        if text[index] in {"'", '"'}:
            index = _skip_quoted(text, index)
            continue
        index += 1
    return tuple(found)


def _binding(kind: str, name: str, text: str, cursor: int, evidence: str, simple: bool) -> HtmlBinding:
    line = text.count("\n", 0, min(cursor, len(text))) + 1
    snippet = " ".join(evidence.split())
    if len(snippet) > 180:
        snippet = snippet[:180]
    return HtmlBinding(kind, name, line, snippet, simple)


def _attribute(text: str, start: int, closer: str) -> tuple[str, str, int]:
    end = text.find(closer, start)
    if end < 0:
        return "", "", len(text)
    name = text[start:end].strip()
    cursor = end + len(closer)
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text) or text[cursor] != "=":
        return name, "", cursor
    cursor += 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text) or text[cursor] not in {"'", '"'}:
        return name, "", cursor
    quote = text[cursor]
    close = text.find(quote, cursor + 1)
    if close < 0:
        return name, text[cursor + 1 :], len(text)
    return name, text[cursor + 1 : close], close + 1


def _group(text: str, start: int, opener: str, closer: str) -> tuple[str, int]:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] != opener:
        return "", start
    end = text.find(closer, start + 1)
    if end < 0:
        return text[start + 1 :], len(text)
    return text[start + 1 : end], end + 1


def _expression_name(expression: str) -> tuple[str, bool]:
    stripped = expression.strip()
    if stripped.endswith("| async"):
        stripped = stripped[: -len("| async")].strip()
    if not stripped:
        return "", False
    call = _call_name(stripped)
    if call:
        return call, True
    ident = _single_ident(stripped)
    if ident:
        return ident, True
    return "", False


def _call_name(expression: str) -> str:
    token, rest = _leading_ident(expression.strip())
    if not token or token in _SKIP:
        return ""
    rest = rest.lstrip()
    if not rest.startswith("("):
        return ""
    depth = 0
    for index, char in enumerate(rest):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return token if not rest[index + 1 :].strip() else ""
    return ""


def _leading_ident(expression: str) -> tuple[str, str]:
    name: list[str] = []
    for index, char in enumerate(expression):
        if _ident_start(char) or (name and (char.isdigit() or char == "$" or char == "_")):
            name.append(char)
            continue
        return "".join(name), expression[index:]
    return "".join(name), ""


def _single_ident(expression: str) -> str:
    token = expression.strip()
    if not token or token in _SKIP:
        return ""
    if token[0].isdigit() or not all(_ident_start(char) or char.isdigit() for char in token):
        return ""
    if not _ident_start(token[0]):
        return ""
    return token


def _of_target(expression: str) -> str:
    parts = expression.split()
    for index, part in enumerate(parts):
        if part == "of" and index + 1 < len(parts):
            return _single_ident(parts[index + 1].rstrip(";"))
    return ""


def _tag(text: str, start: int) -> tuple[str, int]:
    cursor = start
    while cursor < len(text) and (text[cursor].isalnum() or text[cursor] in {"-", "_"}):
        cursor += 1
    return text[start:cursor], cursor


def _skip_quoted(text: str, start: int) -> int:
    quote = text[start]
    cursor = start + 1
    while cursor < len(text) and text[cursor] != quote:
        cursor += 1
    return cursor + 1 if cursor < len(text) else len(text)


def _boundary(text: str, index: int) -> bool:
    return index >= len(text) or not (text[index].isalnum() or text[index] == "_")


def _ident_start(char: str) -> bool:
    return char == "_" or char == "$" or char.isalpha()
