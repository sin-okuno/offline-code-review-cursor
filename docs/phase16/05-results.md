# Phase 16 results

Observed on 2026-11-21. Figures below come from commands run in this workspace. They are not an operational quality proof.

## Tests

```text
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

`Ran 130 tests in 123.778s` and `OK`. The three stderr lines about a missing JSON file, an invalid JSON file, and an output path that would replace the context file come from the existing validation CLI test. That test passed.

`ng build` and `tsc` were not run. They need an Angular package install, which this offline tool does not start.

## Extraction

`analyze` and `evaluate-context` were run for ANG-001:

```text
python -m offline_review analyze \
  --repo fixtures/phase16/angular-ngrx \
  --base 1da882dfca3c24824625282bfead94b4dbdb8c1e \
  --head 345362e208ffc852e070d5deee361e7523bd3397 \
  --comparison direct \
  --output output/phase16/ANG-001

python -m offline_review evaluate-context \
  --context output/phase16/ANG-001/review-context.json \
  --ground-truth fixtures/phase16/cases/ANG-001.json \
  --output output/phase16/ANG-001/context-evaluation.json
```

Both exited 0. ANG-001 kept 1 of 1 changed lines. Context lines omitted were 0. Required-context coverage was 0.6667 because the excerpt does not contain the identifier `loadBeaconsEffect`. The service and reducer symbols were present. `case_context_sufficient` was false. `cross_unit_dependency` was false.

The same measurement for all 12 cases, through `build_context` and `evaluate_context`, gave:

| Case | Changed lines | Required coverage | Sufficient |
|---|---|---|---|
| ANG-001 | 1/1 | 0.6667 | no |
| ANG-002 | 9/9 | 1 | yes |
| ANG-003 | 3/3 | 0.6667 | no |
| ANG-004 | 2/2 | 0.5 | no |
| ANG-005 | 2/2 | 0.5 | no |
| ANG-006 | 2/2 | 0.5 | no |
| ANG-007 | 2/2 | 1 | yes |
| ANG-008 | 2/2 | 0.6667 | no |
| ANG-009 | 6/6 | 1 | yes |
| ANG-010 | 2/2 | 1 | yes |
| CLEAN-001 | 2/2 | N/A | yes |
| CLEAN-002 | 2/2 | N/A | yes |

Across every case, changed-line coverage was 1 and changed lines omitted were 0. Context lines omitted were 0 because the fixture fits in the 2000-line unit. No case split a required symbol across units. Defect context sufficiency was 4/10 (0.4). The recorded goal is 0.90. This sample does not meet it.

The misses are inside the changed file. `export const ... = createEffect(...)` is not a symbol span, so a one-line edit does not keep the effect name. ANG-003 misses `selectBeaconName` because that declaration is the unchanged line above the hunk. ANG-006 misses `Validators` because the constructor is outside the save-method hunk. The related files themselves were selected.

## AI findings

No local model output was supplied. Finding quality is **not evaluated**. Recall, precision, and F1 are not reported as measured scores. `evaluate-findings` is implemented and covered by unit tests with synthetic finding JSON. Those tests are not a review of this fixture by a model.

## After the excerpt fix

A later run, `Ran 133 tests in 197.624s` and `OK`, includes the extractor change. Multi-line `const` initializers are symbols, and a method change keeps its class when the class fits the per-excerpt line cap. The same 10 defect cases then report `missing_required` empty, `changed_lines_omitted` 0, and `case_context_sufficient` true. Defect context sufficiency is 10/10. AI findings remain not evaluated. A class larger than `--max-lines-per-excerpt` still stays on the smaller symbol. `TS_NGMODULE_UNKNOWN` remains for `StoreModule.forFeature(...)`.

## Open limits

- The lexical scanner is not a type checker. `TS_NGMODULE_UNKNOWN` remains on `StoreModule.forFeature(...)`.
- A class larger than the per-excerpt line cap does not contribute constructor lines to a method change.
- Twelve synthetic cases cannot support the 80% recall or 90% sufficiency goals. AI findings are still not evaluated.
