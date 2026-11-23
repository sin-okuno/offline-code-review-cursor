"""Versioned review-context data structures."""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_VERSION = "1.1.0"
TOOL_PHASE = "2-python"
ANGULAR_CONTEXT_SCHEMA = "1"

STATUS_LABELS: dict[str, str] = {
    "A": "added",
    "C": "copied",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type_changed",
    "U": "unmerged",
    "X": "unknown",
}


class OfflineReviewError(Exception):
    """Base error for expected review failures."""


class UsageError(OfflineReviewError):
    """The CLI arguments cannot be executed."""


class RevisionError(OfflineReviewError):
    """A revision is unsafe or cannot be resolved to one commit."""


class GitError(OfflineReviewError):
    """A local Git operation failed or was refused."""


class DiffParseError(OfflineReviewError):
    """A Git diff could not be parsed without dropping changes."""


@dataclass(frozen=True, slots=True)
class AffectedSymbol:
    """A definition or top-level statement touched by changed lines on one side."""

    qualname: str
    kind: str
    start_line: int
    end_line: int
    changed_lines: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ImportRelationship:
    """One file-level import edge incident to a changed Python path."""

    direction: str
    changed_path: str
    changed_commit: str
    changed_module: str | None
    other_path: str | None
    other_module: str | None
    certainty: str
    kind: str
    line: int
    evidence: str


@dataclass(frozen=True, slots=True)
class LineRange:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Hunk:
    kind: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    old_lines: tuple[int, ...]
    new_lines: tuple[int, ...]
    old_line_ranges: tuple[LineRange, ...]
    new_line_ranges: tuple[LineRange, ...]
    evidence: str


@dataclass(frozen=True, slots=True)
class FileChange:
    status: str
    status_code: str
    similarity: int | None
    old_path: str | None
    new_path: str | None
    old_mode: str
    new_mode: str
    old_blob: str | None
    new_blob: str | None
    diff_base_commit: str
    head_commit: str
    binary: bool
    submodule: bool
    symlink: bool
    lfs_pointer: bool
    lfs_pointer_old: bool
    lfs_pointer_new: bool
    old_changed_lines: tuple[int, ...]
    new_changed_lines: tuple[int, ...]
    old_changed_line_ranges: tuple[LineRange, ...]
    new_changed_line_ranges: tuple[LineRange, ...]
    hunks: tuple[Hunk, ...]
    old_python_status: str = "not_python"
    new_python_status: str = "not_python"
    old_affected_symbols: tuple[AffectedSymbol, ...] = ()
    new_affected_symbols: tuple[AffectedSymbol, ...] = ()


@dataclass(frozen=True, slots=True)
class Warning:
    code: str
    message: str
    path: str | None = None
    commit: str | None = None


@dataclass(frozen=True, slots=True)
class Comparison:
    mode: str
    semantics: str
    description: str
    requested_base: str
    requested_head: str
    base_commit: str
    head_commit: str
    diff_base_commit: str
    merge_base_commit: str | None
    merge_bases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Coverage:
    analysis_complete: bool
    partial: bool
    partial_reasons: tuple[str, ...]
    python_ast: str
    dependency_index: str
    angular_semantic: str
    typescript_html: str
    files_total: int
    files_with_hunks: int
    files_added: int
    files_modified: int
    files_deleted: int
    files_renamed: int
    files_copied: int
    files_type_changed: int
    files_binary: int
    files_submodule: int
    files_symlink: int
    files_lfs_pointer: int
    files_angular_or_typescript: int
    hunks_total: int
    deletion_hunks: int
    addition_hunks: int
    edit_hunks: int
    python_files_parsed: int = 0
    python_files_failed: int = 0
    python_files_indexed: int = 0
    imports_confirmed: int = 0
    imports_uncertain: int = 0
    imports_unresolved: int = 0


@dataclass(frozen=True, slots=True)
class AngularNode:
    commit: str
    path: str
    symbol: str | None


@dataclass(frozen=True, slots=True)
class AngularEdge:
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
class AngularSelection:
    revision: str
    commit: str
    path: str
    line_start: int
    line_end: int
    reason: str
    confidence: str
    content: str
    score: int = 0


@dataclass(frozen=True, slots=True)
class AngularOmission:
    revision: str
    commit: str
    path: str
    reason: str
    missing_lines: int = 0
    resolution_reason: str = ""


@dataclass(frozen=True, slots=True)
class AngularCoverage:
    typescript_semantic: str
    template_bindings: str
    truncated: bool
    ngrx: str = "not_applicable"
    base_commit: str = ""
    head_commit: str = ""
    line_budget: int = 0
    lines_selected: int = 0
    lines_omitted: int = 0


@dataclass(frozen=True, slots=True)
class AngularSymbol:
    revision: str
    commit: str
    path: str
    qualname: str
    kind: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class AngularUnresolved:
    kind: str
    confidence: str
    resolution_reason: str
    from_path: str
    to_path: str | None
    to_symbol: str | None
    line: int
    evidence: str
    source_revision: str


@dataclass(frozen=True, slots=True)
class ReviewUnit:
    unit_id: str
    excerpts: tuple[AngularSelection, ...]
    line_count: int


@dataclass(frozen=True, slots=True)
class ChangedLineStat:
    revision: str
    path: str
    changed_lines_total: int
    changed_lines_included: int
    changed_lines_omitted: int


@dataclass(frozen=True, slots=True)
class AngularContext:
    schema: str
    status: str
    analysis_method: str
    nodes: tuple[AngularNode, ...]
    edges: tuple[AngularEdge, ...]
    selected: tuple[AngularSelection, ...]
    omitted: tuple[AngularOmission, ...]
    warnings: tuple[Warning, ...]
    coverage: AngularCoverage
    changed_symbols: tuple[AngularSymbol, ...] = ()
    unresolved: tuple[AngularUnresolved, ...] = ()
    review_units: tuple[ReviewUnit, ...] = ()
    changed_line_files: tuple[ChangedLineStat, ...] = ()


def empty_angular_context() -> AngularContext:
    return AngularContext(
        schema="1",
        status="not_applicable",
        analysis_method="none",
        nodes=(),
        edges=(),
        selected=(),
        omitted=(),
        warnings=(),
        coverage=AngularCoverage("not_applicable", "unsupported", False),
    )


@dataclass(frozen=True, slots=True)
class ReviewContext:
    schema_version: str
    tool_name: str
    tool_version: str
    tool_phase: str
    repository: str
    comparison: Comparison
    files: tuple[FileChange, ...]
    warnings: tuple[Warning, ...]
    coverage: Coverage
    errors: tuple[str, ...]
    python_roots: tuple[str, ...] = ()
    import_relationships: tuple[ImportRelationship, ...] = ()
    angular_context: AngularContext = field(default_factory=empty_angular_context)


def status_label(status_code: str) -> str:
    try:
        return STATUS_LABELS[status_code]
    except KeyError as exc:
        raise DiffParseError(f"unsupported diff status {status_code!r}") from exc


def display_path(change: FileChange) -> str:
    return change.new_path or change.old_path or "(unknown)"
