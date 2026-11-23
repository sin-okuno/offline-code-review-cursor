# Phase 17 root-cause analysis

Checked on 2026-11-23 with the current extractor. No source file was edited. The initial 4/10 result in `docs/phase16/05-results.md` is the historical baseline. It is not the current score.

Current `evaluate_context` for the six historical failures, using each case's own base and head:

| Case | Revision match | Required coverage | Sufficient | Changed lines | Blocking warnings | First unit |
|---|---|---:|---|---|---|---|
| ANG-001 | yes | 1.0 | yes | 1/1 | none | 191 lines |
| ANG-003 | yes | 1.0 | yes | 3/3 | none | 244 lines |
| ANG-004 | yes | 1.0 | yes | 2/2 | none | 194 lines |
| ANG-005 | yes | 1.0 | yes | 2/2 | none | 208 lines |
| ANG-006 | yes | 1.0 | yes | 2/2 | none | 283 lines |
| ANG-008 | yes | 1.0 | yes | 2/2 | none | 208 lines |

ANG-002, ANG-007, ANG-009, and ANG-010 were not part of this failure set. ANG-002's current diff is the reducer `push`, and its current evaluation is also sufficient (9/9 changed lines, coverage 1.0).

## Historical cause

The first failure was not a wrong commit and not an evaluator false negative. The required symbols were outside the excerpt that the selector kept.

| Case | Classification | Evidence | Current state |
|---|---|---|---|
| ANG-001 | `SYMBOL_DETECTION_FAILURE` | The only change is the deleted `catchError` line in `loadBeaconsEffect` (`beacon.effects.ts` around line 26). The first selector kept that line and not the `export const loadBeaconsEffect` declaration, so `missing_required` contained `loadBeaconsEffect`. | The base excerpt is now lines 19–31 and includes the declaration. Coverage is 1.0. |
| ANG-003 | `SYMBOL_DETECTION_FAILURE` | The hunk changes the callback body at lines 13–14. The declaration `export const selectBeaconName` is the unchanged line above it, so the name was absent from the excerpt. | Head excerpt is lines 12–14 and base is 12–15. The binding symbol covers the declaration. |
| ANG-004 | `SYMBOL_DETECTION_FAILURE` | One changed line, `ofType(saveBeacon)`, sits inside `navigateAfterSaveEffect` (line 65). The declaration is several lines above, at line 62. | Both revisions excerpt lines 62–69. |
| ANG-005 | `SYMBOL_DETECTION_FAILURE` | The status assignment at line 54 is inside `saveBeaconEffect`, declared at line 46. The one-line excerpt did not contain `saveBeaconEffect`. | Both revisions excerpt lines 46–60. |
| ANG-006 | `EXTRACTION_MISSING` | The hunk is the `save` guard at line 48. `Validators` is in the constructor, outside that method, so the method-only excerpt did not contain it. | Both revisions excerpt the class, lines 9–58, which includes the constructor and `save`. |
| ANG-008 | `SYMBOL_DETECTION_FAILURE` | The hunk replaces `saveBeaconSuccess(...)` with `navigateAfterSave()` inside `saveBeaconEffect` (line 52). The effect declaration at line 46 was outside the excerpt. | Both revisions excerpt lines 46–60, so the missing success dispatch is visible inside the effect. The reducer symbol `saveBeaconSuccess` is still selected as a dependency. |

No case in this set is `REVISION_MISMATCH`, `TEST_DATA_ERROR`, `EVALUATOR_FALSE_NEGATIVE`, or `EVALUATOR_FALSE_POSITIVE` on the current run. `revision_match` is true for all six.

## Remaining head gap on ANG-001

ANG-001 is a deletion with no added line. The current base excerpt is lines 19–31. The head excerpt for `beacon.effects.ts` is still `0–0`, even though `loadBeaconsEffect` still exists on the head commit. `changed_symbols` lists the binding only for base, because the head side has no changed line number to intersect.

The evaluator passes because the required identifier appears in the base excerpt. That is enough for the current symbol check. It does not mean the head declaration was selected. Step 2 should keep the surviving head symbol instead of emitting `0–0` when the declaration still exists.

## Evaluator limit that is not a current failure

`required_context_coverage` still passes when the identifier occurs in an excerpt. For ANG-006 the class excerpt does contain both `Validators.required` and `save`, so the form relationship is present. The metric itself does not yet prove that those two uses belong to one form. That is a later evaluator check, not the cause of the original 50% result.
