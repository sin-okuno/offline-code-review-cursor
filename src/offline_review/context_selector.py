"""Choose bounded TypeScript and HTML excerpts for the review report.

The selector reads Git blobs for the diff base and head. It does not read the
work tree. Excerpts are partial lexical context, not a TypeScript analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from offline_review.dependency_graph import (
    AngularGraph,
    GraphEdge,
    build_angular_graph,
    edges_from,
    edges_to,
    path_aliases,
)
from offline_review.diff_parser import coalesce_lines
from offline_review.git_adapter import GitAdapter
from offline_review.models import (
    ANGULAR_CONTEXT_SCHEMA,
    AngularContext,
    AngularCoverage,
    AngularEdge,
    AngularNode,
    AngularOmission,
    AngularSelection,
    AngularSymbol,
    AngularUnresolved,
    ChangedLineStat,
    FileChange,
    ReviewUnit,
    Warning,
    empty_angular_context,
)
from offline_review.redaction import redact_secrets
from offline_review.typescript_cache import TypeScriptCache
from offline_review.typescript_index import (
    TsCommitIndex,
    TsFileIndex,
    TsSymbol,
    TsWarning,
    index_typescript_commit,
    is_html_path,
    is_typescript_path,
)

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_FILES = 40
DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_MAX_LINES = 200
DEFAULT_MAX_TOTAL_LINES = 2000


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_depth: int = DEFAULT_MAX_DEPTH
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    max_lines: int = DEFAULT_MAX_LINES
    max_total_lines: int = DEFAULT_MAX_TOTAL_LINES


@dataclass(frozen=True, slots=True)
class _Pick:
    priority: int
    depth: int
    revision: str
    path: str
    reason: str
    confidence: str
    lines: tuple[int, ...] | None
    symbol: str | None
    score: int = 0


def select_angular_context(
    adapter: GitAdapter,
    files: tuple[FileChange, ...],
    *,
    diff_base: str,
    head: str,
    budget: ContextBudget,
    cache_directory: Path | None = None,
) -> AngularContext:
    """Select excerpts for changed TypeScript and HTML files and nearby edges."""

    _validate_budget(budget)
    if not any(_source_path(change.old_path) or _source_path(change.new_path) for change in files):
        return empty_angular_context()
    ts_cache = TypeScriptCache(cache_directory / "typescript") if cache_directory is not None else None
    base_index = index_typescript_commit(adapter, diff_base, ts_cache)
    head_index = index_typescript_commit(adapter, head, ts_cache)
    base_aliases, base_notes = path_aliases(
        adapter.read_bytes(diff_base, "tsconfig.json"),
        follow=_tsconfig_reader(adapter, diff_base),
    )
    head_aliases, head_notes = path_aliases(
        adapter.read_bytes(head, "tsconfig.json"),
        follow=_tsconfig_reader(adapter, head),
    )
    graphs = {
        "base": build_angular_graph(base_index, base_aliases),
        "head": build_angular_graph(head_index, head_aliases),
    }
    indexes = {"base": base_index, "head": head_index}
    commits = {"base": diff_base, "head": head}
    picks = _ranked(files, graphs, budget.max_depth)
    picks, carried_symbols, match_warnings = _carry_deleted_symbols(files, picks, indexes, commits)
    kept, omitted, truncated = _limit_files(picks, commits, budget.max_files)
    selected: list[AngularSelection] = []
    overflow: list[AngularSelection] = []
    remaining = budget.max_bytes
    lines_used = 0
    lines_omitted = 0
    for pick in kept:
        excerpt, remaining, extra_omitted, clipped = _excerpt(
            adapter, indexes[pick.revision], pick, commits[pick.revision], budget.max_lines, remaining
        )
        omitted.extend(extra_omitted)
        accepted, dropped_lines, remainder = _fit_total_lines(excerpt, pick, lines_used, budget.max_total_lines)
        selected.extend(accepted)
        overflow.extend(remainder)
        lines_used += sum(_content_lines(item.content) for item in accepted)
        if dropped_lines:
            lines_omitted += dropped_lines
            omitted.append(
                AngularOmission(
                    pick.revision,
                    commits[pick.revision],
                    pick.path,
                    "max_total_lines",
                    dropped_lines,
                    "budget_exceeded",
                )
            )
        truncated = truncated or clipped or bool(extra_omitted) or bool(dropped_lines)
    relevant = {item.path for item in (*kept, *omitted)}
    notes = (
        *base_notes,
        *head_notes,
        *_index_warnings(base_index),
        *_index_warnings(head_index),
        *graphs["base"].warnings,
        *graphs["head"].warnings,
    )
    unique_notes = {
        item for item in notes if item.path is None or item.path in relevant
    }
    warnings = tuple(
        sorted(
            (
                *(Warning(item.code, item.message, item.path) for item in unique_notes),
                *_budget_warnings(omitted),
                *match_warnings,
            ),
            key=lambda item: (item.code, item.path or "", item.message),
        )
    )
    omitted_items = tuple(sorted(omitted, key=lambda item: (item.reason, item.revision, item.path, item.commit)))
    review_units = _review_units(selected, overflow, budget.max_total_lines)
    changed_line_files = _changed_line_stats(files, review_units)
    html = _scanned_templates(base_index, head_index)
    ngrx = any(edge.kind.startswith("ngrx_") for graph in graphs.values() for edge in graph.edges)
    return AngularContext(
        schema=ANGULAR_CONTEXT_SCHEMA,
        status="partial",
        analysis_method="conservative_lexical",
        nodes=_nodes(graphs, relevant),
        edges=_edges(graphs, relevant),
        selected=tuple(selected),
        omitted=omitted_items,
        warnings=warnings,
        coverage=AngularCoverage(
            "partial",
            "partial" if html else "unsupported",
            truncated or bool(omitted_items),
            "partial" if ngrx else "not_applicable",
            diff_base,
            head,
            budget.max_total_lines,
            lines_used,
            lines_omitted,
        ),
        changed_symbols=_merge_symbols(_changed_symbols(files, indexes, commits), carried_symbols),
        unresolved=_unresolved(graphs, relevant),
        review_units=review_units,
        changed_line_files=changed_line_files,
    )


def _validate_budget(budget: ContextBudget) -> None:
    for name, value in (
        ("max_depth", budget.max_depth),
        ("max_files", budget.max_files),
        ("max_bytes", budget.max_bytes),
        ("max_lines", budget.max_lines),
        ("max_total_lines", budget.max_total_lines),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")


def _source_path(path: str | None) -> bool:
    return path is not None and (is_typescript_path(path) or is_html_path(path))


def _ranked(files: tuple[FileChange, ...], graphs: dict[str, AngularGraph], max_depth: int) -> list[_Pick]:
    changed = _changed(files)
    picks = list(changed)
    seen = {(item.revision, item.path) for item in changed}
    if max_depth == 0:
        return picks
    frontier = [(item.revision, item.path) for item in changed]
    reached_forward: list[tuple[str, str]] = []
    for depth in range(1, max_depth + 1):
        nxt: list[tuple[str, str]] = []
        for revision, path in frontier:
            for edge in edges_from(graphs[revision], path):
                if edge.confidence != "confirmed" or edge.to_path is None:
                    continue
                key = (revision, edge.to_path)
                if key in seen:
                    continue
                seen.add(key)
                picks.append(
                    _Pick(
                        1,
                        depth,
                        revision,
                        edge.to_path,
                        "confirmed direct dependency",
                        "confirmed",
                        None,
                        edge.to_symbol,
                        _dependency_score(edge.kind, depth, edge.to_path),
                    )
                )
                nxt.append(key)
                reached_forward.append(key)
        frontier = nxt
    frontier = [(item.revision, item.path) for item in changed] + reached_forward
    for depth in range(1, max_depth + 1):
        nxt = []
        for revision, path in frontier:
            for edge in edges_to(graphs[revision], path):
                if edge.confidence != "confirmed":
                    continue
                key = (revision, edge.from_path)
                if key in seen:
                    continue
                seen.add(key)
                picks.append(
                    _Pick(
                        2,
                        depth,
                        revision,
                        edge.from_path,
                        "confirmed reverse dependency",
                        "confirmed",
                        None,
                        None,
                        60 if depth == 1 else 50,
                    )
                )
                nxt.append(key)
        frontier = nxt
    for revision, graph in graphs.items():
        changed_paths = {item.path for item in changed if item.revision == revision}
        for edge in graph.edges:
            if edge.confidence != "candidate":
                continue
            target = _candidate_target(edge, changed_paths, seen, revision)
            if target is None:
                continue
            seen.add((revision, target))
            picks.append(_Pick(3, 1, revision, target, "candidate dependency", "candidate", None, edge.to_symbol, 30))
    return _order_picks(picks)


def _changed(files: tuple[FileChange, ...]) -> list[_Pick]:
    picks: list[_Pick] = []
    for change in files:
        if change.old_path and _source_path(change.old_path) and (change.status_code == "D" or change.old_changed_lines):
            picks.append(
                _Pick(0, 0, "base", change.old_path, "changed file", "confirmed", change.old_changed_lines, None, 100)
            )
        if change.new_path and _source_path(change.new_path) and change.status_code != "D":
            picks.append(
                _Pick(0, 0, "head", change.new_path, "changed file", "confirmed", change.new_changed_lines, None, 100)
            )
    picks.sort(key=lambda item: (item.path, item.revision))
    return picks


def _order_picks(picks: list[_Pick]) -> list[_Pick]:
    """Keep both revisions of a change and one revision of an unchanged dependency."""

    ranked = sorted(picks, key=lambda item: (item.priority, item.path, 0 if item.revision == "head" else 1))
    seen: set[str] = set()
    kept: list[_Pick] = []
    for pick in ranked:
        if pick.priority == 0:
            kept.append(pick)
            continue
        if pick.path in seen:
            continue
        seen.add(pick.path)
        kept.append(pick)
    kept.sort(key=lambda item: (item.priority, -item.score, item.depth, item.path, item.revision))
    return kept


def _dependency_score(kind: str, depth: int, path: str) -> int:
    if path.endswith(".spec.ts"):
        return 70
    if depth > 1:
        return 50
    return {
        "template_binding": 90,
        "ngrx_action_reference": 85,
        "ngrx_selector_reference": 80,
        "service_reference": 80,
        "component_template": 75,
        "ts_import": 70,
        "ngmodule_declaration": 20,
        "component_selector": 40,
    }.get(kind, 60)


def _candidate_target(
    edge: GraphEdge, changed_paths: set[str], seen: set[tuple[str, str]], revision: str
) -> str | None:
    if edge.from_path in changed_paths and edge.to_path and (revision, edge.to_path) not in seen:
        return edge.to_path
    if edge.to_path in changed_paths and (revision, edge.from_path) not in seen:
        return edge.from_path
    return None


def _limit_files(
    picks: list[_Pick], commits: dict[str, str], max_files: int
) -> tuple[list[_Pick], list[AngularOmission], bool]:
    kept: list[_Pick] = []
    omitted: list[AngularOmission] = []
    seen: set[tuple[str, str]] = set()
    for pick in picks:
        key = (pick.revision, pick.path)
        if key in seen:
            continue
        seen.add(key)
        if len(kept) >= max_files:
            missing = len(pick.lines or ())
            omitted.append(
                AngularOmission(
                    pick.revision,
                    commits[pick.revision],
                    pick.path,
                    "max_files",
                    missing,
                    "budget_exceeded" if pick.reason == "changed file" else "",
                )
            )
            continue
        kept.append(pick)
    return kept, omitted, bool(omitted)


def _index_warnings(index: TsCommitIndex) -> tuple[TsWarning, ...]:
    return (
        *index.warnings,
        *(note for item in index.files for note in item.warnings),
        *(note for item in index.html_files for note in item.warnings),
    )


def _excerpt(
    adapter: GitAdapter,
    index: TsCommitIndex,
    pick: _Pick,
    commit: str,
    max_lines: int,
    remaining: int,
) -> tuple[list[AngularSelection], int, list[AngularOmission], bool]:
    data = adapter.read_bytes(commit, pick.path)
    if data is None:
        return [], remaining, [AngularOmission(pick.revision, commit, pick.path, "missing_blob")], True
    if b"\0" in data:
        return [], remaining, [AngularOmission(pick.revision, commit, pick.path, "binary")], True
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], remaining, [AngularOmission(pick.revision, commit, pick.path, "decode")], True
    selected: list[AngularSelection] = []
    omitted: list[AngularOmission] = []
    clipped = False
    for start, end in _ranges(pick, index, max_lines):
        content, start, end, line_clipped = _slice(text, start, end, max_lines)
        content = redact_secrets(content)
        raw = content.encode("utf-8")
        if content and len(raw) > remaining:
            if remaining <= 0:
                omitted.append(AngularOmission(pick.revision, commit, pick.path, "max_bytes"))
                clipped = True
                continue
            content = raw[:remaining].decode("utf-8", "ignore")
            end = start + content.count("\n") if content else 0
            remaining = 0
            clipped = True
            omitted.append(AngularOmission(pick.revision, commit, pick.path, "max_bytes"))
        elif content:
            remaining -= len(raw)
        if line_clipped:
            clipped = True
            omitted.append(AngularOmission(pick.revision, commit, pick.path, "max_lines"))
        selected.append(
            AngularSelection(
                pick.revision,
                commit,
                pick.path,
                start,
                end,
                pick.reason,
                pick.confidence,
                content,
                pick.score,
            )
        )
    return selected, remaining, omitted, clipped


def _ranges(pick: _Pick, index: TsCommitIndex, max_lines: int) -> list[tuple[int, int]]:
    if pick.lines is not None:
        ranges = [(item.start, item.end) for item in coalesce_lines(pick.lines)]
        if not ranges:
            return [(0, 0)]
        widened = [_widen_range(start, end, _file_symbols(index, pick.path), max_lines) for start, end in ranges]
        return _bridge_ranges(_merge_ranges(widened), max_lines)
    symbol = _symbol(index, pick.path, pick.symbol)
    if symbol is not None:
        return [(symbol.start_line, symbol.end_line)]
    if max_lines == 0:
        return [(1, 1)]
    return [(1, max_lines)]


def _symbol(index: TsCommitIndex, path: str, name: str | None) -> TsSymbol | None:
    if not name:
        return None
    for item in index.files:
        if item.path != path:
            continue
        for symbol in item.symbols:
            if symbol.qualname == name or symbol.qualname.endswith("." + name):
                return symbol
    return None


def _file_symbols(index: TsCommitIndex, path: str) -> tuple[TsSymbol, ...]:
    for item in index.files:
        if item.path == path:
            return item.symbols
    return ()


def _widen_range(
    start: int, end: int, symbols: tuple[TsSymbol, ...], max_lines: int
) -> tuple[int, int]:
    """Cover the smallest symbol that contains the change and still fits the line cap."""

    if max_lines <= 0:
        return start, end
    fitting = [
        symbol
        for symbol in symbols
        if symbol.start_line <= end and symbol.end_line >= start and symbol.end_line - symbol.start_line + 1 <= max_lines
    ]
    if not fitting:
        return start, end
    symbol = min(fitting, key=lambda item: (item.end_line - item.start_line, item.start_line, item.qualname))
    symbol = _same_file_owner(symbol, symbols, max_lines)
    widened = (min(start, symbol.start_line), max(end, symbol.end_line))
    if widened[1] - widened[0] + 1 <= max_lines:
        return widened
    return start, end


def _same_file_owner(symbol: TsSymbol, symbols: tuple[TsSymbol, ...], max_lines: int) -> TsSymbol:
    """Keep a small enclosing class so constructor evidence stays with a method change."""

    if "." not in symbol.qualname:
        return symbol
    owner = symbol.qualname.rsplit(".", 1)[0]
    for item in symbols:
        span = item.end_line - item.start_line + 1
        if (
            item.kind == "class"
            and item.qualname == owner
            and item.start_line <= symbol.start_line
            and item.end_line >= symbol.end_line
            and span <= max_lines
        ):
            return item
    return symbol


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _bridge_ranges(ranges: list[tuple[int, int]], max_lines: int) -> list[tuple[int, int]]:
    """Keep up to two unchanged lines that sit between changed ranges in the same file."""

    if max_lines <= 0 or len(ranges) < 2:
        return ranges
    bridged = [ranges[0]]
    for start, end in ranges[1:]:
        previous_start, previous_end = bridged[-1]
        gap = start - previous_end - 1
        if 0 < gap <= 2 and end - previous_start + 1 <= max_lines:
            bridged[-1] = (previous_start, end)
            continue
        bridged.append((start, end))
    return bridged


def _review_units(
    selected: list[AngularSelection], overflow: list[AngularSelection], budget: int
) -> tuple[ReviewUnit, ...]:
    units = [ReviewUnit("unit-1", tuple(selected), sum(_content_lines(item.content) for item in selected))]
    if budget <= 0 or not overflow:
        return tuple(units)
    bucket: list[AngularSelection] = []
    count = 0
    number = 2
    for item in overflow:
        size = _content_lines(item.content)
        if bucket and count + size > budget:
            units.append(ReviewUnit(f"unit-{number}", tuple(bucket), count))
            number += 1
            bucket = []
            count = 0
        bucket.append(item)
        count += size
    if bucket:
        units.append(ReviewUnit(f"unit-{number}", tuple(bucket), count))
    return tuple(units)


def _changed_line_stats(
    files: tuple[FileChange, ...], units: tuple[ReviewUnit, ...]
) -> tuple[ChangedLineStat, ...]:
    covered: dict[tuple[str, str], set[int]] = {}
    for unit in units:
        for item in unit.excerpts:
            if item.line_start <= 0 or item.line_end < item.line_start:
                continue
            covered.setdefault((item.revision, item.path), set()).update(range(item.line_start, item.line_end + 1))
    stats: list[ChangedLineStat] = []
    for change in files:
        pairs = (("base", change.old_path, change.old_changed_lines), ("head", change.new_path, change.new_changed_lines))
        for revision, path, lines in pairs:
            if path is None or not _source_path(path):
                continue
            needed = {line for line in lines if line > 0}
            if not needed:
                continue
            found = needed & covered.get((revision, path), set())
            stats.append(ChangedLineStat(revision, path, len(needed), len(found), len(needed - found)))
    stats.sort(key=lambda item: (item.path, item.revision))
    return tuple(stats)


def _tsconfig_reader(adapter: GitAdapter, commit: str):
    def read(path: str) -> bytes | None:
        if not path.endswith(".json") or path.startswith(("/", "\\")) or ".." in path.split("/"):
            return None
        return adapter.read_bytes(commit, path)

    return read


def _slice(text: str, start: int, end: int, max_lines: int) -> tuple[str, int, int, bool]:
    if start <= 0 or end <= 0:
        return "", 0, 0, False
    if max_lines <= 0:
        return "", 0, 0, True
    body = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    if not body:
        return "", 0, 0, False
    start = min(max(start, 1), len(body))
    end = min(max(end, start), len(body))
    clipped = False
    if end - start + 1 > max_lines:
        end = start + max_lines - 1
        clipped = True
    return "\n".join(body[start - 1 : end]), start, end, clipped


def _nodes(graphs: dict[str, AngularGraph], relevant: set[str]) -> tuple[AngularNode, ...]:
    nodes = {
        (node.commit, node.path, node.symbol)
        for graph in graphs.values()
        for node in graph.nodes
        if node.path in relevant
    }
    return tuple(AngularNode(commit, path, symbol) for commit, path, symbol in sorted(nodes, key=lambda item: (item[1], item[0], item[2] or "")))


def _edges(graphs: dict[str, AngularGraph], relevant: set[str]) -> tuple[AngularEdge, ...]:
    edges = [
        AngularEdge(
            edge.kind,
            edge.confidence,
            redact_secrets(edge.evidence),
            edge.source_revision,
            edge.from_path,
            edge.to_path,
            edge.to_symbol,
            edge.line,
            edge.resolution_reason,
        )
        for graph in graphs.values()
        for edge in graph.edges
        if edge.from_path in relevant or (edge.to_path is not None and edge.to_path in relevant)
    ]
    edges.sort(key=lambda item: (item.kind, item.from_path, item.to_path or "", item.to_symbol or "", item.confidence, item.source_revision, item.line))
    return tuple(edges)


def _fit_total_lines(
    excerpt: list[AngularSelection], pick: _Pick, used: int, budget: int
) -> tuple[list[AngularSelection], int, list[AngularSelection]]:
    if budget <= 0:
        dropped = sum(_content_lines(item.content) for item in excerpt)
        return [], dropped, []
    accepted: list[AngularSelection] = []
    remainders: list[AngularSelection] = []
    dropped = 0
    running = used
    for item in excerpt:
        count = _content_lines(item.content)
        room = budget - running
        if count <= room:
            accepted.append(item)
            running += count
            continue
        if pick.reason == "changed file" and room > 0 and item.content:
            pieces = item.content.split("\n")
            kept = "\n".join(pieces[:room])
            rest = "\n".join(pieces[room:])
            accepted.append(
                AngularSelection(
                    item.revision,
                    item.commit,
                    item.path,
                    item.line_start,
                    item.line_start + room - 1,
                    item.reason,
                    item.confidence,
                    kept,
                    item.score,
                )
            )
            if rest:
                remainders.append(
                    AngularSelection(
                        item.revision,
                        item.commit,
                        item.path,
                        item.line_start + room,
                        item.line_end,
                        item.reason,
                        item.confidence,
                        rest,
                        item.score,
                    )
                )
            dropped += count - room
            running = budget
            continue
        if pick.reason == "changed file":
            remainders.append(item)
        dropped += count
    return accepted, dropped, remainders


def _content_lines(content: str) -> int:
    if not content:
        return 0
    return content.count("\n") + 1


def _budget_warnings(omitted: list[AngularOmission]) -> tuple[Warning, ...]:
    return tuple(
        Warning(
            "CHANGED_CONTEXT_OMITTED",
            (
                f"{item.path} has {item.missing_lines} lines outside the first review unit "
                f"because {item.reason} was reached. Changed-file overflow is placed in a later unit."
            ),
            item.path,
        )
        for item in omitted
        if item.resolution_reason == "budget_exceeded" and item.reason in {"max_files", "max_total_lines"}
    )


def _scanned_templates(base: TsCommitIndex, head: TsCommitIndex) -> bool:
    if base.html_files or head.html_files:
        return True
    return any(item.component.inline_template for item in (*base.files, *head.files))


def _carry_deleted_symbols(
    files: tuple[FileChange, ...],
    picks: list[_Pick],
    indexes: dict[str, TsCommitIndex],
    commits: dict[str, str],
) -> tuple[list[_Pick], tuple[AngularSymbol, ...], tuple[Warning, ...]]:
    """Fill a head excerpt when a deletion leaves the declaration in place.

    A pure deletion has line numbers only on the base side. The head file is
    matched by path and by symbol qualname plus kind, not by the old line
    number. A missing, ambiguous, or unscanned declaration is not invented.
    """

    updated = list(picks)
    carried: list[AngularSymbol] = []
    warnings: list[Warning] = []
    for change in files:
        if not change.old_path or not is_typescript_path(change.old_path):
            continue
        base_file = _indexed_file(indexes["base"], change.old_path)
        if change.status_code == "D":
            warnings.extend(_absent_file_warnings(change, base_file))
            continue
        if change.new_changed_lines or not change.new_path or not is_typescript_path(change.new_path):
            continue
        if not change.old_changed_lines:
            continue
        head_file = _indexed_file(indexes["head"], change.new_path)
        if base_file is None or head_file is None or base_file.status != "parsed" or head_file.status != "parsed":
            status = "missing" if head_file is None else head_file.status
            warnings.append(
                Warning(
                    "TS_SYMBOL_MATCH_UNCERTAIN",
                    (
                        f"A deleted line in {change.new_path} was not matched to a head declaration "
                        f"because the TypeScript scan is {status}."
                    ),
                    change.new_path,
                )
            )
            continue
        covering = _smallest_covering(base_file.symbols, change.old_changed_lines)
        if not covering:
            continue
        lines: list[int] = []
        matched = False
        for symbol in covering:
            found = tuple(
                item
                for item in head_file.symbols
                if item.qualname == symbol.qualname and item.kind == symbol.kind
            )
            if len(found) == 1:
                matched = True
                head_symbol = found[0]
                lines.extend(range(head_symbol.start_line, head_symbol.end_line + 1))
                carried.append(
                    AngularSymbol(
                        "head",
                        commits["head"],
                        change.new_path,
                        head_symbol.qualname,
                        head_symbol.kind,
                        head_symbol.start_line,
                        head_symbol.end_line,
                    )
                )
                continue
            if len(found) > 1:
                warnings.append(
                    Warning(
                        "TS_SYMBOL_MATCH_AMBIGUOUS",
                        (
                            f"{symbol.qualname} ({symbol.kind}) has {len(found)} head declarations. "
                            "The head excerpt was not matched."
                        ),
                        change.new_path,
                    )
                )
                continue
            warnings.append(
                Warning(
                    "TS_SYMBOL_ABSENT",
                    (
                        f"{symbol.qualname} ({symbol.kind}) is not on the head revision. "
                        "No head excerpt was created for it."
                    ),
                    change.new_path,
                )
            )
        if matched:
            updated = _replace_empty_head(updated, change.new_path, tuple(sorted(set(lines))))
        else:
            updated = _drop_empty_head(updated, change.new_path)
    return updated, tuple(carried), tuple(warnings)


def _absent_file_warnings(change: FileChange, base_file: TsFileIndex | None) -> tuple[Warning, ...]:
    path = change.old_path or ""
    if base_file is None or base_file.status != "parsed":
        status = "missing" if base_file is None else base_file.status
        return (
            Warning(
                "TS_SYMBOL_MATCH_UNCERTAIN",
                f"Deleted TypeScript file {path} was not scanned ({status}), so removed declarations were not named.",
                path,
            ),
        )
    notes: list[Warning] = []
    for symbol in _smallest_covering(base_file.symbols, change.old_changed_lines):
        notes.append(
            Warning(
                "TS_SYMBOL_ABSENT",
                (
                    f"{symbol.qualname} ({symbol.kind}) is not on the head revision. "
                    "No head excerpt was created for it."
                ),
                path,
            )
        )
    return tuple(notes)


def _indexed_file(index: TsCommitIndex, path: str) -> TsFileIndex | None:
    for item in index.files:
        if item.path == path:
            return item
    return None


def _smallest_covering(symbols: tuple[TsSymbol, ...], lines: tuple[int, ...]) -> tuple[TsSymbol, ...]:
    changed = {line for line in lines if line > 0}
    hits = [
        symbol
        for symbol in symbols
        if changed.intersection(range(symbol.start_line, symbol.end_line + 1))
    ]
    chosen: list[TsSymbol] = []
    for symbol in hits:
        span = symbol.end_line - symbol.start_line
        if any(
            other is not symbol
            and other.start_line >= symbol.start_line
            and other.end_line <= symbol.end_line
            and other.end_line - other.start_line < span
            and changed.intersection(range(other.start_line, other.end_line + 1))
            for other in hits
        ):
            continue
        chosen.append(symbol)
    chosen.sort(key=lambda item: (item.start_line, item.qualname, item.kind))
    return tuple(chosen)


def _replace_empty_head(picks: list[_Pick], path: str, lines: tuple[int, ...]) -> list[_Pick]:
    replaced: list[_Pick] = []
    for pick in picks:
        if _empty_head_pick(pick, path):
            replaced.append(replace(pick, lines=lines))
            continue
        replaced.append(pick)
    return replaced


def _drop_empty_head(picks: list[_Pick], path: str) -> list[_Pick]:
    return [pick for pick in picks if not _empty_head_pick(pick, path)]


def _empty_head_pick(pick: _Pick, path: str) -> bool:
    return pick.priority == 0 and pick.revision == "head" and pick.path == path and pick.lines == ()


def _merge_symbols(found: tuple[AngularSymbol, ...], carried: tuple[AngularSymbol, ...]) -> tuple[AngularSymbol, ...]:
    merged: list[AngularSymbol] = []
    seen: set[tuple[str, str, str, str, int, int]] = set()
    for item in (*found, *carried):
        key = (item.revision, item.path, item.qualname, item.kind, item.line_start, item.line_end)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    merged.sort(key=lambda item: (item.path, item.revision, item.line_start, item.qualname))
    return tuple(merged)


def _changed_symbols(
    files: tuple[FileChange, ...], indexes: dict[str, TsCommitIndex], commits: dict[str, str]
) -> tuple[AngularSymbol, ...]:
    found: list[AngularSymbol] = []
    for change in files:
        pairs = (
            ("base", change.old_path, change.old_changed_lines),
            ("head", change.new_path, change.new_changed_lines),
        )
        for revision, path, lines in pairs:
            if path is None or not is_typescript_path(path):
                continue
            changed = set(lines)
            for item in indexes[revision].files:
                if item.path != path:
                    continue
                for symbol in item.symbols:
                    if changed.intersection(range(symbol.start_line, symbol.end_line + 1)):
                        found.append(
                            AngularSymbol(
                                revision,
                                commits[revision],
                                path,
                                symbol.qualname,
                                symbol.kind,
                                symbol.start_line,
                                symbol.end_line,
                            )
                        )
    found.sort(key=lambda item: (item.path, item.revision, item.line_start, item.qualname))
    return tuple(found)


def _unresolved(graphs: dict[str, AngularGraph], relevant: set[str]) -> tuple[AngularUnresolved, ...]:
    rows = [
        AngularUnresolved(
            edge.kind,
            edge.confidence,
            edge.resolution_reason,
            edge.from_path,
            edge.to_path,
            edge.to_symbol,
            edge.line,
            redact_secrets(edge.evidence),
            edge.source_revision,
        )
        for revision, graph in graphs.items()
        for edge in graph.edges
        if edge.confidence == "unresolved" and edge.from_path in relevant
    ]
    rows.sort(key=lambda item: (item.kind, item.from_path, item.to_symbol or "", item.source_revision, item.line))
    return tuple(rows)

