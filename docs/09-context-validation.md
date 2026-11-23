# Context validation

`validate-context` compares an existing `review-context.json` with a golden set. It does not modify the context file, open the reviewed repository, or change excerpt ranking.

```text
python -m offline_review validate-context \
  --context review-context.json \
  --golden tests/fixtures/angular-context-golden.json \
  --output context-validation.json
```

The Markdown report is written next to the JSON file. Exit 0 means the gate passed. Exit 1 means the report was written and the gate failed. Exit 2 means the JSON or the arguments cannot be used.

## Identity

The golden `base_commit`, `head_commit`, and `comparison` must equal the context's resolved base, head, and comparison mode. `repository` matches the context path when it is the same path or its final path segment. A mismatch fails the gate.

## Item result

A file that merely appears in `selected` is not enough. Every line in every required range must sit inside a selected excerpt for that path and revision.

| Status | Meaning |
| --- | --- |
| `complete` | Every required line on every required revision is selected. |
| `partial` | Some, but not all, required lines of a range are selected. |
| `omitted_by_budget` | A missing range is listed in `omitted` with `max_total_lines`, `max_files`, `max_bytes`, or `max_lines`. |
| `revision_mismatch` | One revision is complete and another required revision is absent. |
| `unresolved` | A missing range contains an `unresolved` record for that file and line. |
| `missing` | The range is neither selected nor explained by an omission. |
| `unknown` | The golden item has no `why_required` or no `evidence`. It is not a success. |

`unresolved_candidates` record imports that were investigated and not adopted, such as `RiskDetailComponent`. A confirmed edge for that symbol fails the gate. An unresolved edge stays `still_unresolved`.

## Metrics

Weights are `critical` 3, `high` 2, and `normal` 1. A recall denominator of zero is `not_applicable`, not 100%. `unknown` items stay in the denominator, so they cannot raise recall. Duplicate lines are counted once.

`changed_line_coverage` is reported separately for base and head. Missing changed lines warn by default. `--require-changed-lines` turns that warning into a gate failure. High recall and context efficiency are reported and are not gate thresholds.

The gate passes only when identity matches, `critical_recall` is complete, `unresolved_critical_count` is 0, and no unresolved candidate was confirmed.

## Sample golden set

`tests/fixtures/angular-context-golden.json` records the `getProductTree` contract change between `7e85d3ecf4e2e584a04a2a3fc930ab75a3f9a205` and `c034d66c1a28543160ae6e0f432f0c47c33ad8a1` in `tree-sample`. It requires both revisions of the service signature, the effect call, the success action, the reducer state and handler, the selector, the view read, and the mock `GET /api/product-tree` response. `app.module.ts` is excluded because it does not reference that contract.

`--max-total-lines` on `analyze` already accepts 2000, 3000, and 5000. This command does not change that budget.
