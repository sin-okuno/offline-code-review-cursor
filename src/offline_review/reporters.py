"""Write the versioned JSON and Markdown review context."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from offline_review.models import (
    AffectedSymbol,
    AngularContext,
    Comparison,
    Coverage,
    FileChange,
    Hunk,
    ImportRelationship,
    LineRange,
    ReviewContext,
    UsageError,
    Warning,
    display_path,
)
from offline_review.redaction import redact_secrets


def write_reports(context: ReviewContext, output_dir: Path) -> tuple[Path, Path]:
    if is_reparse_point(output_dir):
        raise UsageError("output directory must not be a symlink or junction")
    output_dir.mkdir(parents=True, exist_ok=True)
    if is_reparse_point(output_dir):
        raise UsageError("output directory must not be a symlink or junction")
    json_path = output_dir / "review-context.json"
    markdown_path = output_dir / "review-context.md"
    rendered = (
        (json_path, render_json(context)),
        (markdown_path, render_markdown(context)),
    )
    for path, _text in rendered:
        if is_reparse_point(path):
            raise UsageError("refusing to write through a symlink in the output directory")
    for path, text in rendered:
        _write_atomic(path, text)
    return json_path, markdown_path


def render_json(context: ReviewContext) -> str:
    payload = {
        "angular_context": _angular(context.angular_context),
        "comparison": _comparison(context.comparison),
        "coverage": _coverage(context.coverage),
        "errors": [redact_secrets(item) for item in context.errors],
        "files": [_file(change) for change in context.files],
        "import_relationships": [_relationship(item) for item in context.import_relationships],
        "python_roots": list(context.python_roots),
        "repository": context.repository,
        "schema_version": context.schema_version,
        "tool": {
            "name": context.tool_name,
            "phase": context.tool_phase,
            "version": context.tool_version,
        },
        "warnings": [_warning(item) for item in context.warnings],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_markdown(context: ReviewContext) -> str:
    comparison = context.comparison
    coverage = context.coverage
    lines = [
        "# Review context",
        "",
        f"- Schema version: {context.schema_version}",
        f"- Tool: {context.tool_name} {context.tool_version} ({context.tool_phase})",
        f"- Repository: {context.repository}",
        "",
        "## Comparison",
        "",
        f"- Mode: {comparison.mode}",
        f"- Semantics: {comparison.semantics}",
        f"- Requested base: {comparison.requested_base}",
        f"- Requested head: {comparison.requested_head}",
        f"- Base commit: {comparison.base_commit}",
        f"- Head commit: {comparison.head_commit}",
        f"- Diff base commit: {comparison.diff_base_commit}",
        f"- Merge-base commit: {comparison.merge_base_commit or '(none)'}",
        f"- Merge-bases: {_plain_list(comparison.merge_bases)}",
        "",
        comparison.description,
        "",
        "## Coverage",
        "",
        f"- Analysis complete: {_yes_no(coverage.analysis_complete)}",
        f"- Partial: {_yes_no(coverage.partial)}",
        f"- Partial reasons: {_plain_list(coverage.partial_reasons)}",
        f"- Python roots: {_plain_list(context.python_roots)}",
        f"- Python AST: {coverage.python_ast}",
        f"- Dependency index: {coverage.dependency_index}",
        f"- Python files indexed: {coverage.python_files_indexed}",
        f"- Python files parsed: {coverage.python_files_parsed}",
        f"- Python files failed: {coverage.python_files_failed}",
        f"- Confirmed imports: {coverage.imports_confirmed}",
        f"- Uncertain imports: {coverage.imports_uncertain}",
        f"- Unresolved imports: {coverage.imports_unresolved}",
        f"- Angular semantic: {coverage.angular_semantic}",
        f"- TypeScript and HTML: {coverage.typescript_html}",
        f"- Files: {coverage.files_total}",
        f"- Files with hunks: {coverage.files_with_hunks}",
        f"- Added: {coverage.files_added}",
        f"- Modified: {coverage.files_modified}",
        f"- Deleted: {coverage.files_deleted}",
        f"- Renamed: {coverage.files_renamed}",
        f"- Copied: {coverage.files_copied}",
        f"- Type changed: {coverage.files_type_changed}",
        f"- Binary: {coverage.files_binary}",
        f"- Submodule: {coverage.files_submodule}",
        f"- Symlink: {coverage.files_symlink}",
        f"- LFS pointer: {coverage.files_lfs_pointer}",
        f"- Angular or TypeScript paths: {coverage.files_angular_or_typescript}",
        f"- Hunks: {coverage.hunks_total}",
        f"- Deletion hunks: {coverage.deletion_hunks}",
        f"- Addition hunks: {coverage.addition_hunks}",
        f"- Edit hunks: {coverage.edit_hunks}",
        "",
        "## Warnings",
        "",
    ]
    if not context.warnings:
        lines.append("- (none)")
    for warning in context.warnings:
        location = warning.path or "(no path)"
        commit = warning.commit or "(no commit)"
        lines.append(
            f"- {warning.code} | {location} | {commit} | {redact_secrets(warning.message)}"
        )
    lines.extend(["", "## Import relationships", ""])
    if not context.import_relationships:
        lines.append("(none)")
        lines.append("")
    for item in context.import_relationships:
        other = item.other_path or "(unresolved)"
        module = item.other_module or "(no module)"
        lines.append(
            f"- {item.direction} | {item.certainty} | {item.kind} | {item.changed_path} | "
            f"{module} | {other} | line {item.line} | {redact_secrets(item.evidence)}"
        )
    lines.extend(_markdown_angular(context.angular_context))
    lines.extend(["", "## Changed files", ""])
    if not context.files:
        lines.append("No changed files.")
    for index, change in enumerate(context.files, start=1):
        lines.extend(_markdown_file(index, change))
    if context.errors:
        lines.extend(["", "## Errors", ""])
        for error in context.errors:
            lines.append(f"- {redact_secrets(error)}")
    lines.append("")
    return "\n".join(lines)


def _angular(context: AngularContext) -> dict[str, object]:
    coverage = context.coverage
    return {
        "analysis_method": context.analysis_method,
        "coverage": {
            "base_commit": coverage.base_commit,
            "head_commit": coverage.head_commit,
            "line_budget": coverage.line_budget,
            "lines_omitted": coverage.lines_omitted,
            "lines_selected": coverage.lines_selected,
            "ngrx": coverage.ngrx,
            "template_bindings": coverage.template_bindings,
            "truncated": coverage.truncated,
            "typescript_semantic": coverage.typescript_semantic,
        },
        "changed_symbols": [
            {
                "commit": item.commit,
                "kind": item.kind,
                "line_end": item.line_end,
                "line_start": item.line_start,
                "path": item.path,
                "qualname": item.qualname,
                "revision": item.revision,
            }
            for item in context.changed_symbols
        ],
        "changed_line_files": [
            {
                "changed_lines_included": item.changed_lines_included,
                "changed_lines_omitted": item.changed_lines_omitted,
                "changed_lines_total": item.changed_lines_total,
                "path": item.path,
                "revision": item.revision,
            }
            for item in context.changed_line_files
        ],
        "edges": [
            {
                "confidence": edge.confidence,
                "evidence": edge.evidence,
                "from_path": edge.from_path,
                "kind": edge.kind,
                "line": edge.line,
                "resolution_reason": edge.resolution_reason,
                "source_revision": edge.source_revision,
                "to_path": edge.to_path,
                "to_symbol": edge.to_symbol,
            }
            for edge in context.edges
        ],
        "nodes": [
            {"commit": node.commit, "path": node.path, "symbol": node.symbol}
            for node in context.nodes
        ],
        "omitted": [
            {
                "commit": item.commit,
                "missing_lines": item.missing_lines,
                "path": item.path,
                "reason": item.reason,
                "resolution_reason": item.resolution_reason,
                "revision": item.revision,
            }
            for item in context.omitted
        ],
        "schema": context.schema,
        "review_units": [
            {
                "excerpts": [
                    {
                        "commit": item.commit,
                        "confidence": item.confidence,
                        "content": item.content,
                        "line_end": item.line_end,
                        "line_start": item.line_start,
                        "path": item.path,
                        "reason": item.reason,
                        "revision": item.revision,
                    }
                    for item in unit.excerpts
                ],
                "line_count": unit.line_count,
                "unit_id": unit.unit_id,
            }
            for unit in context.review_units
        ],
        "selected": [
            {
                "commit": item.commit,
                "confidence": item.confidence,
                "content": item.content,
                "line_end": item.line_end,
                "line_start": item.line_start,
                "path": item.path,
                "reason": item.reason,
                "revision": item.revision,
                "score": item.score,
            }
            for item in context.selected
        ],
        "status": context.status,
        "unresolved": [
            {
                "confidence": item.confidence,
                "evidence": item.evidence,
                "from_path": item.from_path,
                "kind": item.kind,
                "line": item.line,
                "resolution_reason": item.resolution_reason,
                "source_revision": item.source_revision,
                "to_path": item.to_path,
                "to_symbol": item.to_symbol,
            }
            for item in context.unresolved
        ],
        "warnings": [_warning(item) for item in context.warnings],
    }


def _markdown_angular(context: AngularContext) -> list[str]:
    coverage = context.coverage
    lines = [
        "",
        "## Angular context",
        "",
        f"- Schema: {context.schema}",
        f"- Status: {context.status}",
        f"- Analysis method: {context.analysis_method}",
        f"- TypeScript semantic: {coverage.typescript_semantic}",
        f"- Template bindings: {coverage.template_bindings}",
        f"- NgRx: {coverage.ngrx}",
        f"- Line budget: {coverage.lines_selected}/{coverage.line_budget} selected, {coverage.lines_omitted} omitted",
        f"- Review units: {len(context.review_units)}",
        (
            "- Changed lines across review units: "
            f"{sum(item.changed_lines_included for item in context.changed_line_files)} included, "
            f"{sum(item.changed_lines_omitted for item in context.changed_line_files)} omitted"
        ),
        f"- Truncated: {_yes_no(coverage.truncated)}",
        "",
        "### Git change summary",
        "",
        f"- Base commit: {coverage.base_commit or '(none)'}",
        f"- Head commit: {coverage.head_commit or '(none)'}",
        "",
        "### Changed symbols",
        "",
    ]
    if not context.changed_symbols:
        lines.append("(none)")
        lines.append("")
    for item in context.changed_symbols:
        lines.append(
            f"- {item.revision} | {item.path} | {item.qualname} | {item.kind} | {item.line_start}-{item.line_end}"
        )
    lines.extend(["", "### Angular impact", ""])
    impact = [edge for edge in context.edges if edge.kind in {"template_binding", "component_template", "component_selector"}]
    lines.extend(_edge_lines(impact))
    lines.extend(["### NgRx paths", ""])
    ngrx = [edge for edge in context.edges if edge.kind.startswith("ngrx_") or edge.kind == "service_reference"]
    lines.extend(_edge_lines(ngrx))
    lines.extend(["### Priority review targets", ""])
    ranked = sorted(context.selected, key=lambda item: (-item.score, item.path, item.revision, item.line_start))
    if not ranked:
        lines.append("(none)")
        lines.append("")
    for item in ranked:
        lines.append(f"- {item.score} | {item.reason} | {item.revision} | {item.path}:{item.line_start}-{item.line_end}")
    lines.extend(["", "### Related code", "", "### Selected excerpts", ""])
    if not context.selected:
        lines.append("(none)")
        lines.append("")
    for item in context.selected:
        lines.extend(
            [
                f"#### {item.path}",
                "",
                f"- Revision: {item.revision}",
                f"- Commit: {item.commit}",
                f"- Lines: {item.line_start}-{item.line_end}",
                f"- Reason: {item.reason}",
                f"- Score: {item.score}",
                f"- Confidence: {item.confidence}",
                "",
                _fence(item.content),
                "",
            ]
        )
    later = [unit for unit in context.review_units if unit.unit_id != "unit-1"]
    lines.extend(["### Later review units", ""])
    if not later:
        lines.append("(none)")
        lines.append("")
    for unit in later:
        lines.append(f"#### {unit.unit_id} ({unit.line_count} lines)")
        lines.append("")
        for item in unit.excerpts:
            lines.extend(
                [
                    f"##### {item.path}",
                    "",
                    f"- Revision: {item.revision}",
                    f"- Commit: {item.commit}",
                    f"- Lines: {item.line_start}-{item.line_end}",
                    f"- Reason: {item.reason}",
                    "",
                    _fence(item.content),
                    "",
                ]
            )
    lines.extend(["### Omitted", ""])
    if not context.omitted:
        lines.append("(none)")
    for item in context.omitted:
        lines.append(f"- {item.reason} | {item.revision} | {item.commit} | {item.path} | missing {item.missing_lines}")
    lines.extend(["", "### Unresolved dependencies", ""])
    if not context.unresolved:
        lines.append("(none)")
    for item in context.unresolved:
        lines.append(
            f"- {item.resolution_reason or item.confidence} | {item.kind} | {item.from_path} | {item.to_symbol or item.to_path or '(none)'}"
        )
    lines.extend(
        [
            "",
            "### Analysis limits",
            "",
            "- Template expressions are lexical. Type checking, control-flow, and `tsconfig` extends are out of scope.",
            "- Selector matches are candidates. A hyphenated tag is not a confirmed component edge.",
            "- NgRx edges require an imported name used by `dispatch`, `select`, `ofType`, `on`, or `inject`.",
            "- Effect method calls that are not an imported service injection are not followed to an API.",
            "",
        ]
    )
    return lines


def _edge_lines(edges: list) -> list[str]:
    if not edges:
        return ["(none)", ""]
    return [
        f"- {item.confidence} | {item.kind} | {item.from_path} -> {item.to_path or '(unresolved)'} | {item.to_symbol or '(none)'}"
        for item in edges
    ] + [""]


def _fence(content: str) -> str:
    run = 0
    longest = 0
    for char in content:
        if char == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    marker = "`" * max(3, longest + 1)
    return f"{marker}\n{content}\n{marker}"


def _markdown_file(index: int, change: FileChange) -> list[str]:
    lines = [
        f"### {index}. {change.status}",
        "",
        f"- Status code: {change.status_code}",
        f"- Similarity: {change.similarity if change.similarity is not None else '(none)'}",
        f"- Old path: {change.old_path or '(none)'}",
        f"- New path: {change.new_path or '(none)'}",
        f"- Old mode: {change.old_mode}",
        f"- New mode: {change.new_mode}",
        f"- Old blob: {change.old_blob or '(none)'}",
        f"- New blob: {change.new_blob or '(none)'}",
        f"- Diff base commit: {change.diff_base_commit}",
        f"- Head commit: {change.head_commit}",
        f"- Binary: {_yes_no(change.binary)}",
        f"- Submodule: {_yes_no(change.submodule)}",
        f"- Symlink: {_yes_no(change.symlink)}",
        f"- LFS pointer: {_yes_no(change.lfs_pointer)}",
        f"- Old changed lines: {_ranges(change.old_changed_line_ranges)}",
        f"- New changed lines: {_ranges(change.new_changed_line_ranges)}",
        f"- Old Python status: {change.old_python_status}",
        f"- New Python status: {change.new_python_status}",
        "",
    ]
    lines.extend(_markdown_symbols("Old affected symbols", change.old_affected_symbols))
    lines.extend(_markdown_symbols("New affected symbols", change.new_affected_symbols))
    if not change.hunks:
        lines.append("No text hunks.")
        lines.append("")
        return lines
    for hunk_index, hunk in enumerate(change.hunks, start=1):
        lines.extend(
            [
                f"#### Hunk {hunk_index}",
                "",
                f"- Kind: {hunk.kind}",
                f"- Evidence: {hunk.evidence}",
                f"- Old range: {hunk.old_start},{hunk.old_count}",
                f"- New range: {hunk.new_start},{hunk.new_count}",
                f"- Old lines: {_ranges(hunk.old_line_ranges)}",
                f"- New lines: {_ranges(hunk.new_line_ranges)}",
                "",
            ]
        )
    lines.append(f"- Display path: {display_path(change)}")
    lines.append("")
    return lines


def _comparison(comparison: Comparison) -> dict[str, object]:
    return {
        "base_commit": comparison.base_commit,
        "description": comparison.description,
        "diff_base_commit": comparison.diff_base_commit,
        "head_commit": comparison.head_commit,
        "merge_base_commit": comparison.merge_base_commit,
        "merge_bases": list(comparison.merge_bases),
        "mode": comparison.mode,
        "requested_base": comparison.requested_base,
        "requested_head": comparison.requested_head,
        "semantics": comparison.semantics,
    }


def _coverage(coverage: Coverage) -> dict[str, object]:
    return {
        "addition_hunks": coverage.addition_hunks,
        "analysis_complete": coverage.analysis_complete,
        "angular_semantic": coverage.angular_semantic,
        "deletion_hunks": coverage.deletion_hunks,
        "dependency_index": coverage.dependency_index,
        "edit_hunks": coverage.edit_hunks,
        "imports_confirmed": coverage.imports_confirmed,
        "imports_uncertain": coverage.imports_uncertain,
        "imports_unresolved": coverage.imports_unresolved,
        "files_added": coverage.files_added,
        "files_angular_or_typescript": coverage.files_angular_or_typescript,
        "files_binary": coverage.files_binary,
        "files_copied": coverage.files_copied,
        "files_deleted": coverage.files_deleted,
        "files_lfs_pointer": coverage.files_lfs_pointer,
        "files_modified": coverage.files_modified,
        "files_renamed": coverage.files_renamed,
        "files_submodule": coverage.files_submodule,
        "files_symlink": coverage.files_symlink,
        "files_total": coverage.files_total,
        "files_type_changed": coverage.files_type_changed,
        "files_with_hunks": coverage.files_with_hunks,
        "hunks_total": coverage.hunks_total,
        "partial": coverage.partial,
        "partial_reasons": list(coverage.partial_reasons),
        "python_ast": coverage.python_ast,
        "python_files_failed": coverage.python_files_failed,
        "python_files_indexed": coverage.python_files_indexed,
        "python_files_parsed": coverage.python_files_parsed,
        "typescript_html": coverage.typescript_html,
    }


def _file(change: FileChange) -> dict[str, object]:
    return {
        "binary": change.binary,
        "diff_base_commit": change.diff_base_commit,
        "head_commit": change.head_commit,
        "hunks": [_hunk(hunk) for hunk in change.hunks],
        "lfs_pointer": change.lfs_pointer,
        "lfs_pointer_new": change.lfs_pointer_new,
        "lfs_pointer_old": change.lfs_pointer_old,
        "new_blob": change.new_blob,
        "new_changed_line_ranges": [_range(item) for item in change.new_changed_line_ranges],
        "new_changed_lines": list(change.new_changed_lines),
        "new_mode": change.new_mode,
        "new_affected_symbols": [_symbol(symbol) for symbol in change.new_affected_symbols],
        "new_path": change.new_path,
        "new_python_status": change.new_python_status,
        "old_affected_symbols": [_symbol(symbol) for symbol in change.old_affected_symbols],
        "old_blob": change.old_blob,
        "old_changed_line_ranges": [_range(item) for item in change.old_changed_line_ranges],
        "old_changed_lines": list(change.old_changed_lines),
        "old_mode": change.old_mode,
        "old_path": change.old_path,
        "old_python_status": change.old_python_status,
        "similarity": change.similarity,
        "status": change.status,
        "status_code": change.status_code,
        "submodule": change.submodule,
        "symlink": change.symlink,
    }


def _hunk(hunk: Hunk) -> dict[str, object]:
    return {
        "evidence": hunk.evidence,
        "kind": hunk.kind,
        "new_count": hunk.new_count,
        "new_line_ranges": [_range(item) for item in hunk.new_line_ranges],
        "new_lines": list(hunk.new_lines),
        "new_start": hunk.new_start,
        "old_count": hunk.old_count,
        "old_line_ranges": [_range(item) for item in hunk.old_line_ranges],
        "old_lines": list(hunk.old_lines),
        "old_start": hunk.old_start,
    }


def _symbol(symbol: AffectedSymbol) -> dict[str, object]:
    return {
        "changed_lines": list(symbol.changed_lines),
        "end_line": symbol.end_line,
        "kind": symbol.kind,
        "qualname": symbol.qualname,
        "start_line": symbol.start_line,
    }


def _relationship(item: ImportRelationship) -> dict[str, object]:
    return {
        "certainty": item.certainty,
        "changed_commit": item.changed_commit,
        "changed_module": item.changed_module,
        "changed_path": item.changed_path,
        "direction": item.direction,
        "evidence": redact_secrets(item.evidence),
        "kind": item.kind,
        "line": item.line,
        "other_module": item.other_module,
        "other_path": item.other_path,
    }


def _markdown_symbols(title: str, symbols: tuple[AffectedSymbol, ...]) -> list[str]:
    lines = [f"- {title}:"]
    if not symbols:
        lines.append("  - (none)")
        return lines
    for symbol in symbols:
        changed = ", ".join(str(line) for line in symbol.changed_lines)
        lines.append(
            f"  - {symbol.kind} {symbol.qualname} {symbol.start_line}-{symbol.end_line} lines {changed}"
        )
    return lines


def _warning(warning: Warning) -> dict[str, object]:
    return {
        "code": warning.code,
        "commit": warning.commit,
        "message": redact_secrets(warning.message),
        "path": warning.path,
    }


def _range(item: LineRange) -> dict[str, int]:
    return {"end": item.end, "start": item.start}


def _ranges(ranges: tuple[LineRange, ...]) -> str:
    if not ranges:
        return "(none)"
    return ", ".join(f"{item.start}-{item.end}" for item in ranges)


def _plain_list(values: tuple[str, ...]) -> str:
    if not values:
        return "(none)"
    return ", ".join(values)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def is_reparse_point(path: Path) -> bool:
    """Return whether ``path`` is a symlink, junction, or other reparse point.

    ``Path.resolve`` follows the output directory, so a junction whose target is
    inside the repository is rejected earlier. This check covers a report file
    that itself points somewhere else.
    """

    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _write_atomic(path: Path, text: str) -> None:
    if is_reparse_point(path):
        raise UsageError("refusing to write through a symlink in the output directory")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if is_reparse_point(temporary):
        raise UsageError("refusing to write through a symlink in the output directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if os.name == "nt":
        flags |= os.O_BINARY
    try:
        descriptor = os.open(temporary, flags)
    except OSError as exc:
        raise UsageError(f"cannot create a report file in the output directory: {path.name}") from exc
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        if is_reparse_point(path):
            raise UsageError("refusing to write through a symlink in the output directory")
        os.replace(temporary, path)
    except UsageError:
        temporary.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise UsageError(f"cannot replace the report file: {path.name}") from exc
