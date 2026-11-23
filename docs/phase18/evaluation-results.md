# Phase 18 evaluation results

Observed on 2026-11-23 with `PYTHONPATH=src`. Commands used the case commits in `fixtures/phase16/cases/`. They did not guess revisions. AI findings were not produced and are not part of this result.

## Setup

Each head commit's parent is `1da882dfca3c24824625282bfead94b4dbdb8c1e`. Each diff path matches that case's primary location. ANG-002 changes only `src/app/beacon/beacon.reducer.ts`. `INVALID_TEST_SETUP` is 0.

## CLI

For each `ANG-00N`:

```text
python -m offline_review analyze \
  --repo fixtures/phase16/angular-ngrx \
  --base <case base_revision> \
  --head <case buggy_revision> \
  --comparison direct \
  --output output/phase18/ANG-00N

python -m offline_review evaluate-context \
  --context output/phase18/ANG-00N/review-context.json \
  --ground-truth fixtures/phase16/cases/ANG-00N.json \
  --output output/phase18/ANG-00N/context-evaluation.json
```

Both commands exited 0 for every case. Reports are under `output/phase18/`.

| Case | revision_match | changed_line_coverage | required_context_coverage | case_context_sufficient | combined_units_sufficient | Lines | Judgment |
|---|---|---:|---:|---|---|---:|---|
| ANG-001 | true | 1.0 | 1.0 | true | true | 203 | PASS |
| ANG-002 | true | 1.0 | 1.0 | true | true | 253 | PASS |
| ANG-003 | true | 1.0 | 1.0 | true | true | 244 | PASS |
| ANG-004 | true | 1.0 | 1.0 | true | true | 194 | PASS |
| ANG-005 | true | 1.0 | 1.0 | true | true | 208 | PASS |
| ANG-006 | true | 1.0 | 1.0 | true | true | 283 | PASS |
| ANG-007 | true | 1.0 | 1.0 | true | true | 283 | PASS |
| ANG-008 | true | 1.0 | 1.0 | true | true | 208 | PASS |
| ANG-009 | true | 1.0 | 1.0 | true | true | 279 | PASS |
| ANG-010 | true | 1.0 | 1.0 | true | true | 283 | PASS |

`missing_required` is empty for all ten. `blocking_warnings` is empty. The line budget is 2000 and every first unit is under it. Changed lines omitted are 0. ANG-002, ANG-007, ANG-009, and ANG-010 stay sufficient, as do the six cases that were insufficient in the Phase 16 initial table.

ANG-001 head `loadBeaconsEffect` is lines 19–30 and contains `export const loadBeaconsEffect` and `ofType(loadBeacons)`.

## Evaluator tests

```text
python -m unittest tests.evaluation.test_context_evaluator -v
```

`Ran 13 tests in 0.008s` and `OK`. The tests cover a real declaration and body, a heading, a declaration without a body, the same name in another file, a base excerpt when head still has the symbol, a revision mismatch, a removed symbol, symbols split across units, a linked form, an unrelated `Validators` target, and an unrelated `formControlName`.

## Related regression

```text
python -m unittest tests.test_review_quality tests.test_angular_context -v
```

`Ran 23 tests in 46.373s` and `OK`.

```text
python -m unittest tests.evaluation.test_fixture.FixtureTests.test_generated_context_has_no_answer_labels tests.evaluation.test_fixture.FixtureTests.test_every_defect_keeps_its_required_symbols -v
```

`Ran 2 tests in 39.908s` and `OK`.

```text
python -m unittest tests.test_python_analysis tests.evaluation tests.test_review_quality tests.test_angular_context tests.test_angular_impact -q
```

`Ran 44 tests in 55.360s` and `OK`.

`python -m unittest discover -s tests` was not run again in this phase. The last full run recorded before Phase 18 was `Ran 133 tests in 197.624s` and `OK`, and it does not include these Phase 18 tests.

## Limits

The form check is lexical and stays in one review unit. A `disabled` binding is not compared with the condition inside `save`. A deletion that sits outside every symbol can still produce a `0-0` head excerpt, because there is no declaration to carry. AI bug detection was not measured.
