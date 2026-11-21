"""Parse Python source from Git blobs into symbols and import statements.

The parser reads bytes the caller already loaded from a commit. It does not
open the work tree and it does not execute the module.
"""

from __future__ import annotations

import ast
import codecs
import re
from dataclasses import dataclass

from offline_review.models import AffectedSymbol
from offline_review.redaction import redact_secrets

MAX_PYTHON_SOURCE_BYTES = 1_048_576
PARSER_VERSION = "1"
_CODING_COOKIE = re.compile(rb"coding[:=]\s*([-\w.]+)")
_SYMBOL_KINDS = frozenset({"async_function", "class", "function"})


@dataclass(frozen=True, slots=True)
class Symbol:
    qualname: str
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class RawImport:
    line: int
    evidence: str
    form: str
    level: int
    module: str | None
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParseResult:
    status: str
    error: str | None
    line_count: int
    symbols: tuple[Symbol, ...]
    imports: tuple[RawImport, ...]
    dynamic_nonliteral: bool


def is_python_path(path: str) -> bool:
    return path.lower().endswith(".py")


def parse_python_source(data: bytes) -> ParseResult:
    """Parse one blob. The same bytes always produce the same result."""

    if b"\0" in data:
        return _failed("binary_source", "Python path contains NUL bytes.", 0)
    if len(data) > MAX_PYTHON_SOURCE_BYTES:
        return _failed(
            "too_large",
            f"Python source exceeds {MAX_PYTHON_SOURCE_BYTES} bytes and was not parsed.",
            0,
        )
    text = _decode(data)
    if text is None:
        return _failed(
            "decode_error",
            "Python source is not valid UTF-8 and has no usable coding cookie.",
            0,
        )
    line_count = _line_count(text)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return _failed("syntax_error", _syntax_message(exc), line_count)
    symbols = _symbols(tree)
    imports, dynamic_nonliteral = _imports(tree, text)
    return ParseResult(
        status="parsed",
        error=None,
        line_count=line_count,
        symbols=symbols,
        imports=imports,
        dynamic_nonliteral=dynamic_nonliteral,
    )


def map_affected_symbols(
    symbols: tuple[Symbol, ...], lines: tuple[int, ...], *, line_count: int
) -> tuple[AffectedSymbol, ...]:
    """Map changed lines to the narrowest symbol and its enclosing definitions.

    A line outside every statement becomes the module symbol. Deletion-only
    input has old lines and an empty new line tuple, so only the old side
    receives symbols.
    """

    if not lines:
        return ()
    selected: dict[tuple[str, str, int, int], set[int]] = {}
    for line in lines:
        containing = [
            symbol for symbol in symbols if symbol.start_line <= line <= symbol.end_line
        ]
        if not containing:
            key = ("<module>", "module", 1, max(line_count, line, 1))
            selected.setdefault(key, set()).add(line)
            continue
        narrowest = min(
            containing,
            key=lambda symbol: (
                symbol.end_line - symbol.start_line,
                -symbol.start_line,
                symbol.qualname,
            ),
        )
        for symbol in _chain(symbols, narrowest):
            key = (symbol.qualname, symbol.kind, symbol.start_line, symbol.end_line)
            selected.setdefault(key, set()).add(line)
    affected = [
        AffectedSymbol(
            qualname=qualname,
            kind=kind,
            start_line=start,
            end_line=end,
            changed_lines=tuple(sorted(changed)),
        )
        for (qualname, kind, start, end), changed in selected.items()
    ]
    affected.sort(key=lambda symbol: (symbol.start_line, symbol.end_line, symbol.qualname, symbol.kind))
    return tuple(affected)


def _chain(symbols: tuple[Symbol, ...], narrowest: Symbol) -> tuple[Symbol, ...]:
    chosen = [narrowest]
    for symbol in symbols:
        if symbol == narrowest or symbol.kind not in _SYMBOL_KINDS:
            continue
        contains = (
            symbol.start_line <= narrowest.start_line and symbol.end_line >= narrowest.end_line
        )
        if contains and (symbol.start_line, symbol.end_line) != (narrowest.start_line, narrowest.end_line):
            chosen.append(symbol)
    return tuple(chosen)


def _symbols(tree: ast.AST) -> tuple[Symbol, ...]:
    found: list[Symbol] = []

    def walk(body: list[ast.stmt], prefix: str, *, in_function: bool) -> None:
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(statement, ast.AsyncFunctionDef) else "function"
                qualname = _join(prefix, statement.name)
                found.append(_symbol(statement, qualname, kind))
                walk(statement.body, qualname, in_function=True)
                continue
            if isinstance(statement, ast.ClassDef):
                qualname = _join(prefix, statement.name)
                found.append(_symbol(statement, qualname, "class"))
                walk(statement.body, qualname, in_function=False)
                continue
            if not in_function and hasattr(statement, "lineno"):
                found.append(_symbol(statement, _join(prefix, _statement_label(statement)), "statement"))
            for suite in _suites(statement):
                walk(suite, prefix, in_function=in_function)

    walk(getattr(tree, "body", []), "", in_function=False)
    return _disambiguate(found)


def _symbol(node: ast.AST, qualname: str, kind: str) -> Symbol:
    start = int(getattr(node, "lineno"))
    for decorator in getattr(node, "decorator_list", ()):
        decorator_line = getattr(decorator, "lineno", None)
        if isinstance(decorator_line, int):
            start = min(start, decorator_line)
    end = getattr(node, "end_lineno", None)
    return Symbol(
        qualname=qualname,
        kind=kind,
        start_line=start,
        end_line=int(end) if isinstance(end, int) else start,
    )


def _disambiguate(symbols: list[Symbol]) -> tuple[Symbol, ...]:
    counts: dict[str, int] = {}
    for symbol in symbols:
        counts[symbol.qualname] = counts.get(symbol.qualname, 0) + 1
    unique: list[Symbol] = []
    for symbol in symbols:
        qualname = symbol.qualname
        if counts[qualname] > 1:
            qualname = f"{qualname}@{symbol.start_line}"
        unique.append(
            Symbol(
                qualname=qualname,
                kind=symbol.kind,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
            )
        )
    return tuple(unique)


def _imports(tree: ast.AST, source: str) -> tuple[tuple[RawImport, ...], bool]:
    collector = _ImportCollector(source)
    collector.visit(tree)
    return tuple(collector.imports), collector.dynamic_nonliteral


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.imports: list[RawImport] = []
        self.dynamic_nonliteral = False

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(
            RawImport(
                line=node.lineno,
                evidence=_import_evidence(node),
                form="import",
                level=0,
                module=None,
                names=tuple(alias.name for alias in node.names),
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(
            RawImport(
                line=node.lineno,
                evidence=_from_evidence(node),
                form="from",
                level=node.level or 0,
                module=node.module,
                names=tuple(alias.name for alias in node.names),
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        if _is_import_call(node):
            literal = _literal_argument(node)
            if literal is None:
                self.dynamic_nonliteral = True
            else:
                self.imports.append(
                    RawImport(
                        line=node.lineno,
                        evidence=_dynamic_evidence(node, literal),
                        form="heuristic",
                        level=0,
                        module=literal,
                        names=(),
                    )
                )
        self.generic_visit(node)


def _is_import_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "__import__":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "import_module"


def _literal_argument(node: ast.Call) -> str | None:
    if not node.args:
        return None
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _import_evidence(node: ast.Import) -> str:
    return "import " + ", ".join(_alias(alias) for alias in node.names)


def _from_evidence(node: ast.ImportFrom) -> str:
    module = "." * (node.level or 0) + (node.module or "")
    names = ", ".join(_alias(alias) for alias in node.names)
    return f"from {module} import {names}"


def _dynamic_evidence(node: ast.Call, literal: str) -> str:
    if isinstance(node.func, ast.Name):
        return f'{node.func.id}("{literal}")'
    return f'import_module("{literal}")'


def _alias(alias: ast.alias) -> str:
    if alias.asname:
        return f"{alias.name} as {alias.asname}"
    return alias.name


def _statement_label(statement: ast.stmt) -> str:
    if isinstance(statement, ast.Assign):
        targets = ",".join(_target_name(target) for target in statement.targets)
        return f"assign:{targets}"
    if isinstance(statement, ast.AnnAssign):
        return f"assign:{_target_name(statement.target)}"
    if isinstance(statement, ast.AugAssign):
        return f"assign:{_target_name(statement.target)}"
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return "import"
    return f"statement:{type(statement).__name__}"


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_target_name(node.value)}.{node.attr}"
    if isinstance(node, (ast.Tuple, ast.List)):
        return "(" + ",".join(_target_name(element) for element in node.elts) + ")"
    if isinstance(node, ast.Starred):
        return f"*{_target_name(node.value)}"
    return type(node).__name__


def _suites(statement: ast.stmt) -> list[list[ast.stmt]]:
    suites: list[list[ast.stmt]] = []
    for name in ("body", "orelse", "finalbody"):
        value = getattr(statement, name, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            suites.append(value)
    for handler in getattr(statement, "handlers", ()):
        body = getattr(handler, "body", None)
        if isinstance(body, list) and body:
            suites.append(body)
    for case in getattr(statement, "cases", ()):
        body = getattr(case, "body", None)
        if isinstance(body, list) and body:
            suites.append(body)
    return suites


def _join(prefix: str, name: str) -> str:
    if prefix:
        return f"{prefix}.{name}"
    return name


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        cookie = _coding_cookie(data)
        if cookie is None:
            return None
        try:
            decoder = codecs.lookup(cookie).name
        except LookupError:
            return None
        try:
            return data.decode(decoder)
        except UnicodeDecodeError:
            return None


def _coding_cookie(data: bytes) -> str | None:
    head = b"\n".join(data.splitlines()[:2])
    match = _CODING_COOKIE.search(head)
    if match is None:
        return None
    return match.group(1).decode("ascii", "strict")


def _line_count(text: str) -> int:
    if text == "":
        return 0
    count = text.count("\n")
    if text.endswith("\n"):
        return count
    return count + 1


def _syntax_message(exc: SyntaxError) -> str:
    location = f"line {exc.lineno}" if exc.lineno else "unknown line"
    excerpt = (exc.text or "").strip()
    if excerpt:
        message = f"Python syntax error at {location}: {exc.msg} ({excerpt[:120]})"
    else:
        message = f"Python syntax error at {location}: {exc.msg}"
    return redact_secrets(message)


def _failed(status: str, error: str, line_count: int) -> ParseResult:
    return ParseResult(
        status=status,
        error=error,
        line_count=line_count,
        symbols=(),
        imports=(),
        dynamic_nonliteral=False,
    )
