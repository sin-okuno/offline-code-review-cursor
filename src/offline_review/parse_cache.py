"""Optional on-disk cache of one commit's parsed Python tree.

The key is the commit object id, the parser version, and the configured
import roots. A changed root or parser version misses. The cache stores
symbols and import statements, not the original source bytes. Entries are
evicted oldest-first until the directory is within its byte cap. One record
larger than the cap is kept so the parse that was just paid for can be reused;
older records are removed first.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from offline_review.dependency_index import IndexedModule
from offline_review.models import UsageError, Warning
from offline_review.python_indexer import PARSER_VERSION, ParseResult, RawImport, Symbol
from offline_review.reporters import is_reparse_point

CACHE_SCHEMA = "1"
DEFAULT_MAX_CACHE_BYTES = 64 * 1024 * 1024
_CACHE_NAME = re.compile(r"^[0-9a-f]{64}\.json$")


class ParseCache:
    """Read and write commit parse records under one directory."""

    def __init__(self, directory: Path, *, max_bytes: int = DEFAULT_MAX_CACHE_BYTES) -> None:
        if max_bytes < 1:
            raise UsageError("parse cache byte cap must be positive")
        if is_reparse_point(directory):
            raise UsageError("parse cache directory must not be a symlink or junction")
        directory.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(directory):
            raise UsageError("parse cache directory must not be a symlink or junction")
        self.directory = directory
        self.max_bytes = max_bytes

    def load(
        self, commit: str, roots: tuple[str, ...]
    ) -> tuple[dict[str, IndexedModule], tuple[Warning, ...]] | None:
        path = self._path(commit, roots)
        if is_reparse_point(path) or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            _unlink_quiet(path)
            return None
        if not _payload_matches(payload, commit, roots):
            return None
        try:
            return _decode(payload, commit)
        except (KeyError, TypeError, ValueError):
            _unlink_quiet(path)
            return None

    def store(
        self,
        commit: str,
        roots: tuple[str, ...],
        modules: dict[str, IndexedModule],
        blobs: dict[str, str | None],
    ) -> None:
        path = self._path(commit, roots)
        if is_reparse_point(path):
            raise UsageError("refusing to write a parse cache through a symlink")
        text = json.dumps(
            _encode(commit, roots, modules, blobs),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
        self._evict(keep=path)

    def _path(self, commit: str, roots: tuple[str, ...]) -> Path:
        return self.directory / f"{cache_key(commit, roots)}.json"

    def _evict(self, *, keep: Path) -> None:
        files = [
            path
            for path in self.directory.iterdir()
            if path.is_file() and _CACHE_NAME.fullmatch(path.name) and not is_reparse_point(path)
        ]
        files.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= self.max_bytes or path == keep:
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size


def cache_key(commit: str, roots: tuple[str, ...]) -> str:
    """Return the stable file id for one commit and import-root set."""

    payload = json.dumps(
        {
            "cache_schema": CACHE_SCHEMA,
            "commit": commit,
            "parser_version": PARSER_VERSION,
            "python_roots": list(roots),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload_matches(payload: object, commit: str, roots: tuple[str, ...]) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("cache_schema") == CACHE_SCHEMA
        and payload.get("commit") == commit
        and payload.get("parser_version") == PARSER_VERSION
        and payload.get("python_roots") == list(roots)
    )


def _encode(
    commit: str,
    roots: tuple[str, ...],
    modules: dict[str, IndexedModule],
    blobs: dict[str, str | None],
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for path in sorted(modules):
        result = modules[path].result
        records.append(
            {
                "blob": blobs.get(path),
                "dynamic_nonliteral": result.dynamic_nonliteral,
                "error": result.error,
                "imports": [_import_record(item) for item in result.imports],
                "line_count": result.line_count,
                "path": path,
                "status": result.status,
                "symbols": [_symbol_record(item) for item in result.symbols],
            }
        )
    return {
        "cache_schema": CACHE_SCHEMA,
        "commit": commit,
        "modules": records,
        "parser_version": PARSER_VERSION,
        "python_roots": list(roots),
    }


def _decode(
    payload: dict[str, object], commit: str
) -> tuple[dict[str, IndexedModule], tuple[Warning, ...]]:
    raw_modules = payload["modules"]
    if not isinstance(raw_modules, list):
        raise TypeError("modules")
    modules: dict[str, IndexedModule] = {}
    warnings: list[Warning] = []
    for record in raw_modules:
        if not isinstance(record, dict):
            raise TypeError("module")
        path = _text(record["path"])
        result = ParseResult(
            status=_text(record["status"]),
            error=record["error"] if record["error"] is None else _text(record["error"]),
            line_count=_count(record["line_count"]),
            symbols=tuple(_symbol(item) for item in _list(record["symbols"])),
            imports=tuple(_import(item) for item in _list(record["imports"])),
            dynamic_nonliteral=bool(record["dynamic_nonliteral"]),
        )
        modules[path] = IndexedModule(path, commit, result)
        blob = record["blob"]
        if result.status == "missing" and isinstance(blob, str):
            warnings.append(
                Warning(
                    code="MISSING_BLOB",
                    message=f"Python blob {blob} could not be read.",
                    path=path,
                    commit=commit,
                )
            )
    return modules, tuple(warnings)


def _symbol_record(symbol: Symbol) -> dict[str, object]:
    return {
        "end_line": symbol.end_line,
        "kind": symbol.kind,
        "qualname": symbol.qualname,
        "start_line": symbol.start_line,
    }


def _import_record(item: RawImport) -> dict[str, object]:
    return {
        "evidence": item.evidence,
        "form": item.form,
        "level": item.level,
        "line": item.line,
        "module": item.module,
        "names": list(item.names),
    }


def _symbol(record: object) -> Symbol:
    if not isinstance(record, dict):
        raise TypeError("symbol")
    return Symbol(
        qualname=_text(record["qualname"]),
        kind=_text(record["kind"]),
        start_line=_count(record["start_line"]),
        end_line=_count(record["end_line"]),
    )


def _import(record: object) -> RawImport:
    if not isinstance(record, dict):
        raise TypeError("import")
    module = record["module"]
    names = record["names"]
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise TypeError("names")
    return RawImport(
        line=_count(record["line"]),
        evidence=_text(record["evidence"]),
        form=_text(record["form"]),
        level=_count(record["level"]),
        module=None if module is None else _text(module),
        names=tuple(names),
    )


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("list")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("text")
    return value


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("count")
    return value


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        return
