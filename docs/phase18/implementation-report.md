# Phase 18 implementation

Observed on 2026-11-23. No commit was created. Ground truth and fixture history were not rewritten.

## Cause

ANG-001 deletes one `catchError` line and adds no head line. The selector kept the base declaration of `loadBeaconsEffect` and emitted a head excerpt of lines `0-0`, even though the declaration still exists on head.

The context evaluator treated an identifier inside excerpt text as sufficient. A heading, a bare name, a declaration without its body, the same name in another file, or a base-only excerpt could pass.

## Extractor

A pure deletion is matched by path, qualname, and kind, not by the old line number. When one head declaration remains, the head excerpt uses that declaration's span. When the declaration is gone, the report records `TS_SYMBOL_ABSENT` and does not invent a head body. `TS_SYMBOL_MATCH_AMBIGUOUS` and `TS_SYMBOL_MATCH_UNCERTAIN` mean the head declaration was not chosen. These codes are not blocking warnings.

ANG-001 after the change, from `output/phase18/ANG-001/review-context.json`:

| Revision | Commit | `loadBeaconsEffect` |
|---|---|---|
| base | `1da882dfca3c24824625282bfead94b4dbdb8c1e` | lines 19–31, including `loadBeaconsFailure` |
| head | `345362e208ffc852e070d5deee361e7523bd3397` | lines 19–30, including `export const loadBeaconsEffect` and `ofType(loadBeacons)`, without `loadBeaconsFailure` |

Before the change the head excerpt was lines `0-0`.

## Evaluator

`evaluate-context` still returns schema `1.0`. Existing fields are unchanged. A required item now also carries `gap` and `head_status`. A miss is also listed in `missing_details`.

An item is covered only when an excerpt on the same path shows the declaration and a body, or a use inside such a body. A heading or a bare identifier does not count. If `changed_symbols` lists the symbol on head, the head excerpt must cover that span. `TS_SYMBOL_ABSENT` allows the base body to stand for a removed symbol. `revision_match` false keeps `case_context_sufficient` false even when required coverage is 1.0. `combined_units_sufficient` remains separate from the single-unit result.

`Validators` is covered only when it appears in a `.group(...)` whose assigned member is also read by the primary method on that file. For ANG-006 that member is `this.form` inside `save`. The HTML symbol `required` is covered only when a `formControlName` matches a key of that group. This check stays inside one review unit. It does not follow a form across files, and it does not prove that a `disabled` expression is the same condition `save` checks.

## Files

- `src/offline_review/context_selector.py`
- `src/offline_review/evaluation/context_evaluator.py`
- `tests/test_review_quality.py`
- `tests/evaluation/test_context_evaluator.py`
- `tests/evaluation/test_fixture.py`
- `docs/10-review-context-quality.md`
- `docs/phase16/04-operation-guide.md`

The analyze JSON schema and CLI arguments are unchanged. The package version was not bumped.
