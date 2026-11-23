# Context selection

This document describes how Angular excerpts are chosen. It does not replace `docs/07-benchmark.md`. The published 100k-line cache measurement was not repeated for this change.

## Order

Selection keeps the existing reason strings, in this order:

1. `changed file`
2. `confirmed direct dependency`
3. `confirmed reverse dependency`
4. `candidate dependency`

Within one reason, a higher score comes first. Equal scores use depth, path, then revision. The sort is stable for a given graph.

| Target | Score |
| --- | --- |
| Changed TypeScript or HTML file | 100 |
| Direct `template_binding` | 90 |
| Direct `ngrx_action_reference` | 85 |
| Direct `ngrx_selector_reference` or `service_reference` | 80 |
| Direct `component_template` | 75 |
| Direct `ts_import`, or a `*.spec.ts` file | 70 |
| Reverse dependency at depth 1 | 60 |
| Indirect dependency past depth 1 | 50 |
| `component_selector` candidate | 40 |
| `ngmodule_declaration` | 20 |

A shared module stays at 20, so it does not sort ahead of a changed file or a direct NgRx edge. `max_files` is applied after this sort.

## Impact walk

Forward walk follows confirmed outgoing edges from changed files, up to `--max-depth` (default 2). Reverse walk then starts from the changed files and from those forward targets, so a component's action can reach the reducer and effect that reference the action, and the next depth can reach a component that references the reducer. The walk does not cross revisions and does not re-enter a path.

Both revisions of a changed file are kept. An unchanged dependency is kept once, preferring head when both graphs contain it.

## Line budget

`--max-total-lines` defaults to 2000. Zero keeps no excerpt lines. Other limits are unchanged: `--max-depth`, `--max-files` (40), `--max-bytes` (1048576), `--max-lines-per-excerpt` (200).

Changed excerpts are accepted first. If a changed excerpt does not fit, a prefix that fills the remaining lines is kept and the rest is recorded on `omitted` with reason `max_total_lines`, `resolution_reason` `budget_exceeded`, and `missing_lines`. A warning `CHANGED_CONTEXT_OMITTED` names the path and the missing line count. A non-changed excerpt that does not fit is omitted instead of pushing a changed excerpt out.

`coverage.truncated` is true when any omission or clip happened. `angular_context.status` stays `partial`.

## Report

JSON keeps every previous `angular_context` key. New keys are additive. Markdown adds, under the existing Angular section:

1. Git change summary
2. Changed symbols
3. Angular impact
4. NgRx paths
5. Priority review targets
6. Related code (the selected excerpts)
7. Unresolved dependencies
8. Analysis limits

## Tests

`tests/test_angular_impact.py` covers comment masking, `@if` / `@for`, a missing method, an inline template, component-to-action, action-to-reducer, action-to-effect, effect-to-service, selector-to-component, a duplicate action import, a cycle, an unresolved import, one revision of an unchanged dependency, and a line budget that reports the omitted changed lines. Existing Angular context tests still require the four reason strings in order and `src/app/gone.ts` under `--max-files 1`.
