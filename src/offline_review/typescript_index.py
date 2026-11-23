"""Conservative lexical index of TypeScript and HTML Git blobs.

This is not a TypeScript parser. Comments, strings, and template literals are
masked before any construct is recognized. A file whose spans cannot be bounded
is ``unknown`` and contributes no symbols. Nested functions, JSX trees, and
non-literal decorators are not treated as confirmed structure.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from offline_review.typescript_cache import TypeScriptCache

from offline_review.git_adapter import GitAdapter, is_lfs_pointer
from offline_review.html_bindings import HtmlBinding, scan_bindings
from offline_review.redaction import redact_secrets

EXTRACTOR_VERSION = "5"
MAX_TS_SOURCE_BYTES = 1_048_576
_READ_CHUNK = 32
_TS_SUFFIXES = (".ts", ".tsx")
_HTML_SUFFIXES = (".html",)
_MODIFIERS = frozenset(
    {
        "abstract",
        "async",
        "override",
        "private",
        "protected",
        "public",
        "readonly",
        "static",
    }
)
_DECLARATION_STOP = frozenset(
    {
        "abstract",
        "class",
        "const",
        "enum",
        "export",
        "function",
        "import",
        "interface",
        "let",
        "type",
        "var",
    }
)


@dataclass(frozen=True, slots=True)
class TsWarning:
    code: str
    message: str
    path: str | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class TsSymbol:
    qualname: str
    kind: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True, slots=True)
class TsImport:
    line: int
    evidence: str
    form: str
    module: str | None
    specifiers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComponentMetadata:
    status: str
    selector: str | None = None
    template_url: str | None = None
    style_urls: tuple[str, ...] = ()
    standalone: bool | None = None
    evidence: str = ""
    inline_template: str | None = None


@dataclass(frozen=True, slots=True)
class NgModuleMetadata:
    status: str
    declarations: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    evidence: str = ""
    providers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TsReference:
    """An explicit call the scanner recognized. It is not a resolved edge."""

    kind: str
    name: str | None
    line: int
    evidence: str


@dataclass(frozen=True, slots=True)
class TsFileIndex:
    path: str
    status: str
    line_count: int
    imports: tuple[TsImport, ...]
    exports: tuple[str, ...]
    symbols: tuple[TsSymbol, ...]
    component: ComponentMetadata
    ng_module: NgModuleMetadata
    warnings: tuple[TsWarning, ...]
    references: tuple[TsReference, ...] = ()


@dataclass(frozen=True, slots=True)
class HtmlFileIndex:
    path: str
    status: str
    line_count: int
    warnings: tuple[TsWarning, ...]
    bindings: tuple[HtmlBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class TsCommitIndex:
    commit: str
    files: tuple[TsFileIndex, ...]
    html_files: tuple[HtmlFileIndex, ...]
    warnings: tuple[TsWarning, ...]


@dataclass(frozen=True, slots=True)
class RemovedTsSymbol:
    path: str
    base_commit: str
    qualname: str
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class _Tok:
    kind: str
    value: str
    start: int
    end: int
    line: int


class _Unsafe(Exception):
    def __init__(self, message: str, line: int | None) -> None:
        self.message = message
        self.line = line


def is_typescript_path(path: str) -> bool:
    return path.lower().endswith(_TS_SUFFIXES)


def is_html_path(path: str) -> bool:
    return path.lower().endswith(_HTML_SUFFIXES)


def scan_typescript(data: bytes) -> TsFileIndex:
    """Index one TypeScript blob. The same bytes always produce the same result."""

    if b"\0" in data:
        return _empty_ts("binary", 0, _warn("TS_BINARY", "TypeScript path contains NUL bytes."))
    if len(data) > MAX_TS_SOURCE_BYTES:
        return _empty_ts(
            "too_large",
            0,
            _warn(
                "TS_TOO_LARGE",
                f"TypeScript source exceeds {MAX_TS_SOURCE_BYTES} bytes and was not indexed.",
            ),
        )
    text = _decode(data)
    if text is None:
        return _empty_ts(
            "unknown",
            0,
            _warn("TS_DECODE", "TypeScript source is not valid UTF-8."),
        )
    line_count = _line_count(text)
    if _is_generated("", text):
        return _empty_ts(
            "skipped",
            line_count,
            _warn("TS_GENERATED", "Generated TypeScript was not indexed."),
        )
    if _has_bare_carriage_return(text):
        return _empty_ts(
            "unknown",
            line_count,
            _warn("TS_UNSUPPORTED_NEWLINE", "A carriage return is not a line break here."),
        )
    masked = _mask(text)
    if masked is None:
        return _empty_ts(
            "unknown",
            line_count,
            _warn(
                "TS_UNCLOSED",
                "A comment, string, template, regular expression, or brace span is unclosed.",
            ),
        )
    try:
        tokens = _tokenize(text, masked.chars, masked.strings)
        parsed = _parse_tokens(text, tokens)
    except _Unsafe as exc:
        return _empty_ts("unknown", line_count, _warn("TS_UNSAFE", exc.message, exc.line))
    return TsFileIndex(
        path="",
        status="parsed",
        line_count=line_count,
        imports=tuple(parsed.imports),
        exports=tuple(parsed.exports),
        symbols=tuple(parsed.symbols),
        component=parsed.component,
        ng_module=parsed.ng_module,
        warnings=tuple(parsed.warnings),
        references=_explicit_references(text, tokens, parsed.imports),
    )


def scan_html(data: bytes) -> HtmlFileIndex:
    """Record an HTML blob's size, line count, and lexical template bindings."""

    if b"\0" in data:
        return HtmlFileIndex(
            "",
            "binary",
            0,
            (_warn("TS_BINARY", "HTML path contains NUL bytes."),),
        )
    if len(data) > MAX_TS_SOURCE_BYTES:
        return HtmlFileIndex(
            "",
            "too_large",
            0,
            (
                _warn(
                    "TS_TOO_LARGE",
                    f"HTML source exceeds {MAX_TS_SOURCE_BYTES} bytes and was not indexed.",
                ),
            ),
        )
    text = _decode(data)
    if text is None:
        return HtmlFileIndex(
            "",
            "unknown",
            0,
            (_warn("TS_DECODE", "HTML source is not valid UTF-8."),),
        )
    return HtmlFileIndex("", "parsed", _line_count(text), (), scan_bindings(text))


def index_typescript_commit(
    adapter: GitAdapter, commit: str, cache: TypeScriptCache | None = None
) -> TsCommitIndex:
    """Index ``.ts``, ``.tsx``, and ``.html`` blobs in one commit object.

    ``commit`` must already be a full object id. The work tree is not read.
    ``cache`` reuses a commit record only when the commit id, blob-set digest,
    and extractor version match. Individual blob scans are reused by blob id.
    """

    entries = [
        (mode, sha, path)
        for mode, sha, path in adapter.list_tree(commit)
        if is_typescript_path(path) or is_html_path(path)
    ]
    if cache is not None:
        cached = cache.load_commit(commit, tuple(entries))
        if cached is not None:
            return cached
    warnings: list[TsWarning] = []
    readable: list[tuple[str, str, str]] = []
    for mode, sha, path in entries:
        if mode in {"120000", "160000"}:
            code = "TS_SYMLINK" if mode == "120000" else "TS_SUBMODULE"
            warnings.append(
                _warn(
                    code,
                    "The path is not a regular blob and was not indexed.",
                    path=path,
                )
            )
            continue
        if mode not in {"100644", "100755"}:
            warnings.append(
                _warn(
                    "TS_SKIPPED_MODE",
                    f"Git mode {mode} was not indexed.",
                    path=path,
                )
            )
            continue
        readable.append((mode, sha, path))
    metadata = _chunked_metadata(adapter, [sha for _mode, sha, _path in readable])
    files: list[TsFileIndex] = []
    html_files: list[HtmlFileIndex] = []
    pending: list[tuple[str, str]] = []

    def flush() -> None:
        if not pending:
            return
        bodies = _chunked_blobs(adapter, [sha for _path, sha in pending])
        for path, sha in pending:
            data = bodies.get(sha)
            if is_typescript_path(path):
                indexed = scan_typescript(b"" if data is None else data)
                if data is None:
                    indexed = _empty_ts(
                        "unknown",
                        0,
                        _warn("TS_MISSING_BLOB", "TypeScript blob could not be read.", path=path),
                    )
                elif is_lfs_pointer(data):
                    indexed = _empty_ts(
                        "skipped",
                        0,
                        _warn("TS_LFS_POINTER", "Git LFS pointer text was not indexed."),
                    )
                    if cache is not None:
                        cache.store_blob(sha, "ts", indexed)
                elif cache is not None:
                    cache.store_blob(sha, "ts", indexed)
                files.append(_bind_ts(indexed, path))
            else:
                indexed_html = scan_html(b"" if data is None else data)
                if data is None:
                    indexed_html = HtmlFileIndex(
                        path,
                        "unknown",
                        0,
                        (_warn("TS_MISSING_BLOB", "HTML blob could not be read.", path=path),),
                    )
                elif is_lfs_pointer(data):
                    indexed_html = HtmlFileIndex(
                        "",
                        "skipped",
                        0,
                        (_warn("TS_LFS_POINTER", "Git LFS pointer text was not indexed."),),
                    )
                    if cache is not None:
                        cache.store_blob(sha, "html", indexed_html)
                elif cache is not None:
                    cache.store_blob(sha, "html", indexed_html)
                html_files.append(_bind_html(indexed_html, path))
        pending.clear()

    for (_mode, sha, path), info in zip(readable, metadata, strict=True):
        if info is None or info[0] != "blob":
            if is_typescript_path(path):
                files.append(
                    _empty_ts(
                        "unknown",
                        0,
                        _warn("TS_MISSING_BLOB", "TypeScript blob could not be read.", path=path),
                    )
                )
            else:
                html_files.append(
                    HtmlFileIndex(
                        path,
                        "unknown",
                        0,
                        (_warn("TS_MISSING_BLOB", "HTML blob could not be read.", path=path),),
                    )
                )
            continue
        if info[1] > MAX_TS_SOURCE_BYTES or _is_generated(path, ""):
            if _is_generated(path, ""):
                item = _warn("TS_GENERATED", "Generated TypeScript was not indexed.", path=path)
                files.append(_empty_ts("skipped", 0, item))
                continue
            if is_typescript_path(path):
                files.append(
                    _empty_ts(
                        "too_large",
                        0,
                        _warn(
                            "TS_TOO_LARGE",
                            f"TypeScript source exceeds {MAX_TS_SOURCE_BYTES} bytes and was not indexed.",
                            path=path,
                        ),
                    )
                )
            else:
                html_files.append(
                    HtmlFileIndex(
                        path,
                        "too_large",
                        0,
                        (
                            _warn(
                                "TS_TOO_LARGE",
                                f"HTML source exceeds {MAX_TS_SOURCE_BYTES} bytes and was not indexed.",
                                path=path,
                            ),
                        ),
                    )
                )
            continue
        if cache is not None:
            cached_blob = cache.load_blob(sha)
            if cached_blob is not None:
                kind, indexed_blob = cached_blob
                if kind == "ts" and is_typescript_path(path) and isinstance(indexed_blob, TsFileIndex):
                    files.append(_bind_ts(indexed_blob, path))
                    continue
                if kind == "html" and is_html_path(path) and isinstance(indexed_blob, HtmlFileIndex):
                    html_files.append(_bind_html(indexed_blob, path))
                    continue
        pending.append((path, sha))
        if len(pending) >= _READ_CHUNK:
            flush()
    flush()
    files.sort(key=lambda item: item.path)
    html_files.sort(key=lambda item: item.path)
    warnings.sort(key=lambda item: (item.path or "", item.line or 0, item.code))
    indexed_commit = TsCommitIndex(commit, tuple(files), tuple(html_files), tuple(warnings))
    if cache is not None:
        cache.store_commit(commit, tuple(entries), indexed_commit)
    return indexed_commit


def removed_indexed_paths(base: TsCommitIndex, head: TsCommitIndex) -> tuple[str, ...]:
    """Return paths present in the base commit and absent in head."""

    head_paths = {item.path for item in head.files} | {item.path for item in head.html_files}
    base_paths = [item.path for item in base.files] + [item.path for item in base.html_files]
    return tuple(sorted(path for path in base_paths if path not in head_paths))


def removed_typescript_symbols(base: TsCommitIndex, head: TsCommitIndex) -> tuple[RemovedTsSymbol, ...]:
    """Return base symbols whose qualname and kind are absent at the same path on head.

    A symbol that still exists after a line shift is not removed. Duplicate
    qualnames in one file are not tracked individually. Symbols in a removed
    file are removed.
    """

    head_by_path = {item.path: item for item in head.files}
    removed: list[RemovedTsSymbol] = []
    for item in base.files:
        other = head_by_path.get(item.path)
        head_keys = set() if other is None else {(symbol.qualname, symbol.kind) for symbol in other.symbols}
        for symbol in item.symbols:
            if (symbol.qualname, symbol.kind) in head_keys:
                continue
            removed.append(
                RemovedTsSymbol(
                    item.path,
                    base.commit,
                    symbol.qualname,
                    symbol.kind,
                    symbol.start_line,
                    symbol.end_line,
                )
            )
    removed.sort(key=lambda item: (item.path, item.start_line, item.qualname, item.kind))
    return tuple(removed)


@dataclass
class _Masked:
    chars: str
    strings: dict[int, tuple[int, str]]


@dataclass
class _Parsed:
    imports: list[TsImport]
    exports: list[str]
    symbols: list[TsSymbol]
    component: ComponentMetadata
    ng_module: NgModuleMetadata
    warnings: list[TsWarning]


def _explicit_references(text: str, tokens: list[_Tok], imports: list[TsImport]) -> tuple[TsReference, ...]:
    """Record distinctive calls. ``on`` counts only when imported from an NgRx module."""

    ngrx_on = any(
        item.module is not None and "ngrx" in item.module and "on" in item.specifiers
        for item in imports
    )
    found: list[TsReference] = []
    for index, token in enumerate(tokens):
        if token.kind != "ident":
            continue
        kind = _CALL_KINDS.get(token.value)
        if token.value == "on" and ngrx_on:
            kind = "on_action"
        if kind is None and token.value in {"dispatch", "select", "selectSignal"} and index:
            previous = tokens[index - 1]
            if previous.kind == "punct" and previous.value == ".":
                kind = "dispatch_action" if token.value == "dispatch" else "select_selector"
        if kind is None:
            continue
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if nxt is None or nxt.kind != "punct" or nxt.value != "(":
            continue
        argument = None
        evidence_end = nxt.end
        if kind in {"of_type", "on_action", "inject", "dispatch_action", "select_selector"}:
            argument, evidence_end = _callee_name(tokens, index + 1, evidence_end)
        found.append(
            TsReference(
                kind,
                argument,
                token.line,
                _evidence(text, token.start, evidence_end),
            )
        )
    found.sort(key=lambda item: (item.line, item.kind, item.name or "", item.evidence))
    return tuple(found)


_NOT_CALLEE = frozenset({"dispatch", "new", "select", "selectSignal", "store", "this"})


def _callee_name(tokens: list[_Tok], open_index: int, fallback_end: int) -> tuple[str | None, int]:
    """Name the callee after ``(``, skipping one leading ``new``.

    ``new``, ``this``, ``store``, and ``dispatch`` are never the callee.
    ``ProductTreeActions.loadTree()`` keeps the namespace identifier.
    """

    cursor = open_index + 1
    if cursor < len(tokens) and tokens[cursor].kind == "ident" and tokens[cursor].value == "new":
        cursor += 1
    if cursor >= len(tokens) or tokens[cursor].kind != "ident":
        return None, fallback_end
    name = tokens[cursor].value
    if name in _NOT_CALLEE:
        return None, fallback_end
    return name, tokens[cursor].end


_CALL_KINDS = {
    "createAction": "create_action",
    "createEffect": "create_effect",
    "createReducer": "create_reducer",
    "createSelector": "create_selector",
    "inject": "inject",
    "ofType": "of_type",
}


def _parse_tokens(text: str, tokens: list[_Tok]) -> _Parsed:
    parsed = _Parsed(
        [],
        [],
        [],
        ComponentMetadata("absent"),
        NgModuleMetadata("absent"),
        [],
    )
    cursor = 0
    while cursor < len(tokens):
        nxt = _next_statement(text, tokens, cursor, parsed)
        if nxt <= cursor:
            raise _Unsafe("The TypeScript scan could not advance.", tokens[cursor].line)
        cursor = nxt
    parsed.symbols.sort(key=lambda item: (item.start_line, item.end_line, item.qualname, item.kind))
    parsed.exports = sorted(set(parsed.exports))
    return parsed


def _next_statement(text: str, tokens: list[_Tok], cursor: int, parsed: _Parsed) -> int:
    decorators, cursor = _leading_decorators(text, tokens, cursor, parsed)
    token = tokens[cursor]
    exported = False
    defaulted = False
    if token.kind == "ident" and token.value == "export":
        exported = True
        cursor += 1
        if cursor >= len(tokens):
            raise _Unsafe("An export is unfinished.", token.line)
        if tokens[cursor].kind == "punct" and tokens[cursor].value == "*":
            return _export_star(text, tokens, cursor - 1, parsed)
        if tokens[cursor].kind == "punct" and tokens[cursor].value == "{":
            return _export_braces(text, tokens, cursor - 1, parsed)
        if tokens[cursor].kind == "ident" and tokens[cursor].value == "type":
            following = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
            if following is not None and following.kind == "punct" and following.value == "{":
                return _export_braces(text, tokens, cursor - 1, parsed)
        if tokens[cursor].kind == "ident" and tokens[cursor].value == "default":
            defaulted = True
            parsed.exports.append("default")
            cursor += 1
        if cursor >= len(tokens):
            raise _Unsafe("An export is unfinished.", token.line)
        token = tokens[cursor]
    if token.kind == "ident" and token.value == "import":
        return _import_statement(text, tokens, cursor, parsed)
    if token.kind == "ident" and token.value == "async":
        nxt = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
        if nxt is not None and nxt.kind == "ident" and nxt.value == "function":
            return _function(text, tokens, cursor + 1, parsed, exported, defaulted, async_function=True)
    if token.kind == "ident" and token.value == "function":
        return _function(text, tokens, cursor, parsed, exported, defaulted, async_function=False)
    if token.kind == "ident" and token.value in {"abstract", "class"}:
        start = cursor
        if token.value == "abstract":
            nxt = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
            if nxt is None or nxt.kind != "ident" or nxt.value != "class":
                return _skip_loose(tokens, cursor)
            start = cursor + 1
        return _class(text, tokens, start, parsed, exported, defaulted, decorators)
    if token.kind == "ident" and token.value in {"const", "let", "var"} and not defaulted:
        return _bindings(text, tokens, cursor, parsed, exported)
    if decorators:
        parsed.warnings.append(
            _warn("TS_DECORATOR_UNKNOWN", "A decorator is not attached to a class.", token.line)
        )
    return _skip_loose(tokens, cursor)


def _import_statement(text: str, tokens: list[_Tok], cursor: int, parsed: _Parsed) -> int:
    end = _statement_end(tokens, cursor)
    if end is None:
        parsed.warnings.append(
            _warn("TS_IMPORT_UNKNOWN", "An import has no semicolon and was not recorded.", tokens[cursor].line)
        )
        return _skip_loose(tokens, cursor)
    clause = tokens[cursor : end + 1]
    recorded = _read_import(text, clause)
    if recorded is None:
        parsed.warnings.append(
            _warn("TS_IMPORT_UNKNOWN", "An import was not a supported literal form.", tokens[cursor].line)
        )
    else:
        parsed.imports.append(recorded)
    return end + 1


def _read_import(text: str, clause: list[_Tok]) -> TsImport | None:
    if not clause or clause[0].kind != "ident" or clause[0].value != "import":
        return None
    if clause[-1].kind != "punct" or clause[-1].value != ";":
        return None
    body = clause[1:-1]
    evidence = _evidence(text, clause[0].start, clause[-1].end)
    line = clause[0].line
    if len(body) == 1 and body[0].kind == "string":
        return TsImport(line, evidence, "side_effect", _module(body[0].value), ())
    offset = 0
    form = "named"
    if body and body[0].kind == "ident" and body[0].value == "type":
        form = "type"
        offset = 1
    if offset >= len(body):
        return None
    if body[offset].kind == "punct" and body[offset].value == "*":
        return _namespace_import(body[offset:], line, evidence, form)
    if body[offset].kind == "punct" and body[offset].value == "{":
        return _named_import(body[offset:], line, evidence, form)
    if body[offset].kind != "ident":
        return None
    if offset + 1 < len(body) and body[offset + 1].kind == "punct" and body[offset + 1].value == ",":
        rest = body[offset + 2 :]
        if rest and rest[0].kind == "punct" and rest[0].value == "*":
            namespace = _namespace_import(rest, line, evidence, form)
            if namespace is None:
                return None
            return replace(namespace, specifiers=(body[offset].value, *namespace.specifiers))
        named = _named_import(rest, line, evidence, form)
        if named is None:
            return None
        return replace(named, specifiers=(body[offset].value, *named.specifiers))
    if offset + 1 < len(body) and body[offset + 1].kind == "ident" and body[offset + 1].value == "from":
        if len(body) != offset + 3 or body[offset + 2].kind != "string":
            return None
        return TsImport(line, evidence, "default", _module(body[offset + 2].value), (body[offset].value,))
    return None


def _namespace_import(body: list[_Tok], line: int, evidence: str, form: str) -> TsImport | None:
    if (
        len(body) == 5
        and body[0].value == "*"
        and body[1].kind == "ident"
        and body[1].value == "as"
        and body[2].kind == "ident"
        and body[3].kind == "ident"
        and body[3].value == "from"
        and body[4].kind == "string"
    ):
        return TsImport(line, evidence, form, _module(body[4].value), (body[2].value,))
    return None


def _named_import(body: list[_Tok], line: int, evidence: str, form: str) -> TsImport | None:
    close = _match(body, 0, "{", "}")
    if close is None:
        return None
    specifiers = _specifiers(body[1:close])
    if specifiers is None:
        return None
    rest = body[close + 1 :]
    if len(rest) != 2 or rest[0].kind != "ident" or rest[0].value != "from" or rest[1].kind != "string":
        return None
    return TsImport(line, evidence, form, _module(rest[1].value), specifiers)


def _export_star(text: str, tokens: list[_Tok], cursor: int, parsed: _Parsed) -> int:
    end = _statement_end(tokens, cursor)
    if end is None:
        parsed.warnings.append(_warn("TS_IMPORT_UNKNOWN", "An export has no semicolon.", tokens[cursor].line))
        return _skip_loose(tokens, cursor)
    body = tokens[cursor + 1 : end]
    module = _export_module(body)
    if module is None:
        parsed.warnings.append(
            _warn("TS_IMPORT_UNKNOWN", "An export was not a supported literal form.", tokens[cursor].line)
        )
    else:
        parsed.imports.append(
            TsImport(
                tokens[cursor].line,
                _evidence(text, tokens[cursor].start, tokens[end].end),
                "export_from",
                module[0],
                module[1],
            )
        )
    return end + 1


def _export_braces(text: str, tokens: list[_Tok], cursor: int, parsed: _Parsed) -> int:
    end = _statement_end(tokens, cursor)
    if end is None:
        parsed.warnings.append(_warn("TS_IMPORT_UNKNOWN", "An export has no semicolon.", tokens[cursor].line))
        return _skip_loose(tokens, cursor)
    body = tokens[cursor + 1 : end]
    offset = 0
    if body and body[0].kind == "ident" and body[0].value == "type":
        offset = 1
    if offset >= len(body) or body[offset].value != "{":
        parsed.warnings.append(
            _warn("TS_IMPORT_UNKNOWN", "An export was not a supported literal form.", tokens[cursor].line)
        )
        return end + 1
    close = _match(body, offset, "{", "}")
    specifiers = None if close is None else _specifiers(body[offset + 1 : close])
    rest = [] if close is None else body[close + 1 :]
    if specifiers is None:
        parsed.warnings.append(
            _warn("TS_IMPORT_UNKNOWN", "An export was not a supported literal form.", tokens[cursor].line)
        )
        return end + 1
    if not rest:
        parsed.exports.extend(specifiers)
    elif len(rest) == 2 and rest[0].kind == "ident" and rest[0].value == "from" and rest[1].kind == "string":
        parsed.imports.append(
            TsImport(
                tokens[cursor].line,
                _evidence(text, tokens[cursor].start, tokens[end].end),
                "export_from",
                _module(rest[1].value),
                specifiers,
            )
        )
    else:
        parsed.warnings.append(
            _warn("TS_IMPORT_UNKNOWN", "An export was not a supported literal form.", tokens[cursor].line)
        )
    return end + 1


def _export_module(body: list[_Tok]) -> tuple[str, tuple[str, ...]] | None:
    if (
        len(body) == 3
        and body[0].value == "*"
        and body[1].kind == "ident"
        and body[1].value == "from"
        and body[2].kind == "string"
    ):
        return _module(body[2].value), ()
    if (
        len(body) == 5
        and body[0].value == "*"
        and body[1].kind == "ident"
        and body[1].value == "as"
        and body[2].kind == "ident"
        and body[3].kind == "ident"
        and body[3].value == "from"
        and body[4].kind == "string"
    ):
        return _module(body[4].value), (body[2].value,)
    return None


def _function(
    text: str,
    tokens: list[_Tok],
    cursor: int,
    parsed: _Parsed,
    exported: bool,
    defaulted: bool,
    *,
    async_function: bool,
) -> int:
    name, after_name = _declaration_name(tokens, cursor + 1, defaulted)
    if after_name is None or name is None:
        parsed.warnings.append(_warn("TS_SYMBOL_UNKNOWN", "A function name was not recorded.", tokens[cursor].line))
        return _skip_loose(tokens, cursor)
    body = _signature_body(tokens, after_name)
    if body is None:
        raise _Unsafe("A function body could not be bounded.", tokens[cursor].line)
    end, has_body = body
    if has_body:
        kind = "async_function" if async_function else "function"
        start = cursor - 1 if async_function else cursor
        parsed.symbols.append(
            TsSymbol(
                name,
                kind,
                tokens[start].line,
                tokens[end].line,
                _evidence(text, tokens[start].start, tokens[cursor].end),
            )
        )
        _note_nested(tokens, end, parsed)
    if exported and name != "default":
        parsed.exports.append(name)
    return end + 1


def _class(
    text: str,
    tokens: list[_Tok],
    cursor: int,
    parsed: _Parsed,
    exported: bool,
    defaulted: bool,
    decorators: list[tuple[str, int, int, dict[str, object] | None]],
) -> int:
    name, after_name = _declaration_name(tokens, cursor + 1, defaulted)
    if after_name is None or name is None:
        parsed.warnings.append(_warn("TS_SYMBOL_UNKNOWN", "A class name was not recorded.", tokens[cursor].line))
        return _skip_loose(tokens, cursor)
    brace = _heritage_brace(tokens, after_name)
    if brace is None:
        raise _Unsafe("A class body could not be bounded.", tokens[cursor].line)
    close = _match_tokens(tokens, brace, "{", "}")
    if close is None:
        raise _Unsafe("A class body could not be bounded.", tokens[cursor].line)
    start_line = tokens[cursor].line
    evidence_at = tokens[cursor].start
    if decorators:
        at = decorators[0][1] - 1
        start_line = min(start_line, tokens[at].line)
        evidence_at = tokens[at].start
    parsed.symbols.append(
        TsSymbol(
            name,
            "class",
            start_line,
            tokens[close].line,
            _evidence(text, evidence_at, tokens[cursor].end),
        )
    )
    if exported and name != "default":
        parsed.exports.append(name)
    _apply_decorators(text, tokens, decorators, parsed)
    _class_members(text, tokens, brace + 1, close, name, parsed)
    return close + 1


def _class_members(
    text: str, tokens: list[_Tok], start: int, close: int, class_name: str, parsed: _Parsed
) -> None:
    cursor = start
    while cursor < close:
        nxt = _member(text, tokens, cursor, close, class_name, parsed)
        if nxt <= cursor:
            raise _Unsafe("A class member could not be bounded.", tokens[cursor].line)
        cursor = nxt


def _member(
    text: str, tokens: list[_Tok], cursor: int, close: int, class_name: str, parsed: _Parsed
) -> int:
    if tokens[cursor].kind == "punct" and tokens[cursor].value in {";", ","}:
        return cursor + 1
    start = cursor
    while cursor < close and tokens[cursor].kind == "ident" and tokens[cursor].value in _MODIFIERS:
        cursor += 1
    accessor = False
    if cursor < close and tokens[cursor].kind == "ident" and tokens[cursor].value in {"get", "set"}:
        nxt = tokens[cursor + 1] if cursor + 1 < close else None
        if nxt is not None and nxt.kind == "ident":
            accessor = True
            cursor += 1
    if cursor >= close:
        raise _Unsafe("A class member could not be bounded.", tokens[start].line)
    token = tokens[cursor]
    if token.kind == "punct" and token.value == "[":
        parsed.warnings.append(
            _warn("TS_SYMBOL_UNKNOWN", "A computed class member was not indexed.", token.line)
        )
        end = _member_end(tokens, cursor, close)
        if end is None:
            raise _Unsafe("A computed class member could not be bounded.", token.line)
        return end + 1
    if token.kind != "ident":
        parsed.warnings.append(_warn("TS_SYMBOL_UNKNOWN", "A class member was not indexed.", token.line))
        end = _member_end(tokens, cursor, close)
        if end is None:
            raise _Unsafe("A class member could not be bounded.", token.line)
        return end + 1
    name = token.value
    after = cursor + 1
    if after < close and tokens[after].kind == "punct" and tokens[after].value == "<":
        generic = _match_tokens(tokens, after, "<", ">")
        if generic is None:
            raise _Unsafe("A class member generic was not bounded.", token.line)
        after = generic + 1
    if after < close and tokens[after].kind == "punct" and tokens[after].value == "(":
        params = _match_tokens(tokens, after, "(", ")")
        if params is None:
            raise _Unsafe("A method signature was not bounded.", token.line)
        body = _after_signature(tokens, params + 1, close)
        if body is None:
            raise _Unsafe("A method body was not bounded.", token.line)
        end, has_body = body
        if has_body or accessor or name == "constructor":
            parsed.symbols.append(
                TsSymbol(
                    f"{class_name}.{name}",
                    "method",
                    token.line,
                    tokens[end].line,
                    _evidence(text, tokens[start].start, token.end),
                )
            )
            if has_body:
                _note_nested(tokens, end, parsed)
        return end + 1
    if after < close and tokens[after].kind == "punct" and tokens[after].value in {"?", "!"}:
        after += 1
    if after < close and tokens[after].kind == "punct" and tokens[after].value == "=":
        arrow = _arrow_body(tokens, after + 1, close)
        if arrow is not None:
            end, has_body = arrow
            if has_body:
                parsed.symbols.append(
                    TsSymbol(
                        f"{class_name}.{name}",
                        "method",
                        token.line,
                        tokens[end].line,
                        _evidence(text, tokens[start].start, token.end),
                    )
                )
                _note_nested(tokens, end, parsed)
            return end + 1
    end = _member_end(tokens, cursor, close)
    if end is None:
        raise _Unsafe("A class field could not be bounded.", token.line)
    parsed.symbols.append(
        TsSymbol(
            f"{class_name}.{name}",
            "property",
            token.line,
            tokens[end].line,
            _evidence(text, tokens[start].start, token.end),
        )
    )
    return end + 1


def _bindings(text: str, tokens: list[_Tok], cursor: int, parsed: _Parsed, exported: bool) -> int:
    end = _statement_end(tokens, cursor)
    if end is None:
        return _skip_loose(tokens, cursor)
    body = tokens[cursor + 1 : end]
    offset = 0
    while offset < len(body):
        if body[offset].kind != "ident":
            break
        name = body[offset].value
        if exported:
            parsed.exports.append(name)
        nxt = offset + 1
        if nxt < len(body) and body[nxt].kind == "punct" and body[nxt].value == "=":
            arrow = _arrow_body(body, nxt + 1, len(body))
            if arrow is not None and arrow[1]:
                parsed.symbols.append(
                    TsSymbol(
                        name,
                        "function",
                        body[offset].line,
                        body[arrow[0]].line,
                        _evidence(text, body[offset].start, body[offset].end),
                    )
                )
                _note_nested(tokens, _token_index(tokens, body[arrow[0]]), parsed)
            elif tokens[end].line > body[offset].line:
                parsed.symbols.append(
                    TsSymbol(
                        name,
                        "binding",
                        body[offset].line,
                        tokens[end].line,
                        _evidence(text, body[offset].start, body[offset].end),
                    )
                )
        comma = _comma_at_depth(body, nxt)
        if comma is None:
            break
        offset = comma + 1
    return end + 1


def _leading_decorators(
    text: str, tokens: list[_Tok], cursor: int, parsed: _Parsed
) -> tuple[list[tuple[str, int, int, dict[str, object] | None]], int]:
    found: list[tuple[str, int, int, dict[str, object] | None]] = []
    while cursor < len(tokens) and tokens[cursor].kind == "punct" and tokens[cursor].value == "@":
        if cursor + 1 >= len(tokens) or tokens[cursor + 1].kind != "ident":
            parsed.warnings.append(
                _warn("TS_DECORATOR_UNKNOWN", "A decorator name was not recorded.", tokens[cursor].line)
            )
            return found, cursor + 1
        name = tokens[cursor + 1].value
        name_at = cursor + 1
        payload: dict[str, object] | None = {}
        cursor = name_at + 1
        if cursor < len(tokens) and tokens[cursor].kind == "punct" and tokens[cursor].value == "(":
            if cursor + 1 < len(tokens) and tokens[cursor + 1].kind == "punct" and tokens[cursor + 1].value == "{":
                obj = _object_literal(tokens, cursor + 1)
                if obj is None:
                    payload = None
                    close = _match_tokens(tokens, cursor, "(", ")")
                    if close is None:
                        raise _Unsafe("A decorator argument was not bounded.", tokens[name_at].line)
                    cursor = close + 1
                else:
                    payload, cursor = obj
                    if cursor >= len(tokens) or tokens[cursor].value != ")":
                        payload = None
                    else:
                        cursor += 1
            else:
                payload = None
                close = _match_tokens(tokens, cursor, "(", ")")
                if close is None:
                    raise _Unsafe("A decorator argument was not bounded.", tokens[name_at].line)
                cursor = close + 1
        if name in {"Component", "NgModule"} and payload is None:
            code = "TS_COMPONENT_UNKNOWN" if name == "Component" else "TS_NGMODULE_UNKNOWN"
            parsed.warnings.append(
                _warn(code, f"@{name} metadata is not a supported literal object.", tokens[name_at].line)
            )
        found.append((name, name_at, cursor, payload))
        del text
    return found, cursor


def _apply_decorators(
    text: str,
    tokens: list[_Tok],
    decorators: list[tuple[str, int, int, dict[str, object] | None]],
    parsed: _Parsed,
) -> None:
    for name, name_at, end, payload in decorators:
        evidence = _evidence(text, tokens[name_at - 1].start, tokens[min(end, len(tokens)) - 1].end)
        if name == "Component":
            if parsed.component.status != "absent":
                parsed.component = ComponentMetadata("unknown", evidence=evidence)
                parsed.warnings.append(
                    _warn("TS_COMPONENT_UNKNOWN", "More than one @Component was seen.", tokens[name_at].line)
                )
                continue
            parsed.component = _component_from(payload, evidence, parsed, tokens[name_at].line)
        elif name == "NgModule":
            if parsed.ng_module.status != "absent":
                parsed.ng_module = NgModuleMetadata("unknown", evidence=evidence)
                parsed.warnings.append(
                    _warn("TS_NGMODULE_UNKNOWN", "More than one @NgModule was seen.", tokens[name_at].line)
                )
                continue
            parsed.ng_module = _ng_module_from(payload, evidence, parsed, tokens[name_at].line)


def _component_from(
    payload: dict[str, object] | None, evidence: str, parsed: _Parsed, line: int
) -> ComponentMetadata:
    if payload is None:
        return ComponentMetadata("unknown", evidence=evidence)
    selector = payload.get("selector")
    template = payload.get("templateUrl")
    if "selector" in payload and not isinstance(selector, str):
        parsed.warnings.append(_warn("TS_COMPONENT_UNKNOWN", "@Component selector is not a literal.", line))
        return ComponentMetadata("unknown", evidence=evidence)
    if "templateUrl" in payload and not isinstance(template, str):
        parsed.warnings.append(
            _warn("TS_COMPONENT_UNKNOWN", "@Component templateUrl is not a literal.", line)
        )
        return ComponentMetadata("unknown", evidence=evidence)
    if "styleUrls" in payload:
        styles = _tagged(payload.get("styleUrls"), "string")
        if styles is None:
            parsed.warnings.append(
                _warn("TS_COMPONENT_UNKNOWN", "@Component styleUrls is not a string array.", line)
            )
            return ComponentMetadata("unknown", evidence=evidence)
    else:
        styles = ()
    if "standalone" in payload and not isinstance(payload.get("standalone"), bool):
        parsed.warnings.append(
            _warn("TS_COMPONENT_UNKNOWN", "@Component standalone is not a literal boolean.", line)
        )
        return ComponentMetadata("unknown", evidence=evidence)
    inline = payload.get("template")
    if "template" in payload and not isinstance(inline, str):
        parsed.warnings.append(_warn("TS_COMPONENT_UNKNOWN", "@Component template is not a literal.", line))
        inline = None
    standalone = payload.get("standalone")
    return ComponentMetadata(
        "present",
        selector if isinstance(selector, str) else None,
        template if isinstance(template, str) else None,
        styles,
        standalone if isinstance(standalone, bool) else None,
        evidence,
        inline if isinstance(inline, str) else None,
    )


def _ng_module_from(
    payload: dict[str, object] | None, evidence: str, parsed: _Parsed, line: int
) -> NgModuleMetadata:
    if payload is None:
        return NgModuleMetadata("unknown", evidence=evidence)
    declarations = _optional_names(payload, "declarations")
    imports = _optional_names(payload, "imports")
    providers = _optional_names(payload, "providers")
    if declarations is None or imports is None or providers is None:
        parsed.warnings.append(
            _warn("TS_NGMODULE_UNKNOWN", "@NgModule metadata is not a list of identifiers.", line)
        )
        return NgModuleMetadata("unknown", evidence=evidence)
    return NgModuleMetadata("present", declarations, imports, evidence, providers or ())


def _object_literal(
    tokens: list[_Tok], start: int
) -> tuple[dict[str, object], int] | None:
    if start >= len(tokens) or tokens[start].value != "{":
        return None
    cursor = start + 1
    values: dict[str, object] = {}
    while cursor < len(tokens):
        if tokens[cursor].kind == "punct" and tokens[cursor].value == "}":
            return values, cursor + 1
        if tokens[cursor].kind != "ident":
            return None
        key = tokens[cursor].value
        cursor += 1
        if cursor >= len(tokens) or tokens[cursor].value != ":":
            return None
        cursor += 1
        read = _literal_value(tokens, cursor)
        if read is None:
            skipped = _skip_value(tokens, cursor)
            if skipped is None:
                return None
            values[key] = None
            cursor = skipped
        else:
            value, cursor = read
            values[key] = value
        if cursor < len(tokens) and tokens[cursor].value == ",":
            cursor += 1
            continue
        if cursor < len(tokens) and tokens[cursor].value == "}":
            return values, cursor + 1
        return None
    return None


def _literal_value(tokens: list[_Tok], cursor: int) -> tuple[object, int] | None:
    if cursor >= len(tokens):
        return None
    token = tokens[cursor]
    if token.kind == "string":
        return token.value, cursor + 1
    if token.kind == "ident" and token.value in {"true", "false"}:
        return token.value == "true", cursor + 1
    if token.kind == "punct" and token.value == "[":
        close = _match_tokens(tokens, cursor, "[", "]")
        if close is None:
            return None
        items: list[tuple[str, str]] = []
        inner = cursor + 1
        while inner < close:
            if tokens[inner].kind == "punct" and tokens[inner].value == ",":
                inner += 1
                continue
            if tokens[inner].kind != "string" and not (
                tokens[inner].kind == "ident" and tokens[inner].value not in {"true", "false", "null"}
            ):
                return None
            if tokens[inner].kind == "string":
                items.append((tokens[inner].kind, tokens[inner].value))
            else:
                items.append((tokens[inner].kind, tokens[inner].value))
            inner += 1
            if inner < close and tokens[inner].value == ",":
                inner += 1
        return tuple(items), close + 1
    return None


def _optional_names(payload: dict[str, object], key: str) -> tuple[str, ...] | None:
    if key not in payload:
        return ()
    return _tagged(payload[key], "ident")


def _tagged(value: object, kind: str) -> tuple[str, ...] | None:
    if not isinstance(value, tuple):
        return None
    names: list[str] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2 or item[0] != kind or not isinstance(item[1], str):
            return None
        names.append(item[1])
    return tuple(names)


def _signature_body(tokens: list[_Tok], cursor: int) -> tuple[int, bool] | None:
    if cursor < len(tokens) and tokens[cursor].kind == "punct" and tokens[cursor].value == "<":
        generic = _match_tokens(tokens, cursor, "<", ">")
        if generic is None:
            return None
        cursor = generic + 1
    if cursor >= len(tokens) or tokens[cursor].value != "(":
        return None
    params = _match_tokens(tokens, cursor, "(", ")")
    if params is None:
        return None
    return _after_signature(tokens, params + 1, len(tokens))


def _after_signature(tokens: list[_Tok], cursor: int, limit: int) -> tuple[int, bool] | None:
    if cursor < limit and tokens[cursor].kind == "punct" and tokens[cursor].value == ":":
        cursor += 1
        while cursor < limit:
            if tokens[cursor].kind == "punct" and tokens[cursor].value == "{":
                typed = _match_tokens(tokens, cursor, "{", "}")
                if typed is None:
                    return None
                nxt = typed + 1
                if nxt < limit and tokens[nxt].kind == "punct" and tokens[nxt].value in {"{", ";"}:
                    cursor = nxt
                    continue
                return typed, True
            if tokens[cursor].kind == "punct" and tokens[cursor].value == ";" and _depth_clear(tokens, cursor):
                return cursor, False
            if tokens[cursor].kind == "punct" and tokens[cursor].value in {"(", "[", "<"}:
                pair = {"(": ")", "[": "]", "<": ">"}[tokens[cursor].value]
                close = _match_tokens(tokens, cursor, tokens[cursor].value, pair)
                if close is None:
                    return None
                cursor = close + 1
                continue
            cursor += 1
        return None
    if cursor < limit and tokens[cursor].kind == "punct" and tokens[cursor].value == "{":
        close = _match_tokens(tokens, cursor, "{", "}")
        if close is None:
            return None
        return close, True
    if cursor < limit and tokens[cursor].kind == "punct" and tokens[cursor].value == ";":
        return cursor, False
    return None


def _arrow_body(tokens: list[_Tok], cursor: int, limit: int) -> tuple[int, bool] | None:
    if cursor < limit and tokens[cursor].kind == "ident" and tokens[cursor].value == "async":
        cursor += 1
    if cursor >= limit:
        return None
    if tokens[cursor].kind == "punct" and tokens[cursor].value == "(":
        close = _match_tokens(tokens, cursor, "(", ")")
        if close is None:
            return None
        cursor = close + 1
    elif tokens[cursor].kind == "ident":
        cursor += 1
    else:
        return None
    if (
        cursor + 1 >= limit
        or tokens[cursor].value != "="
        or tokens[cursor + 1].value != ">"
    ):
        return None
    cursor += 2
    if cursor < limit and tokens[cursor].value == "{":
        end = _match_tokens(tokens, cursor, "{", "}")
        if end is None:
            return None
        return end, True
    while cursor < limit:
        if tokens[cursor].kind == "punct" and tokens[cursor].value == ";":
            return cursor, True
        if tokens[cursor].kind == "punct" and tokens[cursor].value in {"(", "[", "{", "<"}:
            pair = {"(": ")", "[": "]", "{": "}", "<": ">"}[tokens[cursor].value]
            close = _match_tokens(tokens, cursor, tokens[cursor].value, pair)
            if close is None:
                return None
            cursor = close + 1
            continue
        cursor += 1
    return None


def _heritage_brace(tokens: list[_Tok], cursor: int) -> int | None:
    while cursor < len(tokens):
        token = tokens[cursor]
        if token.kind == "punct" and token.value == "{":
            return cursor
        if token.kind == "punct" and token.value == ";":
            return None
        if token.kind == "punct" and token.value in {"(", "[", "<"}:
            pair = {"(": ")", "[": "]", "<": ">"}[token.value]
            close = _match_tokens(tokens, cursor, token.value, pair)
            if close is None:
                return None
            cursor = close + 1
            continue
        cursor += 1
    return None


def _declaration_name(tokens: list[_Tok], cursor: int, defaulted: bool) -> tuple[str | None, int | None]:
    if cursor < len(tokens) and tokens[cursor].kind == "ident":
        return tokens[cursor].value, cursor + 1
    if defaulted:
        return "default", cursor
    return None, None


def _statement_end(tokens: list[_Tok], cursor: int) -> int | None:
    depth = 0
    for index in range(cursor + 1, len(tokens)):
        token = tokens[index]
        if token.kind == "punct" and token.value in {"(", "[", "{"}:
            depth += 1
            continue
        if token.kind == "punct" and token.value in {")", "]", "}"}:
            depth -= 1
            if depth < 0:
                return None
            continue
        if depth == 0 and token.kind == "punct" and token.value == ";":
            return index
        if depth == 0 and token.kind == "punct" and token.value == "@":
            return None
        if depth == 0 and token.kind == "ident" and token.value in _DECLARATION_STOP and index > cursor + 1:
            return None
    return None


def _member_end(tokens: list[_Tok], cursor: int, limit: int) -> int | None:
    depth = 0
    for index in range(cursor, limit):
        token = tokens[index]
        if token.kind == "punct" and token.value in {"(", "[", "{", "<"}:
            depth += 1
            continue
        if token.kind == "punct" and token.value in {")", "]", "}", ">"}:
            depth = max(0, depth - 1)
            continue
        if depth == 0 and token.kind == "punct" and token.value in {";", ","}:
            return index
    return None


def _skip_value(tokens: list[_Tok], cursor: int) -> int | None:
    if cursor >= len(tokens):
        return None
    token = tokens[cursor]
    if token.kind == "punct" and token.value in {"(", "[", "{", "<"}:
        pair = {"(": ")", "[": "]", "{": "}", "<": ">"}[token.value]
        close = _match_tokens(tokens, cursor, token.value, pair)
        return None if close is None else close + 1
    return cursor + 1


def _skip_loose(tokens: list[_Tok], cursor: int) -> int:
    end = _statement_end(tokens, cursor)
    if end is not None:
        return end + 1
    token = tokens[cursor]
    if token.kind == "punct" and token.value in {"(", "[", "{", "<"}:
        pair = {"(": ")", "[": "]", "{": "}", "<": ">"}[token.value]
        close = _match_tokens(tokens, cursor, token.value, pair)
        if close is not None:
            return close + 1
    return cursor + 1


def _comma_at_depth(tokens: list[_Tok], cursor: int) -> int | None:
    depth = 0
    for index in range(cursor, len(tokens)):
        token = tokens[index]
        if token.kind == "punct" and token.value in {"(", "[", "{", "<"}:
            depth += 1
        elif token.kind == "punct" and token.value in {")", "]", "}", ">"}:
            depth = max(0, depth - 1)
        elif depth == 0 and token.kind == "punct" and token.value == ",":
            return index
    return None


def _note_nested(tokens: list[_Tok], close: int, parsed: _Parsed) -> None:
    open_at = _match_open(tokens, close, "{", "}")
    if open_at is None:
        return
    for token in tokens[open_at + 1 : close]:
        if token.kind == "ident" and token.value == "function":
            parsed.warnings.append(
                _warn(
                    "TS_NESTED_FUNCTION",
                    "A nested function was not indexed.",
                    token.line,
                )
            )
            return


def _specifiers(tokens: list[_Tok]) -> tuple[str, ...] | None:
    if not tokens:
        return ()
    names: list[str] = []
    cursor = 0
    while cursor < len(tokens):
        if tokens[cursor].kind == "punct" and tokens[cursor].value == ",":
            cursor += 1
            continue
        if tokens[cursor].kind == "ident" and tokens[cursor].value == "type":
            cursor += 1
            if cursor >= len(tokens):
                return None
        if cursor >= len(tokens) or tokens[cursor].kind != "ident":
            return None
        name = tokens[cursor].value
        cursor += 1
        if cursor < len(tokens) and tokens[cursor].kind == "ident" and tokens[cursor].value == "as":
            cursor += 1
            if cursor >= len(tokens) or tokens[cursor].kind != "ident":
                return None
            name = tokens[cursor].value
            cursor += 1
        names.append(name)
        if cursor < len(tokens) and tokens[cursor].kind == "punct" and tokens[cursor].value == ",":
            cursor += 1
            continue
        if cursor < len(tokens):
            return None
    return tuple(names)


def _match(tokens: list[_Tok], start: int, open_value: str, close_value: str) -> int | None:
    return _match_tokens(tokens, start, open_value, close_value)


def _match_tokens(tokens: list[_Tok], start: int, open_value: str, close_value: str) -> int | None:
    if start >= len(tokens) or tokens[start].value != open_value:
        return None
    depth = 0
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.kind == "punct" and token.value == open_value:
            depth += 1
        elif token.kind == "punct" and token.value == close_value:
            depth -= 1
            if depth == 0:
                return index
    return None


def _match_open(tokens: list[_Tok], close: int, open_value: str, close_value: str) -> int | None:
    depth = 0
    for index in range(close, -1, -1):
        token = tokens[index]
        if token.kind == "punct" and token.value == close_value:
            depth += 1
        elif token.kind == "punct" and token.value == open_value:
            depth -= 1
            if depth == 0:
                return index
    return None


def _depth_clear(tokens: list[_Tok], cursor: int) -> bool:
    del tokens, cursor
    return True


def _token_index(tokens: list[_Tok], token: _Tok) -> int:
    for index, item in enumerate(tokens):
        if item is token or (item.start == token.start and item.value == token.value and item.kind == token.kind):
            return index
    return 0


def _tokenize(text: str, masked: str, strings: dict[int, tuple[int, str]]) -> list[_Tok]:
    tokens: list[_Tok] = []
    index = 0
    length = len(text)
    while index < length:
        if index in strings:
            end, value = strings[index]
            tokens.append(_Tok("string", value, index, end, _line_at(text, index)))
            index = end
            continue
        char = masked[index]
        if char != text[index] or char.isspace():
            index += 1
            continue
        if char == "." and text[index : index + 3] == "...":
            tokens.append(_Tok("punct", "...", index, index + 3, _line_at(text, index)))
            index += 3
            continue
        if _is_ident_start(char):
            end = index + 1
            while end < length and _is_ident_continue(masked[end]) and masked[end] == text[end]:
                end += 1
            tokens.append(_Tok("ident", text[index:end], index, end, _line_at(text, index)))
            index = end
            continue
        if char in "{}()[];,.:<>?=*&|!~+-/%^@#":
            tokens.append(_Tok("punct", char, index, index + 1, _line_at(text, index)))
            index += 1
            continue
        if char.isdigit():
            end = index + 1
            while end < length and (masked[end].isdigit() or masked[end] in {".", "_"}):
                end += 1
            index = end
            continue
        index += 1
    return tokens


def _mask(text: str) -> _Masked | None:
    chars = [" "] * len(text)
    strings: dict[int, tuple[int, str]] = {}
    stack = ["code"]
    interp: list[int] = []
    brace = 0
    index = 0
    length = len(text)

    def keep(at: int) -> None:
        chars[at] = text[at]

    while index < length:
        state = stack[-1]
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if state == "line":
            if char == "\n":
                keep(index)
                stack.pop()
            index += 1
            continue
        if state == "block":
            if char == "\n":
                keep(index)
            if char == "*" and nxt == "/":
                index += 2
                stack.pop()
                continue
            index += 1
            continue
        if state in {"squote", "dquote"}:
            if char == "\\":
                index += 2 if index + 1 < length else 1
                continue
            if char == "\n":
                return None
            quote = "'" if state == "squote" else '"'
            if char == quote:
                stack.pop()
            index += 1
            continue
        if state == "template":
            if char == "\n":
                keep(index)
            if char == "\\":
                index += 2 if index + 1 < length else 1
                continue
            if char == "`":
                stack.pop()
                index += 1
                continue
            if char == "$" and nxt == "{":
                stack.append("code")
                brace += 1
                interp.append(brace)
                index += 2
                continue
            index += 1
            continue
        if char == "\n":
            keep(index)
            index += 1
            continue
        if char == "/" and nxt == "/":
            stack.append("line")
            index += 2
            continue
        if char == "/" and nxt == "*":
            stack.append("block")
            index += 2
            continue
        if char == "/" and nxt != "=" and _regex_prefix(chars, index):
            closed = _mask_regex(text, chars, index)
            if closed is None:
                return None
            index = closed
            continue
        if char in {"'", '"', "`"}:
            literal = _take_literal(text, index)
            if literal is None:
                if char == "`":
                    stack.append("template")
                    index += 1
                    continue
                stack.append("squote" if char == "'" else "dquote")
                index += 1
                continue
            end, value, simple = literal
            if simple:
                strings[index] = (end, value)
                index = end
                continue
            stack.append("template")
            index += 1
            continue
        if char == "{":
            brace += 1
            keep(index)
            index += 1
            continue
        if char == "}":
            if interp and brace == interp[-1]:
                interp.pop()
                stack.pop()
                brace -= 1
                index += 1
                continue
            brace -= 1
            keep(index)
            index += 1
            continue
        keep(index)
        index += 1
    if stack != ["code"] or interp or brace != 0:
        return None
    return _Masked("".join(chars), strings)


def _take_literal(text: str, start: int) -> tuple[int, str, bool] | None:
    quote = text[start]
    if quote not in {"'", '"', "`"}:
        return None
    index = start + 1
    pieces: list[str] = []
    simple = True
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text):
                return None
            pieces.append(text[index + 1])
            index += 2
            continue
        if quote == "`" and char == "$" and index + 1 < len(text) and text[index + 1] == "{":
            return None
        if char == quote:
            return index + 1, "".join(pieces), simple
        if char == "\n" and quote != "`":
            return None
        pieces.append(char)
        index += 1
    return None


def _mask_regex(text: str, chars: list[str], start: int) -> int | None:
    index = start + 1
    in_class = False
    while index < len(text):
        char = text[index]
        if char == "\n":
            return None
        if char == "\\":
            index += 2 if index + 1 < len(text) else 1
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            index += 1
            while index < len(text) and text[index] in {"d", "g", "i", "m", "s", "u", "y"}:
                chars[index] = " "
                index += 1
            return index
        index += 1
    return None


def _regex_prefix(chars: list[str], index: int) -> bool:
    previous = ""
    for cursor in range(index - 1, -1, -1):
        if chars[cursor] not in {" ", "\n", "\r", "\t"}:
            previous = chars[cursor]
            break
    return previous in {"", "(", "[", "{", "=", ":", ",", ";", "!", "&", "|", "?", "+", "-", "*", "~", "}"}


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _line_count(text: str) -> int:
    if not text:
        return 0
    count = text.count("\n")
    if text.endswith("\n"):
        return count
    return count + 1


def _line_at(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _has_bare_carriage_return(text: str) -> bool:
    return "\r" in text.replace("\r\n", "")


def _is_generated(path: str, text: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    if lowered.endswith(".generated.ts") or lowered.endswith(".generated.tsx"):
        return True
    if "generated" in lowered.split("/"):
        return True
    head = text.lstrip("\ufeff")[:400].lower()
    return "@generated" in head or "code generated" in head or "do not edit" in head


def _evidence(text: str, start: int, end: int) -> str:
    snippet = " ".join(text[start:end].split())
    if len(snippet) > 240:
        snippet = snippet[:237] + "..."
    return redact_secrets(snippet)


def _module(value: str) -> str:
    return redact_secrets(value)


def _warn(code: str, message: str, line: int | None = None, path: str | None = None) -> TsWarning:
    return TsWarning(code, redact_secrets(message), path, line)


def _empty_ts(status: str, line_count: int, warning: TsWarning) -> TsFileIndex:
    return TsFileIndex(
        "",
        status,
        line_count,
        (),
        (),
        (),
        ComponentMetadata("absent"),
        NgModuleMetadata("absent"),
        (warning,),
    )


def _bind_ts(indexed: TsFileIndex, path: str) -> TsFileIndex:
    status = indexed.status
    warnings = indexed.warnings
    if _is_generated(path, "") and status == "parsed":
        status = "skipped"
        warnings = (
            _warn("TS_GENERATED", "Generated TypeScript was not indexed.", path=path),
        )
        return TsFileIndex(
            path,
            status,
            indexed.line_count,
            (),
            (),
            (),
            ComponentMetadata("absent"),
            NgModuleMetadata("absent"),
            warnings,
        )
    return replace(
        indexed,
        path=path,
        warnings=tuple(replace(item, path=path) for item in warnings),
    )


def _bind_html(indexed: HtmlFileIndex, path: str) -> HtmlFileIndex:
    return replace(indexed, path=path, warnings=tuple(replace(item, path=path) for item in indexed.warnings))


def _is_ident_start(char: str) -> bool:
    return char == "$" or char == "_" or char.isalpha()


def _is_ident_continue(char: str) -> bool:
    return _is_ident_start(char) or char.isdigit()


def _chunked_metadata(adapter: GitAdapter, shas: list[str]) -> list[tuple[str, int] | None]:
    records: list[tuple[str, int] | None] = []
    for start in range(0, len(shas), 200):
        records.extend(adapter.read_blob_metadata(shas[start : start + 200]))
    return records


def _chunked_blobs(adapter: GitAdapter, shas: list[str]) -> dict[str, bytes | None]:
    bodies: dict[str, bytes | None] = {}
    unique = list(dict.fromkeys(shas))
    for start in range(0, len(unique), _READ_CHUNK):
        chunk = unique[start : start + _READ_CHUNK]
        bodies.update(zip(chunk, adapter.read_blobs(chunk), strict=True))
    return bodies
