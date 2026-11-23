# Angular extension implementation plan

This plan records the audit of the working tool. It does not change CLI behavior, Git access, Python analysis, or the JSON schema. Later prompts implement the phases below. Existing files under `.cursor/rules/`, `docs/`, and `prompts/` stay in place.

Cursor is used only to develop this tool against synthetic fixtures. A review of a target repository runs the finished CLI locally. That run does not call Cursor, a remote model, or the network.

## Architecture delta

The package is `src/offline_review`. Current modules:

| Module | Responsibility that stays |
| --- | --- |
| `git_adapter.py` | Allowlisted local Git: `rev-parse`, `merge-base`, `diff`, `cat-file`, `ls-files`, `ls-tree`. `resolve_commit`, `diff_raw`, `diff_patch`, `list_tree`, `read_blobs`, `read_blob_metadata`, `read_bytes`. |
| `diff_parser.py` | Old and new line ranges for both revisions. |
| `python_indexer.py` | Python AST. `PARSER_VERSION` is `"1"`. Files above 1,048,576 bytes are not parsed. |
| `dependency_index.py` | One-pass Python import index and reverse map. |
| `parse_cache.py` | Optional Python parse cache keyed by commit, parser version, and `--python-root`. Cap 64 MiB. |
| `context_builder.py` | Builds `ReviewContext`. Frontend suffixes are `.ts`, `.tsx`, `.html`, `.scss`, `.less`, `.css`. |
| `reporters.py` | Sorted JSON and Markdown. `redact_secrets` on warnings, errors, and import evidence. |
| `models.py` | Schema `1.1.0`, phase `2-python`, package `0.4.0`. |
| `cli.py` | `analyze` with required `--repo`, `--base`, `--head`, `--output`, `--comparison`, and optional `--python-root`, `--cache`. |

Extension points, without renaming these modules:

- Read both revisions through `GitAdapter.list_tree` and `GitAdapter.read_blobs`. Do not add Git subcommands and do not read the work tree.
- Parse TypeScript and HTML in new modules. `context_builder.build_context` calls them after the existing Python pass and passes the result to the reporters.
- Add dataclasses in `models.py`. Do not change the meaning or JSON names of existing fields.
- Add one object, `angular_context`, in `render_json`. Keep every current key.
- Do not store TypeScript parses in `ParseCache`. A later cache, if added, uses its own extractor version so a Python record cannot be read as TypeScript.

`docs/angular-extension/03-architecture.md` suggests new modules `typescript_index.py`, `angular_index.py`, `dependency_graph.py`, `context_selector.py`, and `context_report.py`. Those names are for new files. They are not renames.

`.cursor/rules/30-architecture.mdc` still says Angular Phase 1 collects diffs only and must not pretend to have an AST or NgRx graph. `.cursor/rules/40-angular-scope.mdc` allows a later conservative lexical index as an additive schema change. This plan follows both: the existing coverage fields stay `angular_semantic=out_of_scope` and `typescript_html=diff_only` or `none`, and any lexical result lives only under `angular_context` with status `partial` or `candidate_based`. Full TypeScript AST, template type-checking, and a runtime call graph stay unsupported.

## Fields that must be preserved

`docs/angular-extension/01-current-state.md` describes one supplied direct-diff excerpt. Those commits and paths are not in this workspace and are not a fixture. The excerpt's coverage names match the reporter. Its `python_ast=not_applicable` matches the code path where no changed Python file exists. The illustrative `schema_version` `"1.1"` in `03-architecture.md` does not match the tool. The tool emits `"1.1.0"`.

Top-level JSON keys today, in sorted order: `comparison`, `coverage`, `errors`, `files`, `import_relationships`, `python_roots`, `repository`, `schema_version`, `tool`, `warnings`.

`comparison` keys: `base_commit`, `description`, `diff_base_commit`, `head_commit`, `merge_base_commit`, `merge_bases`, `mode`, `requested_base`, `requested_head`, `semantics`.

`coverage` keys include the excerpt's `files_total`, `files_added`, `files_modified`, `hunks_total`, `addition_hunks`, `edit_hunks`, `deletion_hunks`, `analysis_complete`, `partial`, `partial_reasons`, `angular_semantic`, `typescript_html`, and `python_ast`. Also preserved: `dependency_index`, the other `files_*` counters, `files_angular_or_typescript`, `imports_confirmed`, `imports_uncertain`, `imports_unresolved`, and the three `python_files_*` counters.

Each file keeps `status`, `status_code`, `similarity`, old and new path, mode, blob, both commit ids, binary, submodule, symlink, LFS flags, changed lines and ranges, hunks, Python status, and affected symbols. Each hunk keeps `kind`, counts, line lists, ranges, and `evidence`. Python import relationships stay a separate array.

Tests pin this behavior:

- `tests/test_cli_integration.py` requires schema `1.1.0`, phase `2-python`, `angular_semantic=out_of_scope`, and warning code `ANGULAR_SEMANTIC_OUT_OF_SCOPE`.
- `tests/test_hardening.py` requires the same schema, the same coverage value, partial reason `angular_semantic_out_of_scope`, and the same warning code on the synthetic Angular fixture.

Those assertions stay true. `angular_context` is additional. It is absent until the report phase adds it. When it is added, `schema_version` stays `1.1.0` so those assertions remain valid. The nested object carries its own `schema` string. Changing the top-level schema string requires a separate approval because it edits existing tests.

`angular_semantic` remains `out_of_scope` even after the lexical graph exists. The new object states the lexical limit. Changing the old field would make the current warning false and would violate the additive contract.

## Interfaces

Proposed, not implemented in this step.

`scan_typescript(data: bytes) -> TsFileIndex` in `typescript_index.py`. The result has `status` (`parsed`, `unknown`, `too_large`, `binary`, `skipped`), `line_count`, `imports`, `exports`, `symbols`, `component` metadata, and warnings. Symbols are classes, functions, and methods whose braces are unambiguous after comments, strings, and template literals are masked. Each symbol has a qualname, kind, start line, and end line. Imports keep the raw clause, line, specifiers, and module specifier. Component metadata keeps literal `selector`, `templateUrl`, `styleUrls`, and `standalone` only when the decorator argument is an object of literals. Anything else is `unknown` plus a warning.

`scan_html(data: bytes) -> HtmlFileIndex` records the path's text and line count. It does not confirm selector or binding edges.

`resolve_graph(trees) -> AngularGraph` in `dependency_graph.py`. A node is commit SHA, repo-relative path, and optional symbol. An edge has `kind`, `confidence` (`confirmed` or `candidate`), `evidence`, and `source_revision`. Confirmed kinds are a unique literal relative import, an explicit `templateUrl` that names one Git blob, and an NgModule `declarations` entry that names one indexed class. Candidate kinds are selector text, a symbol-name match, and an explicit NgRx `Action`, `Effect`, `Reducer`, `Selector`, or service token that is not a unique definition. Dynamic import, a computed specifier, and a non-literal `templateUrl` produce no edge. `styleUrls` stay metadata and do not become edges.

`select_context(graph, changes, budget) -> Selection` orders changed files, confirmed forward edges, confirmed reverse edges, then candidates. Deletions use the diff-base commit. Cycles are skipped. Truncation appends to `omitted` and a partial reason.

`angular_context_payload(selection) -> dict` is the only new JSON object. Keys: `schema`, `status`, `analysis_method` (`conservative_lexical`), `nodes`, `edges`, `selected`, `omitted`, `warnings`, `coverage`. Each `selected` item has `revision`, `commit`, `path`, `line_start`, `line_end`, `reason`, `confidence`, and a bounded `content` string passed through `redact_secrets`. Markdown gains a section after the existing import relationships. Existing sections stay.

Budget defaults, used only when the later CLI prompt adds them: depth 2, 40 files, 1,048,576 indexed bytes per file, 200 lines per excerpt, 1,048,576 excerpt bytes in the whole report. Omitting the new flags keeps these defaults. They are explicit in help text, not hidden behavior of the current CLI. The current CLI gains no arguments in the index or graph phases.

## Phased tasks

1. TypeScript index. Implement `scan_typescript` and tests on strings and synthetic blobs. Do not change CLI output. Follow prompt `06-ts-index.md`.
2. Graph. Resolve relative imports, `index.ts`, and a `tsconfig.json` `paths` map only when the mapping is a literal prefix without a computed expression. Build both revisions. Follow prompt `07-angular-graph.md`.
3. Report. Add `angular_context`, excerpts, and the optional budget flags. Keep old keys and old tests green. Follow prompt `08-context-report.md`.
4. Security regression. Adversarial comments, strings, malformed TypeScript, and the existing Python suite. Write a new `docs/06-validation-report.md` only in that prompt. Follow prompt `09-security-regression.md`.
5. Scale. A generated Angular fixture of about 100k lines, measured wall clock and peak RSS, and a separate cache key. Write a new `docs/07-benchmark.md` only in that prompt. Follow prompt `10-performance.md`.

## Risks

- A lexical scan can still mis-read generics, decorators, or JSX. Those files must be `unknown`, not confirmed edges.
- `tsconfig` `extends` and wildcard paths are easy to resolve incorrectly. Unsupported forms are warnings.
- Reusing `ParseCache` would mix Python records with TypeScript records. Do not.
- Excerpts can copy secrets from fixtures. Redact with the existing helper and keep the size cap.
- `list_tree` does not enter submodules. A frontend file inside a submodule stays a submodule diff, as it does today.
- Windows path case and CRLF change line numbers if the scanner splits on `\n` only while the diff counts Git's lines. The scanner must use the same newline split as the diff parser.
- The parent Git repository is `C:\work\00_src`. This plan does not commit or push.

## Test matrix

| Case | Phase | Expected |
| --- | --- | --- |
| Existing `python -m unittest discover -s tests -v` | every phase | Same pass count as the baseline below, plus new tests. No existing assertion edited to hide a failure. |
| Comment or string that looks like `import` or `@Component` | index | No import and no component metadata. |
| Multiline import, type-only import, re-export | index | Specifiers and the module string are kept. |
| Relative `./` and `../`, `.ts` and `index.ts` | graph | Confirmed only when one blob matches. |
| Two `paths` targets or an unknown alias | graph | Unresolved or ambiguous, with a warning. No confirmed edge. |
| `@Component` `templateUrl` | graph | Confirmed edge to that HTML blob on the same commit. |
| NgModule `declarations` with one class | graph | Confirmed. A name with two owners is candidate or unresolved. |
| Explicit NgRx symbol and service token | graph | Candidate unless one definition is unique, then the plan's confirmed rule applies only to imports, `templateUrl`, and declarations. |
| Selector text in HTML | graph | Candidate, never confirmed. |
| Add, edit, delete, rename, deletion-only | report | Old side kept for deletions. Both commit SHAs present. |
| Unicode path, CRLF, zero-line hunk | report | Path and line numbers match the existing diff. |
| Binary, symlink, submodule, file over the byte cap | index | Skipped with a warning. No symbols. |
| Cycle and budget overflow | report | `omitted` lists the dropped items. `analysis_complete` stays false. |
| Two identical runs | report | JSON bytes match, including `angular_context`. |
| Python fixture from the current suite | every phase | `angular_semantic` remains `out_of_scope` where those tests require it. |

## Unresolved questions

- Should a later approved schema bump change `coverage.angular_semantic` from `out_of_scope` to `partial`? This plan says no until that approval, because current tests and the additive contract require the old value.
- Is a unique NgRx class reference allowed to become `confirmed`, or do all NgRx edges stay `candidate`? This plan keeps them `candidate` except where they are also a unique relative import.
- Which `tsconfig` files are in scope beyond a root `tsconfig.json`? `extends` is out of scope until specified.
- Are `styleUrls` ever edges? This plan records them and does not link them.
- The numeric budgets above are proposals for the report phase. They are not CLI behavior yet.
