"""Revision-aware Angular edges from the conservative TypeScript index.

Confirmed edges come from one resolved literal: a relative import, a safe
``tsconfig`` path alias, an explicit ``templateUrl``, an NgModule declaration
imported by name, or an ``ofType`` / ``on`` / ``inject`` argument that is itself
an imported binding. A name match across the commit is only a candidate.
External packages, selector text, and ``styleUrls`` are not edges.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass

from offline_review.git_adapter import GitAdapter
from offline_review.redaction import redact_secrets
from offline_review.html_bindings import HtmlBinding, scan_bindings
from offline_review.typescript_index import (
    TsCommitIndex,
    TsFileIndex,
    TsImport,
    TsWarning,
    index_typescript_commit,
)

ANALYSIS_METHOD = "conservative_lexical"
_MAX_JSON_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class PathAlias:
    key: str
    target: str | None
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class GraphNode:
    commit: str
    path: str
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    kind: str
    confidence: str
    evidence: str
    source_revision: str
    from_path: str
    to_path: str | None
    to_symbol: str | None
    line: int
    resolution_reason: str = ""


@dataclass(frozen=True, slots=True)
class AngularGraph:
    commit: str
    status: str
    analysis_method: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    warnings: tuple[TsWarning, ...]


@dataclass(frozen=True, slots=True)
class RevisionGraphs:
    base: AngularGraph
    head: AngularGraph


@dataclass(frozen=True, slots=True)
class WalkResult:
    paths: tuple[str, ...]
    cycles: tuple[tuple[str, ...], ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class _Resolution:
    confidence: str
    paths: tuple[str, ...]


def path_aliases(
    data: bytes | None,
    *,
    follow: object | None = None,
    origin: str = "tsconfig.json",
    _depth: int = 0,
    _seen: set[str] | None = None,
) -> tuple[tuple[PathAlias, ...], tuple[TsWarning, ...]]:
    """Read literal ``compilerOptions.paths`` from one ``tsconfig`` blob.

    Comments and trailing commas are accepted. A relative ``extends`` is
    followed only through ``follow``, at most three times, and only for a
    repository-relative ``.json`` path. Package names and parent-directory
    paths are not read. A key with several targets is ambiguous.
    """

    if data is None:
        return (), ()
    if b"\0" in data or len(data) > _MAX_JSON_BYTES:
        return (), (_warn("TSCONFIG_UNREADABLE", "tsconfig.json was not a small text file."),)
    try:
        payload = _loads_jsonc(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return (), (_warn("TSCONFIG_UNREADABLE", "tsconfig.json is not JSON or JSONC."),)
    if not isinstance(payload, dict):
        return (), (_warn("TSCONFIG_UNREADABLE", "tsconfig.json is not an object."),)
    warnings: list[TsWarning] = []
    seen = set() if _seen is None else _seen
    extends = payload.get("extends")
    if isinstance(extends, str):
        parent = _parent_config(origin, extends, follow, _depth, seen)
        if parent is None:
            warnings.append(_warn("TSCONFIG_EXTENDS_UNSUPPORTED", "tsconfig extends was not followed."))
        else:
            parent_payload, parent_warnings = parent
            warnings.extend(parent_warnings)
            payload = _merge_tsconfig(parent_payload, payload)
    elif "extends" in payload:
        warnings.append(_warn("TSCONFIG_EXTENDS_UNSUPPORTED", "tsconfig extends was not followed."))
    options = payload.get("compilerOptions")
    if options is None:
        return (), tuple(warnings)
    if not isinstance(options, dict):
        warnings.append(_warn("TSCONFIG_UNREADABLE", "compilerOptions is not an object."))
        return (), tuple(warnings)
    base = options.get("baseUrl", "")
    if not isinstance(base, str) or _unsafe_path(base):
        warnings.append(_warn("TSCONFIG_BASEURL_UNSUPPORTED", "baseUrl was ignored."))
        return (), tuple(warnings)
    raw_paths = options.get("paths")
    if raw_paths is None:
        return (), tuple(warnings)
    if not isinstance(raw_paths, dict):
        warnings.append(_warn("TSCONFIG_UNREADABLE", "compilerOptions.paths is not an object."))
        return (), tuple(warnings)
    aliases: list[PathAlias] = []
    for key in sorted(raw_paths):
        targets = raw_paths[key]
        if not isinstance(key, str) or not isinstance(targets, list) or not all(
            isinstance(item, str) for item in targets
        ):
            warnings.append(_warn("TSCONFIG_PATH_IGNORED", f"Path alias {key!r} was ignored."))
            continue
        if len(targets) != 1:
            aliases.append(PathAlias(key, None, True))
            warnings.append(
                _warn("TSCONFIG_PATH_AMBIGUOUS", f"Path alias {key!r} has more than one target.")
            )
            continue
        target = posixpath.normpath(posixpath.join(base, targets[0])) if base else posixpath.normpath(targets[0])
        if not _safe_alias(key, target):
            warnings.append(_warn("TSCONFIG_PATH_IGNORED", f"Path alias {key!r} was ignored."))
            continue
        aliases.append(PathAlias(key, target, False))
    return tuple(aliases), tuple(warnings)


def _loads_jsonc(text: str) -> object:
    return json.loads(_strip_jsonc(text))


def _strip_jsonc(text: str) -> str:
    kept: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    escape = False
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if in_string:
            kept.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            kept.append(char)
            index += 1
            continue
        if char == "/" and nxt == "/":
            index += 2
            while index < length and text[index] != "\n":
                index += 1
            continue
        if char == "/" and nxt == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                if text[index] == "\n":
                    kept.append("\n")
                index += 1
            index = min(length, index + 2)
            continue
        kept.append(char)
        index += 1
    return _strip_trailing_commas("".join(kept))


def _strip_trailing_commas(text: str) -> str:
    kept: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    escape = False
    while index < length:
        char = text[index]
        if in_string:
            kept.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            kept.append(char)
            index += 1
            continue
        if char == ",":
            cursor = index + 1
            while cursor < length and text[cursor] in " \t\r\n":
                cursor += 1
            if cursor < length and text[cursor] in "}]":
                index += 1
                continue
        kept.append(char)
        index += 1
    return "".join(kept)


def _safe_extends(spec: str) -> str | None:
    if not spec.startswith("./") or "\\" in spec or ":" in spec or spec.startswith("//"):
        return None
    normalized = posixpath.normpath(spec)
    if not normalized.endswith(".json") or _unsafe_path(normalized):
        return None
    return normalized


def _parent_config(
    origin: str,
    spec: str,
    follow: object | None,
    depth: int,
    seen: set[str],
) -> tuple[dict[str, object], tuple[TsWarning, ...]] | None:
    normalized = _resolve_extends(origin, spec)
    if follow is None or normalized is None or depth >= 3 or normalized in seen or not callable(follow):
        return None
    seen.add(normalized)
    try:
        data = follow(normalized)
    except (OSError, ValueError):
        return None
    if not isinstance(data, bytes) or b"\0" in data or len(data) > _MAX_JSON_BYTES:
        return None
    try:
        payload = _loads_jsonc(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    warnings: list[TsWarning] = []
    parent_extends = payload.get("extends")
    if isinstance(parent_extends, str):
        parent = _parent_config(normalized, parent_extends, follow, depth + 1, seen)
        if parent is None:
            warnings.append(_warn("TSCONFIG_EXTENDS_UNSUPPORTED", "tsconfig extends was not followed."))
        else:
            parent_payload, parent_warnings = parent
            warnings.extend(parent_warnings)
            payload = _merge_tsconfig(parent_payload, payload)
    return payload, tuple(warnings)


def _resolve_extends(origin: str, spec: str) -> str | None:
    relative = _safe_extends(spec)
    if relative is None or _unsafe_path(origin):
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(origin), relative))
    if not joined.endswith(".json") or _unsafe_path(joined):
        return None
    return joined


def _merge_tsconfig(parent: dict[str, object], child: dict[str, object]) -> dict[str, object]:
    merged = dict(parent)
    merged.update({key: value for key, value in child.items() if key != "compilerOptions"})
    parent_options = parent.get("compilerOptions")
    child_options = child.get("compilerOptions")
    options: dict[str, object] = dict(parent_options) if isinstance(parent_options, dict) else {}
    if isinstance(child_options, dict):
        options.update(child_options)
    if options:
        merged["compilerOptions"] = options
    merged.pop("extends", None)
    return merged


def build_angular_graph(index: TsCommitIndex, aliases: tuple[PathAlias, ...] = ()) -> AngularGraph:
    """Build edges for one commit index. The result is always partial."""

    files = {item.path: item for item in index.files}
    available = set(files) | {item.path for item in index.html_files}
    edges: list[GraphEdge] = []
    warnings = list(index.warnings)
    for path in sorted(files):
        item = files[path]
        bindings = _bindings(item)
        for imported in item.imports:
            resolution = _resolve_import(path, imported.module, available, aliases, html=False)
            _add_resolution(
                edges,
                warnings,
                kind="ts_import",
                resolution=resolution,
                commit=index.commit,
                from_path=path,
                symbol=None,
                line=imported.line,
                evidence=imported.evidence,
                unresolved_code="TS_IMPORT_UNRESOLVED",
                ambiguous_code="TS_IMPORT_AMBIGUOUS",
            )
            if resolution.confidence == "confirmed":
                for name in imported.specifiers:
                    if not name.endswith("Service"):
                        continue
                    _add_resolution(
                        edges,
                        warnings,
                        kind="service_reference",
                        resolution=resolution,
                        commit=index.commit,
                        from_path=path,
                        symbol=name,
                        line=imported.line,
                        evidence=imported.evidence,
                        unresolved_code="TS_IMPORT_UNRESOLVED",
                        ambiguous_code="TS_IMPORT_AMBIGUOUS",
                    )
        if item.component.status == "present" and item.component.template_url:
            resolution = _resolve_specifier(path, item.component.template_url, available, aliases, html=True)
            _add_resolution(
                edges,
                warnings,
                kind="component_template",
                resolution=resolution,
                commit=index.commit,
                from_path=path,
                symbol=None,
                line=0,
                evidence=item.component.evidence,
                unresolved_code="TS_TEMPLATE_UNRESOLVED",
                ambiguous_code="TS_TEMPLATE_AMBIGUOUS",
            )
        if item.ng_module.status == "present":
            _declarations(item, bindings, available, aliases, index.commit, files, edges, warnings)
        _references(item, bindings, available, aliases, index.commit, edges, warnings)
    _template_links(files, index, edges)
    unique = _dedupe(edges)
    nodes = _nodes(index, unique)
    warnings.sort(key=lambda item: (item.path or "", item.line or 0, item.code, item.message))
    return AngularGraph(
        index.commit,
        "partial",
        ANALYSIS_METHOD,
        nodes,
        unique,
        tuple(warnings),
    )


def build_revision_graphs(adapter: GitAdapter, base: str, head: str) -> RevisionGraphs:
    """Index both commits from Git blobs and build one graph for each.

    ``base`` and ``head`` must already be full object ids. The work tree is not read.
    """

    return RevisionGraphs(
        _graph_for_commit(adapter, base),
        _graph_for_commit(adapter, head),
    )


def edges_from(graph: AngularGraph, path: str) -> tuple[GraphEdge, ...]:
    return tuple(edge for edge in graph.edges if edge.from_path == path)


def edges_to(graph: AngularGraph, path: str) -> tuple[GraphEdge, ...]:
    return tuple(edge for edge in graph.edges if edge.to_path == path)


def removed_edges(base: AngularGraph, head: AngularGraph) -> tuple[GraphEdge, ...]:
    """Confirmed base edges whose kind, path, and symbol are absent on head."""

    head_keys = {
        (edge.kind, edge.from_path, edge.to_path, edge.to_symbol)
        for edge in head.edges
        if edge.confidence == "confirmed"
    }
    return tuple(
        edge
        for edge in base.edges
        if edge.confidence == "confirmed"
        and (edge.kind, edge.from_path, edge.to_path, edge.to_symbol) not in head_keys
    )


def walk_forward(graph: AngularGraph, start: str, *, max_depth: int = 8) -> WalkResult:
    """Follow confirmed edges. A repeated path is reported as a cycle and not entered again."""

    if max_depth < 0:
        raise ValueError("max_depth must be zero or greater")
    outgoing: dict[str, tuple[str, ...]] = {}
    for edge in graph.edges:
        if edge.confidence != "confirmed" or edge.to_path is None:
            continue
        current = outgoing.get(edge.from_path, ())
        if edge.to_path not in current:
            outgoing[edge.from_path] = tuple(sorted((*current, edge.to_path)))
    reached: list[str] = []
    cycles: list[tuple[str, ...]] = []
    truncated = False

    def visit(path: str, stack: tuple[str, ...], depth: int) -> None:
        nonlocal truncated
        if path in stack:
            cycles.append((*stack[stack.index(path) :], path))
            return
        if depth > max_depth:
            truncated = True
            return
        reached.append(path)
        for nxt in outgoing.get(path, ()):
            visit(nxt, (*stack, path), depth + 1)

    visit(start, (), 0)
    unique_cycles = tuple(sorted(set(cycles)))
    return WalkResult(tuple(dict.fromkeys(reached)), unique_cycles, truncated)


def _graph_for_commit(adapter: GitAdapter, commit: str) -> AngularGraph:
    index = index_typescript_commit(adapter, commit)
    config = adapter.read_bytes(commit, "tsconfig.json")
    aliases, warnings = path_aliases(config)
    graph = build_angular_graph(index, aliases)
    merged = tuple(sorted((*warnings, *graph.warnings), key=lambda item: (item.path or "", item.line or 0, item.code, item.message)))
    return AngularGraph(
        graph.commit,
        graph.status,
        graph.analysis_method,
        graph.nodes,
        graph.edges,
        merged,
    )


def _declarations(
    item: TsFileIndex,
    bindings: dict[str, TsImport | None],
    available: set[str],
    aliases: tuple[PathAlias, ...],
    commit: str,
    files: dict[str, TsFileIndex],
    edges: list[GraphEdge],
    warnings: list[TsWarning],
) -> None:
    for name in item.ng_module.declarations:
        binding = bindings.get(name)
        if binding is not None:
            resolution = _resolve_import(item.path, binding.module, available, aliases, html=False)
            _add_resolution(
                edges,
                warnings,
                kind="ngmodule_declaration",
                resolution=resolution,
                commit=commit,
                from_path=item.path,
                symbol=name,
                line=binding.line,
                evidence=item.ng_module.evidence or binding.evidence,
                unresolved_code="TS_NGMODULE_UNRESOLVED",
                ambiguous_code="TS_NGMODULE_AMBIGUOUS",
            )
            continue
        if name in bindings:
            warnings.append(
                _warn(
                    "TS_NGMODULE_AMBIGUOUS",
                    f"Declaration {name} is imported more than once.",
                    path=item.path,
                )
            )
            continue
        owners = [
            path
            for path, other in sorted(files.items())
            if any(symbol.kind == "class" and symbol.qualname == name for symbol in other.symbols)
        ]
        if len(owners) == 1:
            edges.append(
                GraphEdge(
                    "ngmodule_declaration",
                    "candidate",
                    item.ng_module.evidence,
                    commit,
                    item.path,
                    owners[0],
                    name,
                    0,
                )
            )
        elif len(owners) == 0:
            warnings.append(
                _warn(
                    "TS_NGMODULE_UNRESOLVED",
                    f"Declaration {name} is not an imported binding.",
                    path=item.path,
                )
            )
        else:
            warnings.append(
                _warn(
                    "TS_NGMODULE_AMBIGUOUS",
                    f"Declaration {name} matches more than one class.",
                    path=item.path,
                )
            )
    for name in item.ng_module.providers:
        binding = bindings.get(name)
        if binding is None or binding.module is None:
            warnings.append(
                _warn(
                    "TS_NGMODULE_UNRESOLVED",
                    f"Provider {name} is not a single imported binding.",
                    path=item.path,
                )
            )
            continue
        resolution = _resolve_import(item.path, binding.module, available, aliases, html=False)
        _add_resolution(
            edges,
            warnings,
            kind="service_reference",
            resolution=resolution,
            commit=commit,
            from_path=item.path,
            symbol=name,
            line=binding.line,
            evidence=item.ng_module.evidence or binding.evidence,
            unresolved_code="TS_NGMODULE_UNRESOLVED",
            ambiguous_code="TS_NGMODULE_AMBIGUOUS",
        )


def _template_links(
    files: dict[str, TsFileIndex], index: TsCommitIndex, edges: list[GraphEdge]
) -> None:
    pages = {item.path: item for item in index.html_files}
    selectors = _selector_index(files)
    for edge in list(edges):
        if edge.kind != "component_template" or edge.confidence != "confirmed" or edge.to_path is None:
            continue
        component = files.get(edge.from_path)
        page = pages.get(edge.to_path)
        if component is None or page is None:
            continue
        _bind_page(component, page.bindings, edge.to_path, index.commit, edges, selectors)
    for path in sorted(files):
        item = files[path]
        inline = item.component.inline_template
        if item.component.status == "present" and inline:
            _bind_page(item, scan_bindings(inline), path, index.commit, edges, selectors)


def _bind_page(
    component: TsFileIndex,
    bindings: tuple[HtmlBinding, ...],
    source: str,
    commit: str,
    edges: list[GraphEdge],
    selectors: dict[str, tuple[str, ...]],
) -> None:
    members = {
        symbol.qualname.rsplit(".", 1)[-1]
        for symbol in component.symbols
        if "." in symbol.qualname
    }
    for binding in bindings:
        if binding.kind == "selector":
            _selector_edge(binding, source, commit, edges, selectors, component.path)
            continue
        if binding.simple and binding.name in members:
            confidence, reason = "confirmed", ""
        elif binding.simple:
            confidence, reason = "unresolved", "target_not_found"
        else:
            confidence, reason = "candidate", "heuristic"
        edges.append(
            GraphEdge(
                "template_binding",
                confidence,
                binding.evidence,
                commit,
                source,
                component.path,
                binding.name or None,
                binding.line,
                reason,
            )
        )


def _selector_index(files: dict[str, TsFileIndex]) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for path, item in files.items():
        selector = item.component.selector
        if item.component.status == "present" and selector:
            found.setdefault(selector, []).append(path)
    return {name: tuple(sorted(paths)) for name, paths in found.items()}


def _selector_edge(
    binding: HtmlBinding,
    source: str,
    commit: str,
    edges: list[GraphEdge],
    selectors: dict[str, tuple[str, ...]],
    owner: str,
) -> None:
    paths = tuple(path for path in selectors.get(binding.name, ()) if path != owner)
    if len(paths) == 1:
        edges.append(
            GraphEdge(
                "component_selector",
                "candidate",
                binding.evidence,
                commit,
                source,
                paths[0],
                binding.name,
                binding.line,
                "heuristic",
            )
        )
    elif len(paths) > 1:
        edges.append(
            GraphEdge(
                "component_selector",
                "unresolved",
                binding.evidence,
                commit,
                source,
                None,
                binding.name,
                binding.line,
                "ambiguous_resolution",
            )
        )


def _references(
    item: TsFileIndex,
    bindings: dict[str, TsImport | None],
    available: set[str],
    aliases: tuple[PathAlias, ...],
    commit: str,
    edges: list[GraphEdge],
    warnings: list[TsWarning],
) -> None:
    for reference in item.references:
        if reference.kind not in {"of_type", "on_action", "inject", "dispatch_action", "select_selector"} or reference.name is None:
            continue
        if reference.kind == "inject":
            kind = "service_reference"
        elif reference.kind == "select_selector":
            kind = "ngrx_selector_reference"
        else:
            kind = "ngrx_action_reference"
        binding = bindings.get(reference.name)
        if binding is None and reference.name in bindings:
            edges.append(
                GraphEdge(
                    kind,
                    "ambiguous",
                    reference.evidence,
                    commit,
                    item.path,
                    None,
                    reference.name,
                    reference.line,
                    "ambiguous_resolution",
                )
            )
            warnings.append(
                _warn(
                    "TS_REFERENCE_AMBIGUOUS",
                    f"{reference.name} is imported more than once.",
                    path=item.path,
                    line=reference.line,
                )
            )
            continue
        if binding is None and reference.name not in bindings:
            edges.append(
                GraphEdge(
                    kind,
                    "unresolved",
                    reference.evidence,
                    commit,
                    item.path,
                    None,
                    reference.name,
                    reference.line,
                    "target_not_found",
                )
            )
            warnings.append(
                _warn(
                    "TS_REFERENCE_UNRESOLVED",
                    f"{reference.name} is not an imported binding.",
                    path=item.path,
                    line=reference.line,
                )
            )
            continue
        resolution = _resolve_import(item.path, binding.module, available, aliases, html=False)
        if reference.kind == "inject" and resolution.confidence == "confirmed":
            if not reference.name.endswith("Service") and not resolution.paths[0].endswith(".service.ts"):
                kind = "ts_import"
        _add_resolution(
            edges,
            warnings,
            kind=kind,
            resolution=resolution,
            commit=commit,
            from_path=item.path,
            symbol=reference.name,
            line=reference.line,
            evidence=reference.evidence,
            unresolved_code="TS_REFERENCE_UNRESOLVED",
            ambiguous_code="TS_REFERENCE_AMBIGUOUS",
        )


def _add_resolution(
    edges: list[GraphEdge],
    warnings: list[TsWarning],
    *,
    kind: str,
    resolution: _Resolution,
    commit: str,
    from_path: str,
    symbol: str | None,
    line: int,
    evidence: str,
    unresolved_code: str,
    ambiguous_code: str,
) -> None:
    if resolution.confidence == "external":
        return
    target = resolution.paths[0] if resolution.confidence == "confirmed" else None
    if resolution.confidence == "unresolved":
        reason = "target_not_found"
        warnings.append(_warn(unresolved_code, "The specifier did not match one file.", path=from_path, line=line))
    elif resolution.confidence == "ambiguous":
        reason = "ambiguous_resolution"
        listed = ", ".join(resolution.paths) or "more than one target"
        warnings.append(
            _warn(
                ambiguous_code,
                f"The specifier matches more than one file: {listed}.",
                path=from_path,
                line=line,
            )
        )
    else:
        reason = ""
    edges.append(
        GraphEdge(
            kind,
            resolution.confidence,
            evidence,
            commit,
            from_path,
            target,
            symbol,
            line,
            reason,
        )
    )


def _resolve_import(
    from_path: str,
    module: str | None,
    available: set[str],
    aliases: tuple[PathAlias, ...],
    *,
    html: bool,
) -> _Resolution:
    if module is None:
        return _Resolution("unresolved", ())
    if module.startswith("./") or module.startswith("../"):
        return _resolve_specifier(from_path, module, available, aliases, html=html)
    rewritten = _apply_aliases(module, aliases)
    if rewritten.confidence == "external":
        return rewritten
    if rewritten.confidence != "rewritten":
        return rewritten
    return _candidates(rewritten.paths[0], available, html=html)


def _resolve_specifier(
    from_path: str,
    specifier: str,
    available: set[str],
    aliases: tuple[PathAlias, ...],
    *,
    html: bool,
) -> _Resolution:
    if specifier.startswith("./") or specifier.startswith("../"):
        base = posixpath.dirname(from_path)
        joined = posixpath.normpath(posixpath.join(base, specifier))
        if _unsafe_path(joined):
            return _Resolution("unresolved", ())
        return _candidates(joined, available, html=html)
    rewritten = _apply_aliases(specifier, aliases)
    if rewritten.confidence == "external":
        return _Resolution("unresolved", ())
    if rewritten.confidence != "rewritten":
        return rewritten
    if _unsafe_path(rewritten.paths[0]):
        return _Resolution("unresolved", ())
    return _candidates(rewritten.paths[0], available, html=html)


def _apply_aliases(specifier: str, aliases: tuple[PathAlias, ...]) -> _Resolution:
    matches: list[_Resolution] = []
    for alias in aliases:
        if not _alias_matches(alias.key, specifier):
            continue
        if alias.ambiguous or alias.target is None:
            matches.append(_Resolution("ambiguous", ()))
            continue
        rewritten = _rewrite(alias.key, alias.target, specifier)
        if rewritten is None or _unsafe_path(rewritten):
            matches.append(_Resolution("unresolved", ()))
            continue
        matches.append(_Resolution("rewritten", (rewritten,)))
    if not matches:
        return _Resolution("external", ())
    if len(matches) > 1 or matches[0].confidence != "rewritten":
        confidence = "ambiguous" if any(item.confidence == "ambiguous" for item in matches) else matches[0].confidence
        return _Resolution(confidence, ())
    return matches[0]


def _alias_matches(key: str, specifier: str) -> bool:
    if "*" not in key:
        return specifier == key
    prefix, suffix = key.split("*", 1)
    return specifier.startswith(prefix) and specifier.endswith(suffix) and len(specifier) >= len(prefix) + len(suffix)


def _rewrite(key: str, target: str, specifier: str) -> str | None:
    if "*" not in key:
        return target
    prefix, suffix = key.split("*", 1)
    middle = specifier[len(prefix) : len(specifier) - len(suffix) if suffix else None]
    target_prefix, target_suffix = target.split("*", 1)
    return posixpath.normpath(target_prefix + middle + target_suffix)


def _candidates(path: str, available: set[str], *, html: bool) -> _Resolution:
    path = posixpath.normpath(path)
    if html:
        options = (path,) if path.endswith(".html") else (path + ".html",)
    elif path.endswith((".ts", ".tsx", ".html")):
        options = (path,)
    elif path.endswith(".js"):
        stem = path[:-3]
        options = (stem + ".ts", stem + ".tsx")
    else:
        options = (path + ".ts", path + ".tsx", path + "/index.ts", path + "/index.tsx")
    found = tuple(item for item in options if item in available)
    if len(found) == 1:
        return _Resolution("confirmed", found)
    if len(found) > 1:
        return _Resolution("ambiguous", found)
    return _Resolution("unresolved", ())


def _bindings(item: TsFileIndex) -> dict[str, TsImport | None]:
    bindings: dict[str, TsImport | None] = {}
    for imported in item.imports:
        for name in imported.specifiers:
            if name in bindings:
                bindings[name] = None
            else:
                bindings[name] = imported
    return bindings


def _nodes(index: TsCommitIndex, edges: tuple[GraphEdge, ...]) -> tuple[GraphNode, ...]:
    nodes = {
        (item.path, None)
        for item in (*index.files, *index.html_files)
    }
    for edge in edges:
        nodes.add((edge.from_path, None))
        if edge.to_path is not None:
            nodes.add((edge.to_path, edge.to_symbol))
    return tuple(
        GraphNode(index.commit, path, symbol)
        for path, symbol in sorted(nodes, key=lambda item: (item[0], item[1] or ""))
    )


def _dedupe(edges: list[GraphEdge]) -> tuple[GraphEdge, ...]:
    chosen: dict[tuple[str, str, str, str | None, str | None], GraphEdge] = {}
    for edge in edges:
        key = (edge.kind, edge.confidence, edge.from_path, edge.to_path, edge.to_symbol)
        current = chosen.get(key)
        if current is None or (edge.line or 10**9) < (current.line or 10**9):
            chosen[key] = edge
    return tuple(sorted(chosen.values(), key=lambda item: (item.kind, item.from_path, item.to_path or "", item.to_symbol or "", item.confidence, item.line)))


def _safe_alias(key: str, target: str) -> bool:
    if key.count("*") > 1 or target.count("*") > 1:
        return False
    if ("*" in key) != ("*" in target):
        return False
    return not _unsafe_path(key.replace("*", "")) and not _unsafe_path(target.replace("*", "x"))


def _unsafe_path(path: str) -> bool:
    if path.startswith(("/", "\\")) or "\\" in path or ":" in path:
        return True
    normalized = posixpath.normpath(path)
    return normalized == ".." or normalized.startswith("../")


def _warn(code: str, message: str, path: str | None = None, line: int | None = None) -> TsWarning:
    return TsWarning(code, redact_secrets(message), path, line)
