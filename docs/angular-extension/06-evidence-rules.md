# Graph evidence rules

This note describes `dependency_graph.py`. It does not change the CLI report. The graph status is always `partial` and the method is `conservative_lexical`.

## Confirmed

A confirmed edge names one file in the same commit.

| Kind | Evidence |
| --- | --- |
| `ts_import` | A literal relative specifier, or one safe `tsconfig` `paths` entry, resolves to one `.ts`, `.tsx`, `.html`, or `index.ts` / `index.tsx` blob. A `.js` specifier tries the `.ts` and `.tsx` names. |
| `component_template` | `@Component` metadata was a literal object and `templateUrl` resolves to one `.html` blob. |
| `ngmodule_declaration` | `@NgModule` `declarations` names an identifier that this file imports, and that import resolves to one file. |
| `service_reference` | A confirmed import binding ends with `Service`, or `inject(Name)` refers to an imported binding whose name ends with `Service` or whose file ends with `.service.ts`. |
| `ngrx_action_reference` | `ofType(Name)` or NgRx `on(Name)` uses an imported binding, and that import resolves to one file. `on` is read only when `on` is imported from a module path containing `ngrx`. |

`tsconfig.json` is the blob at the commit root. `extends` is not followed. A key with more than one target, more than one `*`, or a `*` on only one side is not used to confirm an edge. `baseUrl` must be a relative path without `..`.

## Not confirmed

| Result | When |
| --- | --- |
| `ambiguous` | The specifier matches more than one candidate. No file is chosen. |
| `unresolved` | A relative specifier or alias matches no blob, or it escapes the repository. |
| `candidate` | An NgModule declaration is not imported, and exactly one class in the commit has that qualname. |
| no edge | A bare package specifier such as `@angular/core` that matches no alias. Dynamic import, a non-literal `templateUrl`, and `styleUrls`. Selector text in HTML. |

## Reverse edges and revisions

`edges_to` lists edges whose target path is the requested file. `walk_forward` follows confirmed targets and records a cycle when a path repeats, without entering that path again. `build_revision_graphs` reads both commits with `list_tree` and `cat-file`. `removed_edges` returns confirmed base edges that are absent on head, including a component file that was deleted.

## Coverage gaps

- This is not a TypeScript program graph. Nested functions, JSX, and non-literal decorators stay as the indexer left them.
- Selector matching, template bindings, and `styleUrls` are not edges.
- `ofType` reads only a single identifier argument, not `ofType(Actions.load)` or a renamed `ofType` import.
- NgModule `imports` do not get their own edge kind. A TypeScript import in that file can still be a `ts_import`.
- `createEffect`, `createAction`, `createReducer`, and `createSelector` are recorded on the file and do not by themselves create an edge.
- Duplicate local import names are not resolved.
- Two classes with the same qualname are not a confirmed declaration target.
- JSON with comments is not a readable `tsconfig.json`.
- The CLI JSON schema does not include this graph yet.
