"""One-pass file-level import index and reverse lookup.

Each parsed module is visited once. Module names are inserted into a dict,
and each import edge is inserted into a reverse dict in that same walk.
Changed files then read those dicts. They do not scan the tree again.

Overlapping Python roots are not collapsed. A path that matches more than one
root keeps every module name and is an uncertain match. Imports of modules
that are not present under the configured roots stay unresolved. ``from module
import name`` adds an uncertain submodule candidate because ``name`` may be an
attribute rather than a module. Literal ``import_module`` and ``__import__``
calls are heuristic candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from offline_review.models import (
    AffectedSymbol,
    FileChange,
    ImportRelationship,
    UsageError,
    Warning,
)
from offline_review.python_indexer import (
    ParseResult,
    RawImport,
    is_python_path,
    map_affected_symbols,
)

_DIRECT_KINDS = frozenset({"direct", "parent"})


@dataclass(frozen=True, slots=True)
class ModuleIdentity:
    """One path's importable names. Each candidate is ``(module_name, package)``."""

    candidates: tuple[tuple[str, str], ...]
    is_package: bool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _package in self.candidates)

    @property
    def certainty(self) -> str:
        if not self.candidates:
            return "unresolved"
        if len(self.candidates) == 1:
            return "confirmed"
        return "uncertain"


@dataclass(frozen=True, slots=True)
class IndexedModule:
    path: str
    commit: str
    result: ParseResult


@dataclass(frozen=True, slots=True)
class _Edge:
    importer_path: str
    importer_module: str | None
    module_name: str | None
    target_paths: tuple[str, ...]
    certainty: str
    kind: str
    line: int
    evidence: str


@dataclass(frozen=True, slots=True)
class ImportIndex:
    """Lookup tables produced by a single walk of one commit's Python files."""

    identities: dict[str, ModuleIdentity]
    by_module: dict[str, tuple[str, ...]]
    confirmed_modules: frozenset[str]
    outgoing: dict[str, tuple[_Edge, ...]]
    reverse: dict[str, tuple[_Edge, ...]]

    @classmethod
    def build(cls, modules: dict[str, IndexedModule], roots: tuple[str, ...]) -> ImportIndex:
        identities = {
            path: module_identity(path, roots)
            for path in modules
            if modules[path].result.status == "parsed"
        }
        by_module: dict[str, list[str]] = {}
        for path in sorted(identities):
            for name in identities[path].names:
                by_module.setdefault(name, []).append(path)
        frozen_modules = {name: tuple(paths) for name, paths in by_module.items()}
        confirmed = frozenset(
            name
            for name, paths in frozen_modules.items()
            if len(paths) == 1 and identities[paths[0]].certainty == "confirmed"
        )
        outgoing: dict[str, list[_Edge]] = {}
        reverse: dict[str, list[_Edge]] = {}
        for path in sorted(modules):
            module = modules[path]
            if module.result.status != "parsed":
                continue
            identity = identities[path]
            edges = _edges_for_module(module, identity, frozen_modules, confirmed)
            outgoing[path] = edges
            for edge in edges:
                if edge.module_name:
                    reverse.setdefault(edge.module_name, []).append(edge)
        return cls(
            identities=identities,
            by_module=frozen_modules,
            confirmed_modules=confirmed,
            outgoing={path: tuple(edges) for path, edges in outgoing.items()},
            reverse={name: tuple(edges) for name, edges in reverse.items()},
        )


@dataclass(frozen=True, slots=True)
class PythonAnalysis:
    files: tuple[FileChange, ...]
    relationships: tuple[ImportRelationship, ...]
    warnings: tuple[Warning, ...]
    python_files_parsed: int
    python_files_failed: int
    python_files_indexed: int
    imports_confirmed: int
    imports_uncertain: int
    imports_unresolved: int
    changed_python: int
    changed_python_failed: int
    roots_configured: bool


def normalize_python_roots(values: list[str]) -> tuple[str, ...]:
    """Return sorted unique repository-relative roots. ``.`` is the repo root."""

    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or value == "" or value != value.strip():
            raise UsageError(
                "python root must be a non-empty repository-relative path; use . for the repository root"
            )
        text = value.replace("\\", "/")
        if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
            raise UsageError("python root must be repository-relative, not absolute")
        parts: list[str] = []
        for part in text.split("/"):
            if part in {"", "."}:
                continue
            if part == "..":
                raise UsageError("python root must not contain ..")
            parts.append(part)
        normalized.append("." if not parts else "/".join(parts))
    return tuple(sorted(set(normalized)))


def module_identity(path: str, roots: tuple[str, ...]) -> ModuleIdentity:
    package_file = path.endswith("/__init__.py") or path == "__init__.py"
    candidates: set[tuple[str, str]] = set()
    for root in roots:
        prefix = "" if root == "." else root
        if prefix and not path.startswith(prefix + "/"):
            continue
        relative = path if prefix == "" else path[len(prefix) + 1 :]
        module_name = _module_name(relative)
        if module_name is None:
            continue
        package = module_name if package_file else _parent_module(module_name)
        candidates.add((module_name, package))
    return ModuleIdentity(tuple(sorted(candidates)), package_file)


def analyze_python(
    files: tuple[FileChange, ...],
    modules_by_commit: dict[str, dict[str, IndexedModule]],
    roots: tuple[str, ...],
) -> PythonAnalysis:
    indexes = {
        commit: ImportIndex.build(modules, roots) for commit, modules in modules_by_commit.items()
    }
    warnings: list[Warning] = []
    parsed = 0
    failed = 0
    indexed = 0
    for commit, modules in sorted(modules_by_commit.items()):
        indexed += len(modules)
        for path, module in sorted(modules.items()):
            if module.result.status == "parsed":
                parsed += 1
            elif module.result.status in {"syntax_error", "decode_error", "binary_source", "too_large"}:
                failed += 1
                warnings.append(
                    Warning(
                        code=_status_warning(module.result.status),
                        message=module.result.error or "Python file was not parsed.",
                        path=path,
                        commit=commit,
                    )
                )
            if module.result.dynamic_nonliteral:
                warnings.append(
                    Warning(
                        code="PYTHON_DYNAMIC_IMPORT",
                        message=(
                            "A non-literal import_module or __import__ call was seen. "
                            "Its target was not inferred."
                        ),
                        path=path,
                        commit=commit,
                    )
                )
    if roots == () and indexed:
        warnings.append(
            Warning(
                code="PYTHON_ROOTS_NOT_CONFIGURED",
                message=(
                    "No --python-root was given, so module names were not assigned "
                    "and import targets were not matched to files. "
                    "Pass each repository-relative import root explicitly; use . for the repository root."
                ),
            )
        )
    relationships: list[ImportRelationship] = []
    updated: list[FileChange] = []
    changed_python = 0
    changed_failed = 0
    for change in files:
        old_module = _module_for(change.old_path, change.diff_base_commit, modules_by_commit)
        new_module = _module_for(change.new_path, change.head_commit, modules_by_commit)
        old_status, old_symbols = _side(change.old_path, old_module, change.old_changed_lines)
        new_status, new_symbols = _side(change.new_path, new_module, change.new_changed_lines)
        if old_status not in {"not_python", "absent"} or new_status not in {"not_python", "absent"}:
            changed_python += 1
        failed_status = {"syntax_error", "decode_error", "binary_source", "too_large", "missing"}
        if old_status in failed_status or new_status in failed_status:
            changed_failed += 1
        updated.append(
            replace(
                change,
                old_python_status=old_status,
                new_python_status=new_status,
                old_affected_symbols=old_symbols,
                new_affected_symbols=new_symbols,
            )
        )
        relationships.extend(_relationships_for_side(change, old_module, indexes, side="old"))
        relationships.extend(_relationships_for_side(change, new_module, indexes, side="new"))
    deduped = tuple(sorted(set(relationships), key=_relationship_key))
    counts = {"confirmed": 0, "uncertain": 0, "unresolved": 0}
    for item in deduped:
        counts[item.certainty] = counts.get(item.certainty, 0) + 1
    return PythonAnalysis(
        files=tuple(updated),
        relationships=deduped,
        warnings=tuple(warnings),
        python_files_parsed=parsed,
        python_files_failed=failed,
        python_files_indexed=indexed,
        imports_confirmed=counts["confirmed"],
        imports_uncertain=counts["uncertain"],
        imports_unresolved=counts["unresolved"],
        changed_python=changed_python,
        changed_python_failed=changed_failed,
        roots_configured=bool(roots),
    )


def _edges_for_module(
    module: IndexedModule,
    identity: ModuleIdentity,
    by_module: dict[str, tuple[str, ...]],
    confirmed_modules: frozenset[str],
) -> list[_Edge]:
    edges: list[_Edge] = []
    for raw in module.result.imports:
        if raw.form == "import":
            for name in raw.names:
                edges.extend(
                    _materialize(
                        module,
                        identity,
                        name,
                        "import",
                        0,
                        raw,
                        True,
                        by_module,
                        confirmed_modules,
                    )
                )
            continue
        if raw.level == 0:
            edges.extend(
                _materialize(
                    module,
                    identity,
                    raw.module,
                    raw.form,
                    0,
                    raw,
                    True,
                    by_module,
                    confirmed_modules,
                )
            )
            continue
        if not identity.candidates:
            edges.extend(
                _materialize(
                    module,
                    identity,
                    raw.module,
                    raw.form,
                    raw.level,
                    raw,
                    False,
                    by_module,
                    confirmed_modules,
                    package="",
                )
            )
            continue
        for _name, package in identity.candidates:
            edges.extend(
                _materialize(
                    module,
                    identity,
                    raw.module,
                    raw.form,
                    raw.level,
                    raw,
                    identity.certainty == "confirmed",
                    by_module,
                    confirmed_modules,
                    package=package,
                )
            )
    return edges


def _materialize(
    module: IndexedModule,
    identity: ModuleIdentity,
    imported: str | None,
    form: str,
    level: int,
    raw: RawImport,
    identity_certain: bool,
    by_module: dict[str, tuple[str, ...]],
    confirmed_modules: frozenset[str],
    package: str = "",
) -> list[_Edge]:
    importer_module = identity.names[0] if len(identity.names) == 1 else None
    if form == "heuristic":
        targets = ((imported, "heuristic", "uncertain"),) if imported else ()
    elif level == 0 and form == "import":
        targets = tuple(_absolute_targets(imported or "", raw.names, from_import=False))
    elif level == 0:
        targets = tuple(_absolute_targets(imported or "", raw.names, from_import=True))
    else:
        resolved = _resolve_relative(package, level, imported)
        if resolved is None:
            return [
                _Edge(
                    importer_path=module.path,
                    importer_module=importer_module,
                    module_name=None,
                    target_paths=(),
                    certainty="unresolved",
                    kind="relative",
                    line=raw.line,
                    evidence=raw.evidence,
                )
            ]
        base_certainty = "confirmed" if identity_certain else "uncertain"
        targets = tuple(_relative_targets(resolved, raw, base_certainty, imported_module=imported))
    edges: list[_Edge] = []
    for module_name, kind, certainty in targets:
        if not module_name:
            edges.append(
                _Edge(
                    importer_path=module.path,
                    importer_module=importer_module,
                    module_name=None,
                    target_paths=(),
                    certainty="unresolved",
                    kind=kind,
                    line=raw.line,
                    evidence=raw.evidence,
                )
            )
            continue
        paths = by_module.get(module_name, ())
        edges.append(
            _Edge(
                importer_path=module.path,
                importer_module=importer_module,
                module_name=module_name,
                target_paths=paths,
                certainty=_match_certainty(certainty, kind, paths, module_name, confirmed_modules),
                kind=kind,
                line=raw.line,
                evidence=raw.evidence,
            )
        )
    return edges


def _absolute_targets(
    module_name: str, names: tuple[str, ...], *, from_import: bool
) -> list[tuple[str, str, str]]:
    if module_name == "":
        return []
    parts = module_name.split(".")
    targets = [
        (".".join(parts[:index]), "direct" if index == len(parts) else "parent", "confirmed")
        for index in range(1, len(parts) + 1)
    ]
    if from_import:
        for name in names:
            if name != "*":
                targets.append((f"{module_name}.{name}", "possible_submodule", "uncertain"))
    return targets


def _relative_targets(
    resolved: str,
    raw: RawImport,
    certainty: str,
    *,
    imported_module: str | None,
) -> list[tuple[str, str, str]]:
    parts = resolved.split(".")
    targets = [
        (".".join(parts[:index]), "direct" if index == len(parts) else "parent", certainty)
        for index in range(1, len(parts) + 1)
    ]
    extra_names = raw.names if imported_module is None else raw.names
    for name in extra_names:
        if name != "*":
            targets.append((f"{resolved}.{name}", "possible_submodule", "uncertain"))
    return targets


def _resolve_relative(package: str, level: int, module: str | None) -> str | None:
    if level <= 0:
        return module
    if package == "":
        return None
    bits = package.rsplit(".", level - 1)
    if len(bits) < level:
        return None
    base = bits[0]
    if module:
        return f"{base}.{module}"
    return base


def _match_certainty(
    certainty: str,
    kind: str,
    paths: tuple[str, ...],
    module_name: str,
    confirmed_modules: frozenset[str],
) -> str:
    if not paths:
        return "unresolved"
    if kind not in _DIRECT_KINDS:
        return "uncertain"
    if certainty != "confirmed" or len(paths) != 1 or module_name not in confirmed_modules:
        return "uncertain"
    return "confirmed"


def _module_name(relative: str) -> str | None:
    if relative.endswith("/__init__.py"):
        relative = relative[: -len("/__init__.py")]
    elif relative == "__init__.py":
        return None
    elif relative.endswith(".py"):
        relative = relative[: -len(".py")]
    else:
        return None
    if relative == "":
        return None
    return relative.replace("/", ".")


def _parent_module(module_name: str) -> str:
    if "." not in module_name:
        return ""
    return module_name.rsplit(".", 1)[0]


def _module_for(
    path: str | None,
    commit: str,
    modules_by_commit: dict[str, dict[str, IndexedModule]],
) -> IndexedModule | None:
    if path is None or not is_python_path(path):
        return None
    return modules_by_commit.get(commit, {}).get(path)


def _side(
    path: str | None,
    module: IndexedModule | None,
    lines: tuple[int, ...],
) -> tuple[str, tuple[AffectedSymbol, ...]]:
    if path is None:
        return "absent", ()
    if not is_python_path(path):
        return "not_python", ()
    if module is None:
        return "missing", ()
    symbols = ()
    if module.result.status == "parsed":
        symbols = map_affected_symbols(module.result.symbols, lines, line_count=module.result.line_count)
    return module.result.status, symbols


def _relationships_for_side(
    change: FileChange,
    module: IndexedModule | None,
    indexes: dict[str, ImportIndex],
    *,
    side: str,
) -> list[ImportRelationship]:
    if module is None:
        return []
    path = module.path
    commit = change.diff_base_commit if side == "old" else change.head_commit
    index = indexes.get(commit)
    if index is None:
        return []
    identity = index.identities.get(path)
    changed_module = None
    if identity is not None and len(identity.names) == 1:
        changed_module = identity.names[0]
    relationships: list[ImportRelationship] = []
    for edge in index.outgoing.get(path, ()):
        if edge.target_paths:
            for target in edge.target_paths:
                relationships.append(
                    _relationship(
                        direction="outgoing",
                        changed_path=path,
                        changed_commit=commit,
                        changed_module=changed_module,
                        other_path=target,
                        other_module=edge.module_name,
                        certainty=edge.certainty,
                        kind=edge.kind,
                        line=edge.line,
                        evidence=edge.evidence,
                    )
                )
        else:
            relationships.append(
                _relationship(
                    direction="outgoing",
                    changed_path=path,
                    changed_commit=commit,
                    changed_module=changed_module,
                    other_path=None,
                    other_module=edge.module_name,
                    certainty="unresolved",
                    kind=edge.kind,
                    line=edge.line,
                    evidence=edge.evidence,
                )
            )
    if identity is None:
        return relationships
    for name in identity.names:
        for edge in index.reverse.get(name, ()):
            if edge.importer_path == path and edge.kind == "direct" and name in identity.names:
                # Self-imports are already represented as outgoing edges.
                continue
            certainty = edge.certainty
            if identity.certainty != "confirmed":
                certainty = "uncertain" if certainty != "unresolved" else "unresolved"
            if len(index.by_module.get(name, ())) != 1 and certainty == "confirmed":
                certainty = "uncertain"
            relationships.append(
                _relationship(
                    direction="incoming",
                    changed_path=path,
                    changed_commit=commit,
                    changed_module=name,
                    other_path=edge.importer_path,
                    other_module=edge.importer_module,
                    certainty=certainty,
                    kind=edge.kind,
                    line=edge.line,
                    evidence=edge.evidence,
                )
            )
    return relationships


def _relationship(**kwargs: object) -> ImportRelationship:
    return ImportRelationship(
        direction=str(kwargs["direction"]),
        changed_path=str(kwargs["changed_path"]),
        changed_commit=str(kwargs["changed_commit"]),
        changed_module=kwargs["changed_module"] if isinstance(kwargs["changed_module"], str) else None,
        other_path=kwargs["other_path"] if isinstance(kwargs["other_path"], str) else None,
        other_module=kwargs["other_module"] if isinstance(kwargs["other_module"], str) else None,
        certainty=str(kwargs["certainty"]),
        kind=str(kwargs["kind"]),
        line=int(kwargs["line"]),  # type: ignore[arg-type]
        evidence=str(kwargs["evidence"]),
    )


def _relationship_key(item: ImportRelationship) -> tuple[object, ...]:
    return (
        item.direction,
        item.changed_path,
        item.changed_commit,
        item.changed_module or "",
        item.other_path or "",
        item.other_module or "",
        item.certainty,
        item.kind,
        item.line,
        item.evidence,
    )


def _status_warning(status: str) -> str:
    return {
        "syntax_error": "PYTHON_SYNTAX_ERROR",
        "decode_error": "PYTHON_DECODE_ERROR",
        "binary_source": "PYTHON_BINARY_SOURCE",
        "too_large": "PYTHON_FILE_TOO_LARGE",
    }.get(status, "PYTHON_PARSE_FAILED")
