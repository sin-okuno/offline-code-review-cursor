# Phase 16 operation

Run these commands from the repository root with `PYTHONPATH` set to `src`. They do not call a model or use the network.

## Recreate the fixture

```text
python -m offline_review.evaluation.fixture_builder \
  --repo fixtures/phase16/angular-ngrx \
  --cases fixtures/phase16/cases
```

Remove both directories first when replacing an older fixture. The builder sets Git identity only inside the new repository.

## Extract one case

```text
python -m offline_review analyze \
  --repo fixtures/phase16/angular-ngrx \
  --base 1da882dfca3c24824625282bfead94b4dbdb8c1e \
  --head 345362e208ffc852e070d5deee361e7523bd3397 \
  --comparison direct \
  --output output/phase16/ANG-001
```

`output/` is gitignored. The review context does not contain the ground-truth file. Do not pass `fixtures/phase16/cases/` to the local model.

## Evaluate extraction

```text
python -m offline_review evaluate-context \
  --context output/phase16/ANG-001/review-context.json \
  --ground-truth fixtures/phase16/cases/ANG-001.json \
  --output output/phase16/ANG-001/context-evaluation.json
```

`changed_lines_omitted` and `context_lines_omitted` are separate. `case_context_sufficient` is true only when one review unit, at most three required files, and the line budget contain every required symbol. A symbol counts only when the excerpt for that path shows its declaration and body, or a use inside such a body. A heading or a bare identifier does not count. When `changed_symbols` lists the symbol on head, the head excerpt must cover that span. `TS_SYMBOL_ABSENT` is the explicit exception for a symbol that was removed. `revision_match` false keeps `case_context_sufficient` false. `combined_units_sufficient` is reported and is not treated as a single model input.

`Validators` is covered only when it is used in a `.group(...)` assigned to a member that the primary method on the same file also reads, such as `this.form` inside `save`. The HTML symbol `required` is covered only when a `formControlName` matches a key of that group. This is a lexical check in one review unit. It does not follow a form across files, and it does not prove that a `disabled` expression is the same condition the save method checks. `missing_details` records the gap without removing `missing_required`.

## Evaluate saved findings

Place the model output in the findings schema. This command only reads that file.

```text
python -m offline_review evaluate-findings \
  --findings output/phase16/ANG-001/ai-findings.json \
  --ground-truth fixtures/phase16/cases/ANG-001.json \
  --context-evaluation output/phase16/ANG-001/context-evaluation.json \
  --output output/phase16/ANG-001/quality-report.json
```

Without `--context-evaluation`, a miss stays `not_evaluated`. A missing findings file is not a false negative. Automatic acceptance needs the primary path, a line inside the primary range, and two match phrases. Anything weaker and still related goes to `needs_manual_review`.

Exit 0 means the evaluation file was written. Exit 2 means the JSON or the output path was rejected. A low score does not change the exit code.
