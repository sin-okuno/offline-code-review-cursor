# Angular impact analysis

This document describes the lexical impact analysis added on top of the existing TypeScript index and dependency graph. It does not replace `docs/06-validation-report.md`. The top-level JSON schema stays `1.1.0`. `coverage.angular_semantic` stays `out_of_scope`. Nested `angular_context.schema` stays `"1"`.

The extractor is not a TypeScript or Angular compiler. Confirmed edges require a literal that resolves to one file, or a template binding whose component link is itself literal and whose name matches one member of that component.

## Data model

Existing `angular_context` fields stay. Added fields:

| Field | Meaning |
| --- | --- |
| `coverage.ngrx` | `partial` when an NgRx edge was recorded, otherwise `not_applicable`. |
| `coverage.template_bindings` | `partial` when an HTML blob or a literal inline template was scanned. `unsupported` when none was scanned. Never `supported`. |
| `coverage.line_budget`, `lines_selected`, `lines_omitted` | Total excerpt-line budget and what was kept or dropped. |
| `coverage.base_commit`, `head_commit` | The diff base and head used for the two graphs. |
| `changed_symbols` | Symbols whose line range overlaps a changed line on that revision. |
| `unresolved` | Edges with confidence `unresolved`, including `resolution_reason`. |
| `edges[].resolution_reason` | Empty when confirmed. Otherwise one of the reasons below. |
| `selected[].score` | Stable priority score. The reason strings are unchanged. |
| `omitted[].missing_lines`, `omitted[].resolution_reason` | Lines not copied, and `budget_exceeded` when a budget caused the omission. |

`resolution_reason` values:

| Reason | When |
| --- | --- |
| `target_not_found` | A relative specifier or a simple template name matched no file or member. |
| `ambiguous_resolution` | More than one file or component matched. |
| `heuristic` | The template expression is not a single call or identifier, or a selector matched one component. |
| `budget_exceeded` | The file was left out because a count or line budget was reached. |

`unsupported_import` and `parse_failed` are reserved. A bare package import stays external and produces no edge. A file that fails the lexical scan produces an index warning and no guessed edge. Neither case stops the rest of the analysis.

## Template bindings

`html_bindings.scan_bindings` masks `<!-- -->` comments before it looks for bindings. It records:

- `(event)="call()"` as an event binding whose name is the callee
- `[name]="ident"` and `[(name)]="ident"` as property bindings
- `@if (ident)` and `@for (item of ident; ...)`
- `{{ ident }}` and `{{ ident | async }}`
- a hyphenated start tag as a selector candidate

A binding is confirmed only when a literal `templateUrl` or a literal inline `template` links the HTML to one component and the name equals a method or property of that component. A simple name with no such member is `unresolved` / `target_not_found`. Any other expression is `candidate` / `heuristic`. A hyphenated tag that matches one other component's literal selector is `component_selector` with confidence `candidate`. Several matches are `ambiguous_resolution`. The component's own selector is not an edge back to itself.

## NgRx paths

An NgRx edge is confirmed only when `dispatch`, `select`, `selectSignal`, `ofType`, `on`, or `inject` names an imported binding that resolves to one file.

| Call | Edge kind |
| --- | --- |
| `dispatch`, `ofType`, NgRx `on` | `ngrx_action_reference` |
| `select`, `selectSignal` | `ngrx_selector_reference` |
| `inject` of a `Service` name or a `*.service.ts` file | `service_reference` |

`createAction`, `createReducer`, `createEffect`, and `createSelector` are indexed as calls. They do not by themselves create an edge to another file. `createActionGroup` and `createFeature` are not given a separate edge. An effect reaches a service through `inject`, not through a guessed method-call chain, and no HTTP client is inferred.

Base and head graphs are built separately. An edge never points from one commit's file to the other commit's file.

## Changed symbols

On each revision, a symbol is changed when its start/end lines overlap a changed line from the diff. The same path can contribute symbols on base and on head. A deletion uses the base blob.

## Limits

- Comments, strings, and template literals are masked in TypeScript. HTML comments are masked. Quoted attribute values are the expressions, not skipped.
- Control flow, generics, and template type checking are out of scope.
- `tsconfig.json` `extends` is not followed. Only the blob at the commit root is read.
- `styleUrls` are not edges.
- A class field is recorded as a property symbol so a template can name it. Nested functions are still not symbols.
- Extractor version is `"2"` so TypeScript cache records that omit bindings are not reused.
