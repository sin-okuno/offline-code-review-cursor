# Phase 16 design

The evaluation package is `src/offline_review/evaluation/`. `analyze` and `validate-context` keep their arguments and output schema.

## Commands

```text
python -m offline_review evaluate-context \
  --context review-context.json \
  --ground-truth fixtures/phase16/cases/ANG-001.json \
  --output context-evaluation.json

python -m offline_review evaluate-findings \
  --findings ai-findings.json \
  --ground-truth fixtures/phase16/cases/ANG-001.json \
  --output quality-report.json \
  --context-evaluation context-evaluation.json
```

`--context-evaluation` is optional. Without it, a miss is `not_evaluated` rather than an AI miss. Both commands write a JSON file and a Markdown sibling. Exit 0 means the evaluation file was written. Exit 2 means the input schema or the output path was rejected. A low score does not change the exit code. Twelve synthetic cases are not evidence of production quality.

The output path must already have a parent directory, must end in `.json`, and must not replace the context, findings, or ground-truth file.

## Ground truth

Schema `1.0`. A defect case has one expected finding, primary locations, required context, and at least two match phrases. A clean case has `case_kind` `clean` and no match phrases. Case ids, phrases, and expected text live only in `fixtures/phase16/cases/`. The fixture source and the review context must not contain those ids.

`fixtures/phase16/angular-ngrx/` is generated. Its `.git` directory is ignored. `python -m offline_review.evaluation.fixture_builder` recreates the same commit ids when Git honors the fixed author dates and the repository-local identity.

## Context metrics

- `changed_line_coverage` uses `changed_line_files`. A zero total is `N/A`.
- `context_lines_omitted` copies `angular_context.coverage.lines_omitted`. It is not the changed-line gap.
- A required symbol is covered when an excerpt for that path contains the symbol as an identifier.
- `case_context_sufficient` is true only when one review unit covers every required symbol, the required paths fit in `--max-input-files` (default 3), and that unit is within the context line budget.
- Coverage across units is reported as `combined_units_sufficient` and is not treated as an AI input.
- `cross_unit_dependency` means every required symbol exists, but no single allowed input contains all of them.

## Finding metrics

Automatic true positive requires the primary path, a line inside a primary range, and at least two match phrases. Path proximity alone, or phrases alone, becomes `needs_manual_review`. A second hit on the same case is a duplicate, not another true positive. An unrelated finding is a false positive. A clean case treats every finding as a false positive. A missing findings file is not a false negative.

Miss causes:

1. `extractor_missing_context`
2. `cross_unit_context_gap`
3. `ai_missed_with_sufficient_context`
4. `ambiguous_ground_truth`
5. `not_evaluated`

Recall, precision, and F1 use `N/A` when the denominator is zero. Default goals are recall 0.80, precision 0.80, changed-line coverage 1, and context sufficiency 0.90. Flags can change the recorded goals. The report sets `operational_proof` to false.

## Fixture

The sample is a fictional beacon ledger. It uses an NgModule, actions, a reducer, selectors, effects, a service, a component, and a template. No Angular packages are downloaded. The lexical scanner checks that each revision still parses. That is not an Angular build or a type check.
