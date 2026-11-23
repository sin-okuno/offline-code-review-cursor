"""On-disk TypeScript index cache.

A commit record is used only when its commit object id, the digest of every
indexed path and blob id, and the extractor version all match. A blob record
is the scan of one immutable blob and is shared by later commits that contain
that same blob id. The cache stores symbols and metadata, not source text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from offline_review.html_bindings import HtmlBinding
from offline_review.models import UsageError
from offline_review.reporters import is_reparse_point
from offline_review.typescript_index import (
    EXTRACTOR_VERSION,
    ComponentMetadata,
    HtmlFileIndex,
    NgModuleMetadata,
    TsCommitIndex,
    TsFileIndex,
    TsImport,
    TsReference,
    TsSymbol,
    TsWarning,
)

CACHE_SCHEMA = "1"
DEFAULT_MAX_CACHE_BYTES = 64 * 1024 * 1024
_COMMIT_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_PACK_NAME = "blob-pack.json"


class TypeScriptCache:
    """Remember commit indexes and blob scans under one directory."""

    def __init__(self, directory: Path, *, max_bytes: int = DEFAULT_MAX_CACHE_BYTES) -> None:
        if max_bytes < 1:
            raise UsageError("typescript cache byte cap must be positive")
        if is_reparse_point(directory):
            raise UsageError("typescript cache directory must not be a symlink or junction")
        directory.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(directory):
            raise UsageError("typescript cache directory must not be a symlink or junction")
        self.directory = directory
        self.max_bytes = max_bytes
        self._memory: dict[str, tuple[str, TsFileIndex | HtmlFileIndex]] = {}
        self._pack: dict[str, tuple[str, TsFileIndex | HtmlFileIndex]] | None = None
        self._pack_dirty = False

    def load_commit(
        self, commit: str, entries: tuple[tuple[str, str, str], ...]
    ) -> TsCommitIndex | None:
        digest = blob_digest(entries)
        path = self.directory / f"{commit_cache_key(commit, digest)}.json"
        if is_reparse_point(path) or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            _unlink_quiet(path)
            return None
        if not _commit_matches(payload, commit, digest):
            _unlink_quiet(path)
            return None
        try:
            return _decode_commit(payload)
        except (KeyError, TypeError, ValueError):
            _unlink_quiet(path)
            return None

    def store_commit(
        self, commit: str, entries: tuple[tuple[str, str, str], ...], index: TsCommitIndex
    ) -> None:
        digest = blob_digest(entries)
        path = self.directory / f"{commit_cache_key(commit, digest)}.json"
        if is_reparse_point(path):
            raise UsageError("refusing to write a typescript cache through a symlink")
        text = json.dumps(
            _encode_commit(commit, digest, index),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        _replace(path, text)
        self.save_pack()
        self._evict(keep=path)

    def load_blob(self, blob: str) -> tuple[str, TsFileIndex | HtmlFileIndex] | None:
        if blob in self._memory:
            return self._memory[blob]
        pack = self._load_pack()
        found = pack.get(blob)
        if found is not None:
            self._memory[blob] = found
        return found

    def store_blob(self, blob: str, kind: str, indexed: TsFileIndex | HtmlFileIndex) -> None:
        self._memory[blob] = (kind, indexed)
        self._load_pack()[blob] = (kind, indexed)
        self._pack_dirty = True

    def save_pack(self) -> None:
        if not self._pack_dirty or self._pack is None:
            return
        path = self.directory / _PACK_NAME
        if is_reparse_point(path):
            raise UsageError("refusing to write a typescript cache through a symlink")
        text = json.dumps(
            _encode_pack(self._pack),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        _replace(path, text)
        self._pack_dirty = False

    def _load_pack(self) -> dict[str, tuple[str, TsFileIndex | HtmlFileIndex]]:
        if self._pack is not None:
            return self._pack
        path = self.directory / _PACK_NAME
        loaded: dict[str, tuple[str, TsFileIndex | HtmlFileIndex]] = {}
        if path.is_file() and not is_reparse_point(path):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                loaded = _decode_pack(payload)
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                _unlink_quiet(path)
                loaded = {}
        self._pack = loaded
        return loaded

    def _evict(self, *, keep: Path) -> None:
        files = [
            path
            for path in self.directory.iterdir()
            if path.is_file() and _COMMIT_NAME.fullmatch(path.name) and not is_reparse_point(path)
        ]
        pack = self.directory / _PACK_NAME
        pack_size = pack.stat().st_size if pack.is_file() and not is_reparse_point(pack) else 0
        files.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
        total = pack_size + sum(path.stat().st_size for path in files)
        for path in files:
            if total <= self.max_bytes or path == keep:
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size


def commit_cache_key(commit: str, digest: str) -> str:
    """Return the file id for one commit, blob set, and extractor version."""

    payload = json.dumps(
        {
            "blob_digest": digest,
            "cache_schema": CACHE_SCHEMA,
            "commit": commit,
            "extractor_version": EXTRACTOR_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def blob_digest(entries: tuple[tuple[str, str, str], ...]) -> str:
    """Digest mode, blob id, and path so a different tree cannot reuse the record."""

    digest = hashlib.sha256()
    for mode, sha, path in sorted(entries, key=lambda item: item[2]):
        digest.update(mode.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha.encode("ascii"))
        digest.update(b"\0")
        digest.update(path.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


def _commit_matches(payload: object, commit: str, digest: str) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("cache_schema") == CACHE_SCHEMA
        and payload.get("extractor_version") == EXTRACTOR_VERSION
        and payload.get("commit") == commit
        and payload.get("blob_digest") == digest
        and payload.get("kind") == "commit"
    )


def _encode_commit(commit: str, digest: str, index: TsCommitIndex) -> dict[str, object]:
    return {
        "blob_digest": digest,
        "cache_schema": CACHE_SCHEMA,
        "commit": commit,
        "extractor_version": EXTRACTOR_VERSION,
        "files": [_encode_ts(item) for item in index.files],
        "html_files": [_encode_html(item) for item in index.html_files],
        "kind": "commit",
        "warnings": [_encode_warning(item) for item in index.warnings],
    }


def _decode_commit(payload: dict[str, object]) -> TsCommitIndex:
    files = payload["files"]
    html_files = payload["html_files"]
    warnings = payload["warnings"]
    if not isinstance(files, list) or not isinstance(html_files, list) or not isinstance(warnings, list):
        raise TypeError("commit cache record is not a list of files")
    commit = payload["commit"]
    if not isinstance(commit, str):
        raise TypeError("commit cache record has no commit")
    return TsCommitIndex(
        commit,
        tuple(_decode_ts(item) for item in files),
        tuple(_decode_html(item) for item in html_files),
        tuple(_decode_warning(item) for item in warnings),
    )


def _encode_pack(pack: dict[str, tuple[str, TsFileIndex | HtmlFileIndex]]) -> dict[str, object]:
    blobs: dict[str, object] = {}
    for sha in sorted(pack):
        kind, indexed = pack[sha]
        body = _encode_ts(indexed) if kind == "ts" else _encode_html(indexed)
        blobs[sha] = {"blob": sha, "body": body, "kind": kind}
    return {
        "blobs": blobs,
        "cache_schema": CACHE_SCHEMA,
        "extractor_version": EXTRACTOR_VERSION,
        "kind": "blob-pack",
    }


def _decode_pack(payload: object) -> dict[str, tuple[str, TsFileIndex | HtmlFileIndex]]:
    if not isinstance(payload, dict):
        raise TypeError("blob pack is not an object")
    if payload.get("cache_schema") != CACHE_SCHEMA or payload.get("extractor_version") != EXTRACTOR_VERSION:
        raise ValueError("blob pack version does not match")
    if payload.get("kind") != "blob-pack":
        raise ValueError("blob pack kind does not match")
    blobs = payload.get("blobs")
    if not isinstance(blobs, dict):
        raise TypeError("blob pack has no blob map")
    loaded: dict[str, tuple[str, TsFileIndex | HtmlFileIndex]] = {}
    for sha, item in blobs.items():
        if not isinstance(sha, str) or not isinstance(item, dict):
            raise TypeError("blob pack entry is not an object")
        if item.get("blob") != sha or item.get("kind") not in {"ts", "html"}:
            raise ValueError("blob pack entry does not match its blob id")
        kind = item["kind"]
        body = item["body"]
        indexed = _decode_ts(body) if kind == "ts" else _decode_html(body)
        loaded[sha] = (str(kind), indexed)
    return loaded


def _encode_ts(item: TsFileIndex) -> dict[str, object]:
    return {
        "component": _encode_component(item.component),
        "exports": list(item.exports),
        "imports": [_encode_import(entry) for entry in item.imports],
        "line_count": item.line_count,
        "ng_module": _encode_module(item.ng_module),
        "path": item.path,
        "references": [_encode_reference(entry) for entry in item.references],
        "status": item.status,
        "symbols": [_encode_symbol(entry) for entry in item.symbols],
        "warnings": [_encode_warning(entry) for entry in item.warnings],
    }


def _decode_ts(payload: object) -> TsFileIndex:
    if not isinstance(payload, dict):
        raise TypeError("typescript cache file is not an object")
    return TsFileIndex(
        _text(payload["path"]),
        _text(payload["status"]),
        _count(payload["line_count"]),
        tuple(_decode_import(item) for item in _list(payload["imports"])),
        tuple(_text(item) for item in _list(payload["exports"])),
        tuple(_decode_symbol(item) for item in _list(payload["symbols"])),
        _decode_component(payload["component"]),
        _decode_module(payload["ng_module"]),
        tuple(_decode_warning(item) for item in _list(payload["warnings"])),
        tuple(_decode_reference(item) for item in _list(payload["references"])),
    )


def _encode_html(item: HtmlFileIndex) -> dict[str, object]:
    return {
        "bindings": [_encode_binding(entry) for entry in item.bindings],
        "line_count": item.line_count,
        "path": item.path,
        "status": item.status,
        "warnings": [_encode_warning(entry) for entry in item.warnings],
    }


def _decode_html(payload: object) -> HtmlFileIndex:
    if not isinstance(payload, dict):
        raise TypeError("html cache file is not an object")
    return HtmlFileIndex(
        _text(payload["path"]),
        _text(payload["status"]),
        _count(payload["line_count"]),
        tuple(_decode_warning(item) for item in _list(payload["warnings"])),
        tuple(_decode_binding(item) for item in _list(payload.get("bindings", []))),
    )


def _encode_binding(item: HtmlBinding) -> dict[str, object]:
    return {
        "evidence": item.evidence,
        "kind": item.kind,
        "line": item.line,
        "name": item.name,
        "simple": item.simple,
    }


def _decode_binding(payload: object) -> HtmlBinding:
    if not isinstance(payload, dict):
        raise TypeError("html binding is not an object")
    return HtmlBinding(
        _text(payload["kind"]),
        _text(payload["name"]),
        _count(payload["line"]),
        _text(payload["evidence"]),
        payload["simple"] if isinstance(payload["simple"], bool) else False,
    )


def _encode_import(item: TsImport) -> dict[str, object]:
    return {
        "evidence": item.evidence,
        "form": item.form,
        "line": item.line,
        "module": item.module,
        "specifiers": list(item.specifiers),
    }


def _decode_import(payload: object) -> TsImport:
    if not isinstance(payload, dict):
        raise TypeError("import cache entry is not an object")
    module = payload["module"]
    if module is not None and not isinstance(module, str):
        raise TypeError("import module is not text")
    return TsImport(
        _count(payload["line"]),
        _text(payload["evidence"]),
        _text(payload["form"]),
        module,
        tuple(_text(item) for item in _list(payload["specifiers"])),
    )


def _encode_symbol(item: TsSymbol) -> dict[str, object]:
    return {
        "end_line": item.end_line,
        "evidence": item.evidence,
        "kind": item.kind,
        "qualname": item.qualname,
        "start_line": item.start_line,
    }


def _decode_symbol(payload: object) -> TsSymbol:
    if not isinstance(payload, dict):
        raise TypeError("symbol cache entry is not an object")
    return TsSymbol(
        _text(payload["qualname"]),
        _text(payload["kind"]),
        _count(payload["start_line"]),
        _count(payload["end_line"]),
        _text(payload["evidence"]),
    )


def _encode_component(item: ComponentMetadata) -> dict[str, object]:
    return {
        "evidence": item.evidence,
        "inline_template": item.inline_template,
        "selector": item.selector,
        "standalone": item.standalone,
        "status": item.status,
        "style_urls": list(item.style_urls),
        "template_url": item.template_url,
    }


def _decode_component(payload: object) -> ComponentMetadata:
    if not isinstance(payload, dict):
        raise TypeError("component cache entry is not an object")
    return ComponentMetadata(
        _text(payload["status"]),
        _optional_text(payload["selector"]),
        _optional_text(payload["template_url"]),
        tuple(_text(item) for item in _list(payload["style_urls"])),
        _optional_bool(payload["standalone"]),
        _text(payload["evidence"]),
        _optional_text(payload.get("inline_template")),
    )


def _encode_module(item: NgModuleMetadata) -> dict[str, object]:
    return {
        "declarations": list(item.declarations),
        "evidence": item.evidence,
        "imports": list(item.imports),
        "providers": list(item.providers),
        "status": item.status,
    }


def _decode_module(payload: object) -> NgModuleMetadata:
    if not isinstance(payload, dict):
        raise TypeError("ngmodule cache entry is not an object")
    return NgModuleMetadata(
        _text(payload["status"]),
        tuple(_text(item) for item in _list(payload["declarations"])),
        tuple(_text(item) for item in _list(payload["imports"])),
        _text(payload["evidence"]),
        tuple(_text(item) for item in _list(payload.get("providers", []))),
    )


def _encode_reference(item: TsReference) -> dict[str, object]:
    return {"evidence": item.evidence, "kind": item.kind, "line": item.line, "name": item.name}


def _decode_reference(payload: object) -> TsReference:
    if not isinstance(payload, dict):
        raise TypeError("reference cache entry is not an object")
    return TsReference(
        _text(payload["kind"]),
        _optional_text(payload["name"]),
        _count(payload["line"]),
        _text(payload["evidence"]),
    )


def _encode_warning(item: TsWarning) -> dict[str, object]:
    return {"code": item.code, "line": item.line, "message": item.message, "path": item.path}


def _decode_warning(payload: object) -> TsWarning:
    if not isinstance(payload, dict):
        raise TypeError("warning cache entry is not an object")
    line = payload["line"]
    if line is not None and not isinstance(line, int):
        raise TypeError("warning line is not an integer")
    return TsWarning(_text(payload["code"]), _text(payload["message"]), _optional_text(payload["path"]), line)


def _replace(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("cache field is not a list")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("cache field is not text")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError("cache field is not a boolean")
    return value


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("cache field is not an integer")
    return value
