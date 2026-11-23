# Phase 16 current state

Audit date: 2026-11-21. This note records the code that existed before the evaluation package. It does not change `analyze`.

## Package

`src/offline_review/` is a flat package. Version `0.4.0`. JSON schema `1.1.0`. Nested Angular schema `"1"`. Phase label `2-python`. Runtime dependencies are the Python 3.13 standard library and the installed Git binary.

Modules used by `analyze`:

- `cli.py` parses `analyze` and `validate-context`.
- `git_adapter.py` reads local Git objects. It does not clone, fetch, pull, push, or run repository hooks.
- `diff_parser.py` records old and new line ranges.
- `python_indexer.py` and `dependency_index.py` parse Python with the standard-library AST.
- `typescript_index.py` is a conservative lexical scanner. Extractor version is `4`. It is not a TypeScript type checker.
- `dependency_graph.py` builds confirmed or candidate edges for imports, templates, NgModule declarations, providers that are a single imported name, and explicit NgRx calls.
- `context_selector.py` ranks excerpts. Defaults: depth 2, 40 files, 1 MiB, 200 lines per excerpt, 2000 lines in the first review unit. Changed-file overflow goes to `unit-2` and later. `--max-total-lines 0` keeps no excerpt lines.
- `context_builder.py` assembles the report. `coverage.angular_semantic` stays `out_of_scope`. `analysis_complete` stays false when frontend files are present.
- `reporters.py` writes `review-context.json` and `review-context.md`.
- `context_validator.py` compares a context file with a golden file. That golden format is for the product-tree sample, not for Phase 16 case ids.

## CLI

```text
python -m offline_review analyze --repo PATH --base REV --head REV --comparison merge-base|direct --output DIR
python -m offline_review validate-context --context FILE --golden FILE --output FILE.json [--require-changed-lines]
```

Exit 0 means the report was written (`validate-context` exit 0 also means the gate passed). Exit 1 is a Git, revision, or diff failure, or a failed validation gate. Exit 2 is usage. There was no `evaluate-context` or `evaluate-findings` command.

## Angular limits

The scanner records imports, classes, functions, methods, `@Component` metadata, `@NgModule` declarations and imports, and explicit `dispatch`, `ofType`, `createEffect`, `on`, and `select` calls. `coverage.ngrx`, `template_bindings`, and `typescript_semantic` stay `partial` or `not_applicable`. They are never `complete`. `ANGULAR_SEMANTIC_OUT_OF_SCOPE` remains when frontend files change. Its message says the Angular section is a partial lexical result.

`changed_line_files` counts changed TypeScript and HTML lines across every review unit. `coverage.lines_omitted` counts excerpt lines that did not fit the first unit. Those two numbers are different.

## Tests and docs

Tests are `unittest` modules under `tests/`. Synthetic repositories are created with `tests/support.py`. No third-party test library is installed. Existing docs `docs/01` through `docs/10` and `docs/angular-extension/` stay as they are. Phase 16 documents are new files under `docs/phase16/`.

## Smallest addition

The evaluation reads report JSON that `analyze` already writes. It does not import the reviewed application, does not call a model, and does not add a package. New commands are subcommands on the existing parser. Ground truth and AI findings use schema `1.0`, separate from schema `1.1.0`.
