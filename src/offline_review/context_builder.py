"""Assemble a versioned review context from read-only Git data."""

from __future__ import annotations

from offline_review import __version__
from offline_review.dependency_index import IndexedModule, PythonAnalysis, analyze_python
from offline_review.parse_cache import ParseCache
from offline_review.diff_parser import ParsedFile, coalesce_lines, combine_diff, parse_raw_diff_z
from offline_review.git_adapter import GitAdapter, is_lfs_pointer
from offline_review.context_selector import ContextBudget, select_angular_context
from offline_review.models import (
    SCHEMA_VERSION,
    TOOL_PHASE,
    Comparison,
    Coverage,
    FileChange,
    GitError,
    ReviewContext,
    Warning,
    display_path,
    status_label,
)
from offline_review.python_indexer import (
    MAX_PYTHON_SOURCE_BYTES,
    ParseResult,
    is_python_path,
    parse_python_source,
)

_LFS_READ_LIMIT = 1024
_PYTHON_READ_CHUNK = 32
_FRONTEND_SUFFIXES = (".css", ".html", ".less", ".scss", ".ts", ".tsx")
_MERGE_BASE_DESCRIPTION = (
    "Three-dot comparison. The diff base is the merge base of the resolved "
    "base and head commits, and the diff runs from that merge base to head. "
    "This matches git diff <base>...<head>."
)
_DIRECT_DESCRIPTION = (
    "Two-dot comparison. The diff runs from the resolved base commit to the "
    "resolved head commit. This matches git diff <base> <head>. "
    "A merge base is recorded when Git can compute one, and it is not the diff base."
)


def build_context(
    adapter: GitAdapter,
    *,
    repository: str,
    base: str,
    head: str,
    comparison: str,
    python_roots: tuple[str, ...] = (),
    cache: ParseCache | None = None,
    budget: ContextBudget | None = None,
) -> ReviewContext:
    if comparison not in {"merge-base", "direct"}:
        raise GitError(f"unsupported comparison mode: {comparison}")
    base_commit = adapter.resolve_commit(base)
    head_commit = adapter.resolve_commit(head)
    selected_merge_base = adapter.merge_base(base_commit, head_commit)
    every_merge_base = tuple(sorted(set(adapter.merge_bases(base_commit, head_commit))))
    warnings: list[Warning] = []
    if selected_merge_base is not None and selected_merge_base not in every_merge_base:
        every_merge_base = tuple(sorted((*every_merge_base, selected_merge_base)))
        warnings.append(
            Warning(
                code="MERGE_BASE_SELECTION",
                message=(
                    "git merge-base selected a commit that git merge-base --all did not list. "
                    f"The selected commit {selected_merge_base} was added to the recorded set."
                ),
                commit=selected_merge_base,
            )
        )
    if len(every_merge_base) > 1:
        listed = ", ".join(every_merge_base)
        if comparison == "merge-base":
            message = (
                f"Git found multiple merge bases ({listed}). "
                f"The diff base is {selected_merge_base}, the commit returned by git merge-base."
            )
        else:
            message = (
                f"Git found multiple merge bases ({listed}). "
                "Direct mode diffs the resolved base commit and only records these merge bases."
            )
        warnings.append(Warning(code="MULTIPLE_MERGE_BASES", message=message))
    if comparison == "merge-base":
        if selected_merge_base is None:
            raise GitError(
                "no merge base exists between the resolved base and head; "
                "use --comparison direct to diff those two commits"
            )
        diff_base = selected_merge_base
        semantics = "three-dot"
        description = _MERGE_BASE_DESCRIPTION
    else:
        diff_base = base_commit
        semantics = "two-dot"
        description = _DIRECT_DESCRIPTION
        if selected_merge_base is None:
            warnings.append(
                Warning(
                    code="NO_MERGE_BASE",
                    message=(
                        "No merge base exists between the resolved base and head. "
                        "The report uses the direct two-dot diff."
                    ),
                )
            )
    raw, raw_notice = adapter.diff_raw(diff_base, head_commit)
    patch, patch_notice = adapter.diff_patch(diff_base, head_commit)
    for notice in _unique((raw_notice, patch_notice)):
        warnings.append(
            Warning(code="GIT_DIFF_NOTICE", message=f"git diff reported: {notice}")
        )
    parsed = combine_diff(parse_raw_diff_z(raw), patch)
    parsed = tuple(sorted(parsed, key=_file_sort_key))
    lfs_flags, missing_blobs = _lfs_flags(adapter, parsed)
    for blob in missing_blobs:
        warnings.append(
            Warning(
                code="MISSING_BLOB",
                message=f"Git object {blob} was named by the diff and could not be read.",
                commit=blob,
            )
        )
    if adapter.worktree_differs_from_head():
        warnings.append(
            Warning(
                code="WORKTREE_DIRTY",
                message=(
                    "The work tree or index differs from HEAD, or untracked files are present. "
                    "The diff is only between the resolved base and head commits. "
                    "Uncommitted worktree content is not part of this report."
                ),
                commit=head_commit,
            )
        )
    files = tuple(
        _to_file_change(
            entry,
            diff_base_commit=diff_base,
            head_commit=head_commit,
            lfs_old=lfs_flags.get(entry.old_blob or "", False),
            lfs_new=lfs_flags.get(entry.new_blob or "", False),
        )
        for entry in parsed
    )
    warnings.extend(_file_warnings(files))
    modules_by_commit, load_warnings = _load_python_modules(
        adapter, (diff_base, head_commit), python_roots, cache
    )
    warnings.extend(load_warnings)
    python = analyze_python(files, modules_by_commit, python_roots)
    files = python.files
    warnings.extend(python.warnings)
    warnings.extend(_phase_warnings(files))
    coverage = _coverage(files, python)
    ordered_warnings = tuple(
        sorted(warnings, key=lambda item: (item.code, item.path or "", item.commit or "", item.message))
    )
    return ReviewContext(
        schema_version=SCHEMA_VERSION,
        tool_name="offline_review",
        tool_version=__version__,
        tool_phase=TOOL_PHASE,
        repository=repository,
        comparison=Comparison(
            mode=comparison,
            semantics=semantics,
            description=description,
            requested_base=base,
            requested_head=head,
            base_commit=base_commit,
            head_commit=head_commit,
            diff_base_commit=diff_base,
            merge_base_commit=selected_merge_base,
            merge_bases=every_merge_base,
        ),
        files=files,
        warnings=ordered_warnings,
        coverage=coverage,
        errors=(),
        python_roots=python_roots,
        import_relationships=python.relationships,
        angular_context=select_angular_context(
            adapter,
            files,
            diff_base=diff_base,
            head=head_commit,
            budget=budget or ContextBudget(),
            cache_directory=None if cache is None else cache.directory,
        ),
    )


def _to_file_change(
    entry: ParsedFile,
    *,
    diff_base_commit: str,
    head_commit: str,
    lfs_old: bool,
    lfs_new: bool,
) -> FileChange:
    old_lines = tuple(sorted({line for hunk in entry.hunks for line in hunk.old_lines}))
    new_lines = tuple(sorted({line for hunk in entry.hunks for line in hunk.new_lines}))
    return FileChange(
        status=status_label(entry.status_code),
        status_code=entry.status_code,
        similarity=entry.similarity,
        old_path=entry.old_path,
        new_path=entry.new_path,
        old_mode=entry.old_mode,
        new_mode=entry.new_mode,
        old_blob=entry.old_blob,
        new_blob=entry.new_blob,
        diff_base_commit=diff_base_commit,
        head_commit=head_commit,
        binary=entry.binary,
        submodule=entry.submodule,
        symlink=entry.symlink,
        lfs_pointer=lfs_old or lfs_new,
        lfs_pointer_old=lfs_old,
        lfs_pointer_new=lfs_new,
        old_changed_lines=old_lines,
        new_changed_lines=new_lines,
        old_changed_line_ranges=coalesce_lines(old_lines),
        new_changed_line_ranges=coalesce_lines(new_lines),
        hunks=entry.hunks,
    )


def _lfs_flags(
    adapter: GitAdapter, files: tuple[ParsedFile, ...]
) -> tuple[dict[str, bool], tuple[str, ...]]:
    ordered: list[str] = []
    seen: set[str] = set()
    for entry in files:
        if entry.submodule:
            continue
        for blob in (entry.old_blob, entry.new_blob):
            if blob and blob not in seen:
                seen.add(blob)
                ordered.append(blob)
    if not ordered:
        return {}, ()
    metadata = adapter.read_blob_metadata(ordered)
    missing = [blob for blob, item in zip(ordered, metadata, strict=True) if item is None]
    readable = [
        blob
        for blob, item in zip(ordered, metadata, strict=True)
        if item is not None and item[0] == "blob" and item[1] <= _LFS_READ_LIMIT
    ]
    contents = adapter.read_blobs(readable)
    flags = {
        blob: is_lfs_pointer(content or b"")
        for blob, content in zip(readable, contents, strict=True)
    }
    for blob, content in zip(readable, contents, strict=True):
        if content is None and blob not in missing:
            missing.append(blob)
    return flags, tuple(missing)


def _file_warnings(files: tuple[FileChange, ...]) -> list[Warning]:
    warnings: list[Warning] = []
    for change in files:
        path = display_path(change)
        commit = change.head_commit
        if change.binary:
            warnings.append(
                Warning(
                    code="BINARY_FILE",
                    message="Binary file differences have no line ranges.",
                    path=path,
                    commit=commit,
                )
            )
        if change.submodule:
            warnings.append(
                Warning(
                    code="SUBMODULE",
                    message=(
                        "Submodule gitlink changes are listed by commit id. "
                        "The submodule was not fetched, checked out, or traversed."
                    ),
                    path=path,
                    commit=commit,
                )
            )
        if change.symlink:
            warnings.append(
                Warning(
                    code="SYMLINK",
                    message="Symlink changes record the link target blob. The target was not followed.",
                    path=path,
                    commit=commit,
                )
            )
        if change.lfs_pointer:
            sides: list[str] = []
            if change.lfs_pointer_old:
                sides.append("old")
            if change.lfs_pointer_new:
                sides.append("new")
            joined = " and ".join(sides)
            warnings.append(
                Warning(
                    code="LFS_POINTER",
                    message=(
                        f"Git LFS pointer detected on the {joined} side. "
                        "The pointer text was not smudged or downloaded."
                    ),
                    path=path,
                    commit=commit,
                )
            )
        if change.status_code == "T":
            warnings.append(
                Warning(
                    code="TYPE_CHANGED",
                    message="The Git file type changed between the diff base and head.",
                    path=path,
                    commit=commit,
                )
            )
    return warnings


def _phase_warnings(files: tuple[FileChange, ...]) -> list[Warning]:
    warnings: list[Warning] = []
    if any(_is_frontend(change.old_path) or _is_frontend(change.new_path) for change in files):
        warnings.append(
            Warning(
                code="ANGULAR_SEMANTIC_OUT_OF_SCOPE",
                message=(
                    "Angular and TypeScript type-checker analysis is out of scope. "
                    "angular_context is a partial lexical result, not a complete semantic graph."
                ),
            )
        )
    return warnings


def _load_python_modules(
    adapter: GitAdapter,
    commits: tuple[str, ...],
    roots: tuple[str, ...],
    cache: ParseCache | None,
) -> tuple[dict[str, dict[str, IndexedModule]], tuple[Warning, ...]]:
    """Parse each Python blob once per object id, then reuse it for every path.

    Source bytes are dropped after each chunk. When ``cache`` is set, a commit
    already stored for this parser version and these import roots is not listed
    or parsed again.
    """

    parsed_by_blob: dict[str, ParseResult] = {}
    warnings: list[Warning] = []
    loaded: dict[str, dict[str, IndexedModule]] = {}
    for commit in sorted(set(commits)):
        if cache is not None:
            hit = cache.load(commit, roots)
            if hit is not None:
                loaded[commit], hit_warnings = hit
                warnings.extend(hit_warnings)
                continue
        modules, blobs, commit_warnings = _parse_commit(adapter, commit, parsed_by_blob)
        warnings.extend(commit_warnings)
        loaded[commit] = modules
        if cache is not None:
            cache.store(commit, roots, modules, blobs)
    return loaded, tuple(warnings)


def _parse_commit(
    adapter: GitAdapter, commit: str, parsed_by_blob: dict[str, ParseResult]
) -> tuple[dict[str, IndexedModule], dict[str, str | None], list[Warning]]:
    entries = [
        (mode, sha, path)
        for mode, sha, path in adapter.list_tree(commit)
        if is_python_path(path) and mode in {"100644", "100755"}
    ]
    metadata = _chunked_metadata(adapter, [sha for _mode, sha, _path in entries])
    modules: dict[str, IndexedModule] = {}
    blobs: dict[str, str | None] = {}
    warnings: list[Warning] = []
    pending: list[tuple[str, str]] = []

    def flush() -> None:
        if not pending:
            return
        needed = [sha for _path, sha in pending if sha not in parsed_by_blob]
        bodies = _chunked_blobs(adapter, needed) if needed else {}
        for path, sha in pending:
            if sha not in parsed_by_blob:
                parsed_by_blob[sha] = _parse_blob(bodies.get(sha))
            modules[path] = IndexedModule(path, commit, parsed_by_blob[sha])
            blobs[path] = sha
        pending.clear()

    for (mode, sha, path), info in zip(entries, metadata, strict=True):
        del mode
        if info is None or info[0] != "blob":
            warnings.append(
                Warning(
                    code="MISSING_BLOB",
                    message=f"Python blob {sha} could not be read.",
                    path=path,
                    commit=commit,
                )
            )
            modules[path] = IndexedModule(path, commit, _missing_parse())
            blobs[path] = sha
            continue
        if info[1] > MAX_PYTHON_SOURCE_BYTES:
            modules[path] = IndexedModule(
                path,
                commit,
                ParseResult(
                    status="too_large",
                    error=(
                        f"Python source exceeds {MAX_PYTHON_SOURCE_BYTES} bytes and was not parsed."
                    ),
                    line_count=0,
                    symbols=(),
                    imports=(),
                    dynamic_nonliteral=False,
                ),
            )
            blobs[path] = sha
            continue
        pending.append((path, sha))
        if len(pending) >= _PYTHON_READ_CHUNK:
            flush()
    flush()
    return modules, blobs, warnings


def _parse_blob(data: bytes | None) -> ParseResult:
    if data is None:
        return _missing_parse()
    if is_lfs_pointer(data):
        return ParseResult(
            status="lfs_pointer",
            error=None,
            line_count=0,
            symbols=(),
            imports=(),
            dynamic_nonliteral=False,
        )
    return parse_python_source(data)


def _missing_parse() -> ParseResult:
    return ParseResult(
        status="missing",
        error="Python blob could not be read.",
        line_count=0,
        symbols=(),
        imports=(),
        dynamic_nonliteral=False,
    )


def _chunked_metadata(
    adapter: GitAdapter, shas: list[str]
) -> list[tuple[str, int] | None]:
    if not shas:
        return []
    records: list[tuple[str, int] | None] = []
    for start in range(0, len(shas), 200):
        records.extend(adapter.read_blob_metadata(shas[start : start + 200]))
    return records


def _chunked_blobs(adapter: GitAdapter, shas: list[str]) -> dict[str, bytes | None]:
    unique = list(dict.fromkeys(shas))
    bodies: dict[str, bytes | None] = {}
    for start in range(0, len(unique), 200):
        chunk = unique[start : start + 200]
        bodies.update(zip(chunk, adapter.read_blobs(chunk), strict=True))
    return bodies


def _coverage(files: tuple[FileChange, ...], python: PythonAnalysis) -> Coverage:
    frontend = sum(
        1 for change in files if _is_frontend(change.old_path) or _is_frontend(change.new_path)
    )
    hunks = [hunk for change in files for hunk in change.hunks]
    reasons: list[str] = []
    dynamic = any(item.code == "PYTHON_DYNAMIC_IMPORT" for item in python.warnings)
    if python.changed_python == 0:
        python_ast = "not_applicable"
    elif python.changed_python_failed:
        python_ast = "partial"
        reasons.append("python_parse_incomplete")
    else:
        python_ast = "parsed"
    if python.python_files_indexed == 0:
        dependency_index = "not_applicable"
    elif not python.roots_configured:
        dependency_index = "not_configured"
        reasons.append("python_roots_not_configured")
    elif (
        python.imports_unresolved
        or python.imports_uncertain
        or python.python_files_failed
        or dynamic
    ):
        dependency_index = "partial"
        if python.imports_unresolved:
            reasons.append("unresolved_imports")
        if python.imports_uncertain:
            reasons.append("uncertain_imports")
        if python.python_files_failed:
            reasons.append("python_index_incomplete")
        if dynamic:
            reasons.append("dynamic_imports")
    else:
        dependency_index = "complete"
    if any(change.binary for change in files):
        reasons.append("binary_files")
    if any(change.submodule for change in files):
        reasons.append("submodules_not_traversed")
    if any(change.symlink for change in files):
        reasons.append("symlinks_not_followed")
    if any(change.lfs_pointer for change in files):
        reasons.append("lfs_pointers_not_smudged")
    if frontend:
        reasons.append("angular_semantic_out_of_scope")
    partial_reasons = tuple(sorted(set(reasons)))
    return Coverage(
        analysis_complete=not partial_reasons,
        partial=bool(partial_reasons),
        partial_reasons=partial_reasons,
        python_ast=python_ast,
        dependency_index=dependency_index,
        angular_semantic="out_of_scope",
        typescript_html="diff_only" if frontend else "none",
        python_files_parsed=python.python_files_parsed,
        python_files_failed=python.python_files_failed,
        python_files_indexed=python.python_files_indexed,
        imports_confirmed=python.imports_confirmed,
        imports_uncertain=python.imports_uncertain,
        imports_unresolved=python.imports_unresolved,
        files_total=len(files),
        files_with_hunks=sum(1 for change in files if change.hunks),
        files_added=sum(1 for change in files if change.status_code == "A"),
        files_modified=sum(1 for change in files if change.status_code == "M"),
        files_deleted=sum(1 for change in files if change.status_code == "D"),
        files_renamed=sum(1 for change in files if change.status_code == "R"),
        files_copied=sum(1 for change in files if change.status_code == "C"),
        files_type_changed=sum(1 for change in files if change.status_code == "T"),
        files_binary=sum(1 for change in files if change.binary),
        files_submodule=sum(1 for change in files if change.submodule),
        files_symlink=sum(1 for change in files if change.symlink),
        files_lfs_pointer=sum(1 for change in files if change.lfs_pointer),
        files_angular_or_typescript=frontend,
        hunks_total=len(hunks),
        deletion_hunks=sum(1 for hunk in hunks if hunk.kind == "deletion"),
        addition_hunks=sum(1 for hunk in hunks if hunk.kind == "addition"),
        edit_hunks=sum(1 for hunk in hunks if hunk.kind == "edit"),
    )


def _is_frontend(path: str | None) -> bool:
    if path is None:
        return False
    lowered = path.lower()
    return lowered.endswith(_FRONTEND_SUFFIXES)


def _file_sort_key(entry: ParsedFile) -> tuple[str, str, str]:
    return (entry.old_path or "", entry.new_path or "", entry.status_code)


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)
