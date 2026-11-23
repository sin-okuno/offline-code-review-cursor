# Review context quality

This note describes the additive fields and limits added for review-context selection. Existing JSON fields, the default 2000-line budget, and `coverage.angular_semantic = "out_of_scope"` stay as they are.

## Changed lines and review units

Changed TypeScript and HTML lines are selected before unchanged dependency excerpts. A changed range is widened to the smallest enclosing symbol when that symbol still fits `--max-lines-per-excerpt` (default 200). A multi-line `const`, `let`, or `var` initializer is that kind of symbol, so a change inside `createEffect` or `createSelector` keeps the declared name. When the changed symbol is a method and its class still fits the same cap, the excerpt uses the class span, which keeps constructor evidence such as `Validators` with the changed method. A class larger than the cap stays on the smaller symbol. If a span would exceed the cap, the excerpt stays on the changed lines. Up to two unchanged lines between changed ranges in the same file are kept when the combined span still fits that cap. A deletion that adds no head line is matched to the head declaration by path, qualname, and kind. When that declaration remains, the head excerpt uses its span, so the range is not `0-0`. When that declaration is gone, the report records `TS_SYMBOL_ABSENT` and does not invent a head body. `TS_SYMBOL_MATCH_AMBIGUOUS` and `TS_SYMBOL_MATCH_UNCERTAIN` mean the head declaration was not chosen. The TypeScript extractor version is `5`.

`angular_context.selected` is the first review unit and remains inside `--max-total-lines` (default 2000). Changed-file lines that do not fit that first unit are packed into `review_units` `unit-2`, `unit-3`, and so on. Each later unit is also limited to the same line budget. Unchanged dependency excerpts that do not fit the first unit are omitted and are not copied into later units.

`changed_line_files` counts changed lines of TypeScript and HTML paths that appear in any review unit:

- `changed_lines_total` is the number of changed lines in that revision.
- `changed_lines_included` counts those lines present in any unit.
- `changed_lines_omitted` is the remainder.

`CHANGED_CONTEXT_OMITTED` still means lines are absent from the first unit. For a changed file, those lines are in a later unit when the unit split succeeded. `--max-total-lines 0` keeps no excerpt lines and does not create a later unit.

## TypeScript config

`tsconfig.json` is read as JSONC. Line comments, block comments, and trailing commas are removed before parsing. A `//` inside a double-quoted string is kept.

A relative `extends` that starts with `./` and stays inside the repository is followed, at most three times, from the directory of the config that names it. The child `compilerOptions` override the parent. These forms are not followed and produce `TSCONFIG_EXTENDS_UNSUPPORTED`:

- no reader was supplied
- a package name
- a URL or absolute path
- a path containing `..`
- a path that is not a `.json` file

`target_not_found` means the import path was not present in that commit. It is not, by itself, a statement that the application fails to build. A path alias, a generated file, or a file outside the scanned commit can produce the same status.

## Coverage wording

`ANGULAR_SEMANTIC_OUT_OF_SCOPE` remains when frontend files are in the diff. Its message now says that `angular_context` is a partial lexical result. `coverage.ngrx`, `template_bindings`, and `typescript_semantic` stay `partial` when that lexical scan runs. They are not marked complete.

`WORKTREE_DIRTY` means the work tree differs from `HEAD`. The report uses the resolved base and head commits. Uncommitted content is not part of the diff.

## NgModule providers

An identifier in `@NgModule({ providers: [...] })` becomes a `service_reference` edge when that name is one imported binding and the module resolves to one file. `useClass`, `useFactory`, and other object providers are not parsed. A provider that is not a single import is reported unresolved rather than guessed.

Provider names are part of the scan. The current extractor version is `5`, so older cache files are rebuilt.

## Limits

- Full TypeScript type checking is still out of scope.
- Selector-tag matches stay candidates.
- `createActionGroup` and `createFeature` have no dedicated edge.
- An effect-to-HTTP call is recorded only when the service injection is visible. The HTTP URL itself is not inferred from a method name.
- `styleUrls` are not edges.
- Parent-directory `tsconfig` extends, package extends, and `node_modules` are not read.
- Ranking among unchanged files is unchanged: a large changed file can still fill the first unit before a small unchanged dependency. Those unchanged lines stay omitted from every unit.
